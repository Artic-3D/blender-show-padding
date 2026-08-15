"""Cached GPU overlay and Blender lifecycle integration."""

from __future__ import annotations

import time

import bmesh
import bpy
import gpu
from bpy.app.handlers import persistent
from gpu.types import GPUShaderCreateInfo, GPUStageInterfaceInfo
from gpu_extras.batch import batch_for_shader

from .geometry import (
    build_overlay_geometry_with_template,
    compute_band_width,
    rebuild_overlay_geometry,
    state_signature,
)


_DRAW_HANDLE = None
_SHADER = None
_COMPOSITE_SHADER = None
_COMPOSITE_BATCH = None
_CACHE = {}
_OFFSCREENS = {}
_REVISION = 1
_LAST_INVALIDATION = 0.0
_LAST_TRANSFORM_ACTIVITY = 0.0
_FALLBACK_INTERVAL = 30.0
_IDLE_VERIFY_DELAY = 0.5
_BMESH_QUIET_DELAY = 0.15
_MAX_CACHE_ENTRIES = 8
_UPDATE_INTERVAL = 1.0 / 30.0
_IDLE_INTERVAL = 0.2
_STYLE_REDRAW_DELAY = 0.01
_REGISTERED = False
_RUNTIME_NAMESPACE_KEY = "_uv_padding_overlay_runtime"
_RUNTIME_TOKEN = object()
_RUNTIME_RECORD = None


class _CacheEntry:
    __slots__ = (
        "batch",
        "revision",
        "signature",
        "last_signature_check",
        "last_geometry_update",
        "last_used",
        "stats",
        "templates",
        "band_width",
        "corner_segments",
        "source_key",
        "selected_only",
        "uv_select_sync",
        "mesh_select_mode",
        "pending_positions",
        "pending_triangles",
        "needs_verify",
        "gpu_revision",
    )

    def __init__(self):
        self.batch = None
        self.revision = -1
        self.signature = None
        self.last_signature_check = 0.0
        self.last_geometry_update = 0.0
        self.last_used = 0.0
        self.stats = {}
        self.templates = ()
        self.band_width = None
        self.corner_segments = 2
        self.source_key = ()
        self.selected_only = False
        self.uv_select_sync = False
        self.mesh_select_mode = (True, False, False)
        self.pending_positions = None
        self.pending_triangles = None
        self.needs_verify = False
        self.gpu_revision = 0


class _OffscreenEntry:
    __slots__ = (
        "offscreen",
        "width",
        "height",
        "content_key",
        "last_used",
        "mask_render_count",
    )

    def __init__(self, offscreen, width, height):
        self.offscreen = offscreen
        self.width = int(width)
        self.height = int(height)
        self.content_key = None
        self.last_used = time.monotonic()
        self.mask_render_count = 0


def tag_uv_editors_for_redraw():
    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return
    for window in window_manager.windows:
        for area in window.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.tag_redraw()


def _deferred_style_redraw():
    """Give newly initialized GPU resources one follow-up editor redraw."""

    if _REGISTERED:
        tag_uv_editors_for_redraw()
    return None


def request_style_redraw():
    """Redraw now and once on the next event-loop tick, coalescing requests."""

    tag_uv_editors_for_redraw()
    if (
        _REGISTERED
        and not bpy.app.timers.is_registered(_deferred_style_redraw)
    ):
        bpy.app.timers.register(
            _deferred_style_redraw,
            first_interval=_STYLE_REDRAW_DELAY,
        )


def invalidate_geometry(clear=False):
    global _REVISION, _LAST_INVALIDATION
    _REVISION += 1
    _LAST_INVALIDATION = time.monotonic()
    if clear:
        _CACHE.clear()
    _ensure_update_timer()
    tag_uv_editors_for_redraw()


def _source_objects(context):
    objects = getattr(context, "objects_in_mode_unique_data", None)
    if objects is None:
        view_layer = getattr(context, "view_layer", None)
        if view_layer is None:
            return []
        objects = [obj for obj in view_layer.objects if obj.mode == "EDIT"]
    result = []
    seen_meshes = set()
    for obj in objects:
        if obj.type != "MESH" or obj.mode != "EDIT":
            continue
        mesh = obj.data
        mesh_pointer = mesh.as_pointer()
        if mesh_pointer in seen_meshes:
            continue
        seen_meshes.add(mesh_pointer)
        result.append(obj)
    result.sort(key=lambda obj: obj.data.as_pointer())
    return result


def _source_objects_for_scene(scene):
    result = []
    seen_meshes = set()
    for obj in scene.objects:
        if obj.type != "MESH" or obj.mode != "EDIT":
            continue
        mesh = obj.data
        mesh_pointer = mesh.as_pointer()
        if mesh_pointer in seen_meshes:
            continue
        seen_meshes.add(mesh_pointer)
        result.append(obj)
    result.sort(key=lambda obj: obj.data.as_pointer())
    return result


def _collect_sources_for_scene(scene):
    sources = []
    for obj in _source_objects_for_scene(scene):
        mesh = obj.data
        active_uv = mesh.uv_layers.active
        if active_uv is None:
            continue
        try:
            bm = bmesh.from_edit_mesh(mesh)
        except (RuntimeError, ValueError):
            continue
        uv_layer = bm.loops.layers.uv.get(active_uv.name)
        if uv_layer is None:
            continue
        sources.append((mesh, bm, uv_layer, active_uv.name))
    return sources


def context_status(context):
    from . import settings as settings_module

    scene_settings = context.scene.uv_padding_overlay
    global_settings = settings_module.get_preferences(context)
    if global_settings is None or not global_settings.enabled:
        return None, None
    objects = _source_objects(context)
    if not objects:
        return "Enter mesh Edit Mode", "INFO"
    if not any(obj.data.uv_layers.active is not None for obj in objects):
        return "No active UV map", "INFO"
    if scene_settings.margin_px <= 0.0:
        return "Margin is zero", "INFO"
    return None, None


def _source_key(sources):
    return tuple(
        (mesh.as_pointer(), layer_name)
        for mesh, _bm, _uv_layer, layer_name in sources
    )


def _combined_signature(
    sources,
    selected_only,
    uv_select_sync,
    mesh_select_mode,
):
    combined = 0xCBF29CE484222325
    for mesh, bm, uv_layer, layer_name in sources:
        value = state_signature(
            bm,
            uv_layer,
            selected_only,
            uv_select_sync,
            mesh_select_mode,
        )
        combined ^= mesh.as_pointer() & ((1 << 64) - 1)
        combined ^= hash(layer_name) & ((1 << 64) - 1)
        combined ^= value
        combined = (combined * 0x100000001B3) & ((1 << 64) - 1)
    return combined


def _rebuild_entry(
    entry,
    sources,
    band_width,
    selected_only,
    uv_select_sync,
    corner_segments,
    mesh_select_mode,
):
    positions = []
    triangles = []
    combined_signature = 0xCBF29CE484222325
    aggregate = {
        "visible_faces": 0,
        "islands": 0,
        "boundary_edges": 0,
        "triangles": 0,
        "degenerate_edges": 0,
    }
    templates = []
    for mesh, bm, uv_layer, layer_name in sources:
        (
            local_positions,
            local_triangles,
            stats,
            signature,
            template,
        ) = build_overlay_geometry_with_template(
            bm,
            uv_layer,
            band_width,
            selected_only,
            uv_select_sync,
            corner_segments,
            mesh_select_mode,
        )
        templates.append(template)
        offset = len(positions)
        positions.extend(local_positions)
        triangles.extend(
            (a + offset, b + offset, c + offset)
            for a, b, c in local_triangles
        )
        for name in aggregate:
            aggregate[name] += stats[name]
        combined_signature ^= mesh.as_pointer() & ((1 << 64) - 1)
        combined_signature ^= hash(layer_name) & ((1 << 64) - 1)
        combined_signature ^= signature
        combined_signature = (
            combined_signature * 0x100000001B3
        ) & ((1 << 64) - 1)

    _queue_batch(entry, positions, triangles)
    entry.stats = aggregate
    entry.templates = tuple(templates)
    entry.band_width = band_width
    entry.corner_segments = corner_segments
    entry.signature = combined_signature
    entry.revision = _REVISION
    now = time.monotonic()
    entry.last_signature_check = now
    entry.last_geometry_update = now
    entry.needs_verify = False


def _queue_batch(entry, positions, triangles):
    entry.pending_positions = positions
    entry.pending_triangles = triangles


def _upload_pending_batch(entry):
    positions = entry.pending_positions
    triangles = entry.pending_triangles
    if positions is None or triangles is None:
        return
    entry.pending_positions = None
    entry.pending_triangles = None
    shader = _ensure_shader()
    if positions and triangles:
        entry.batch = batch_for_shader(
            shader,
            "TRIS",
            {"position": positions},
            indices=triangles,
        )
    else:
        entry.batch = None
    entry.gpu_revision += 1


def _refresh_entry(entry, sources, band_width, corner_segments):
    if len(entry.templates) != len(sources):
        return False
    positions = []
    triangles = []
    aggregate = {
        "visible_faces": 0,
        "islands": 0,
        "boundary_edges": 0,
        "triangles": 0,
        "degenerate_edges": 0,
    }
    try:
        for template, (_mesh, bm, uv_layer, _layer_name) in zip(
            entry.templates,
            sources,
        ):
            current_counts = (len(bm.verts), len(bm.edges), len(bm.faces))
            if template is None or current_counts != template.mesh_counts:
                return False
            local_positions, local_triangles, stats = rebuild_overlay_geometry(
                bm,
                template,
                uv_layer,
                band_width,
                corner_segments,
            )
            offset = len(positions)
            positions.extend(local_positions)
            triangles.extend(
                (a + offset, b + offset, c + offset)
                for a, b, c in local_triangles
            )
            for name in aggregate:
                aggregate[name] += stats[name]
    except (ReferenceError, RuntimeError, ValueError):
        return False
    _queue_batch(entry, positions, triangles)
    entry.stats = aggregate
    entry.band_width = band_width
    entry.corner_segments = corner_segments
    entry.revision = _REVISION
    entry.last_geometry_update = time.monotonic()
    entry.needs_verify = True
    return True


def _prune_cache():
    if len(_CACHE) <= _MAX_CACHE_ENTRIES:
        return
    oldest = sorted(_CACHE.items(), key=lambda item: item[1].last_used)
    for key, _entry in oldest[: len(_CACHE) - _MAX_CACHE_ENTRIES]:
        del _CACHE[key]


def _ensure_entry(
    scene,
    sources,
    band_width,
    selected_only,
    uv_select_sync,
    corner_segments,
    mesh_select_mode,
):
    corner_segments = max(1, int(corner_segments))
    key = scene.as_pointer()
    entry = _CACHE.get(key)
    if entry is None:
        entry = _CacheEntry()
        _CACHE[key] = entry
    now = time.monotonic()
    entry.last_used = now
    source_key = _source_key(sources)
    configuration_changed = (
        entry.source_key != source_key
        or entry.selected_only != bool(selected_only)
        or entry.uv_select_sync != bool(uv_select_sync)
        or entry.mesh_select_mode != tuple(mesh_select_mode)
    )
    if entry.signature is None or configuration_changed:
        _rebuild_entry(
            entry,
            sources,
            band_width,
            selected_only,
            uv_select_sync,
            corner_segments,
            mesh_select_mode,
        )
        entry.source_key = source_key
        entry.selected_only = bool(selected_only)
        entry.uv_select_sync = bool(uv_select_sync)
        entry.mesh_select_mode = tuple(mesh_select_mode)
    elif (
        entry.revision != _REVISION
        or entry.band_width != band_width
        or entry.corner_segments != corner_segments
    ):
        if not _refresh_entry(
            entry,
            sources,
            band_width,
            corner_segments,
        ):
            _rebuild_entry(
                entry,
                sources,
                band_width,
                selected_only,
                uv_select_sync,
                corner_segments,
                mesh_select_mode,
            )
    should_verify_refresh = (
        entry.needs_verify
        and now - _LAST_INVALIDATION >= _IDLE_VERIFY_DELAY
    )
    should_run_fallback = (
        now - entry.last_signature_check >= _FALLBACK_INTERVAL
    )
    if should_verify_refresh or should_run_fallback:
        signature = _combined_signature(
            sources,
            selected_only,
            uv_select_sync,
            mesh_select_mode,
        )
        entry.last_signature_check = now
        if signature != entry.signature:
            _rebuild_entry(
                entry,
                sources,
                band_width,
                selected_only,
                uv_select_sync,
                corner_segments,
                mesh_select_mode,
            )
        else:
            entry.needs_verify = False
    _prune_cache()
    return entry


def _ensure_shader():
    global _SHADER
    if _SHADER is not None:
        return _SHADER
    interface = GPUStageInterfaceInfo("uv_padding_overlay_interface")
    interface.smooth("VEC2", "uv_coordinate")
    info = GPUShaderCreateInfo()
    info.vertex_in(0, "VEC2", "position")
    info.push_constant("VEC4", "view_rect")
    info.push_constant("VEC4", "color")
    info.vertex_out(interface)
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(
        """
        void main()
        {
            vec2 span = max(view_rect.zw - view_rect.xy, vec2(1e-20));
            vec2 unit_position = (position - view_rect.xy) / span;
            uv_coordinate = position;
            gl_Position = vec4(unit_position * 2.0 - 1.0, 0.0, 1.0);
        }
        """
    )
    info.fragment_source(
        """
        void main()
        {
            fragColor = color + vec4(uv_coordinate * 0.0, 0.0, 0.0);
        }
        """
    )
    _SHADER = gpu.shader.create_from_info(info)
    return _SHADER


def _ensure_composite_resources():
    global _COMPOSITE_SHADER, _COMPOSITE_BATCH
    if _COMPOSITE_SHADER is None:
        interface = GPUStageInterfaceInfo(
            "uv_padding_overlay_composite_interface"
        )
        interface.smooth("VEC2", "texture_coordinate")
        info = GPUShaderCreateInfo()
        info.vertex_in(0, "VEC2", "position")
        info.vertex_in(1, "VEC2", "texture_uv")
        info.push_constant("VEC4", "color")
        info.push_constant("VEC4", "highlight_color")
        info.push_constant("FLOAT", "mask_scale")
        info.push_constant("FLOAT", "highlight_threshold")
        info.sampler(0, "FLOAT_2D", "mask_texture")
        info.vertex_out(interface)
        info.fragment_out(0, "VEC4", "fragColor")
        info.vertex_source(
            """
            void main()
            {
                texture_coordinate = texture_uv;
                gl_Position = vec4(position, 0.0, 1.0);
            }
            """
        )
        info.fragment_source(
            """
            void main()
            {
                float mask = texture(mask_texture, texture_coordinate).a;
                float is_overlap = step(highlight_threshold, mask);
                vec4 display_color = mix(color, highlight_color, is_overlap);
                float coverage = min(mask * mask_scale, 1.0);
                fragColor = vec4(
                    display_color.rgb,
                    display_color.a * coverage
                );
            }
            """
        )
        _COMPOSITE_SHADER = gpu.shader.create_from_info(info)
    if _COMPOSITE_BATCH is None:
        _COMPOSITE_BATCH = batch_for_shader(
            _COMPOSITE_SHADER,
            "TRIS",
            {
                "position": ((-1.0, -1.0), (3.0, -1.0), (-1.0, 3.0)),
                "texture_uv": ((0.0, 0.0), (2.0, 0.0), (0.0, 2.0)),
            },
        )
    return _COMPOSITE_SHADER, _COMPOSITE_BATCH


def _free_offscreen_entry(entry):
    try:
        entry.offscreen.free()
    except (ReferenceError, RuntimeError):
        pass


def _ensure_offscreen(window, area, width, height):
    window_pointer = window.as_pointer()
    active_area_pointers = {
        candidate.as_pointer()
        for candidate in window.screen.areas
        if candidate.type == "IMAGE_EDITOR"
    }
    for stale_key in tuple(_OFFSCREENS):
        if (
            stale_key[0] == window_pointer
            and stale_key[1] not in active_area_pointers
        ):
            _free_offscreen_entry(_OFFSCREENS.pop(stale_key))
    key = (window_pointer, area.as_pointer())
    entry = _OFFSCREENS.get(key)
    if (
        entry is not None
        and (entry.width != width or entry.height != height)
    ):
        _free_offscreen_entry(entry)
        entry = None
    if entry is None:
        entry = _OffscreenEntry(
            gpu.types.GPUOffScreen(width, height, format="RGBA8"),
            width,
            height,
        )
        _OFFSCREENS[key] = entry
    entry.last_used = time.monotonic()
    return entry


def _draw_layered(entry, view_rect, color):
    shader = _ensure_shader()
    previous_blend = gpu.state.blend_get()
    previous_depth = gpu.state.depth_test_get()
    try:
        shader.bind()
        shader.uniform_float("view_rect", view_rect)
        shader.uniform_float("color", color)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("ALPHA")
        entry.batch.draw(shader)
    finally:
        gpu.state.blend_set(previous_blend)
        gpu.state.depth_test_set(previous_depth)


def _draw_composited(
    context,
    entry,
    view_rect,
    color,
    highlight_color,
    highlighted,
):
    region = context.region
    width = int(region.width)
    height = int(region.height)
    if width <= 0 or height <= 0:
        return
    offscreen_entry = _ensure_offscreen(
        context.window,
        context.area,
        width,
        height,
    )
    content_key = (
        context.scene.as_pointer(),
        entry.gpu_revision,
        tuple(float(value) for value in view_rect),
        bool(highlighted),
    )
    previous_viewport = gpu.state.viewport_get()
    previous_blend = gpu.state.blend_get()
    previous_depth = gpu.state.depth_test_get()
    try:
        if offscreen_entry.content_key != content_key:
            with offscreen_entry.offscreen.bind():
                gpu.state.viewport_set(0, 0, width, height)
                gpu.state.active_framebuffer_get().clear(
                    color=(0.0, 0.0, 0.0, 0.0)
                )
                gpu.state.depth_test_set("NONE")
                gpu.state.blend_set("ALPHA" if highlighted else "NONE")
                mask_shader = _ensure_shader()
                mask_shader.bind()
                mask_shader.uniform_float("view_rect", view_rect)
                mask_shader.uniform_float(
                    "color",
                    (1.0, 1.0, 1.0, 0.5)
                    if highlighted
                    else (1.0, 1.0, 1.0, 1.0),
                )
                entry.batch.draw(mask_shader)
            offscreen_entry.content_key = content_key
            offscreen_entry.mask_render_count += 1

        gpu.state.viewport_set(*previous_viewport)
        composite_shader, composite_batch = _ensure_composite_resources()
        composite_shader.bind()
        composite_shader.uniform_float("color", color)
        composite_shader.uniform_float("highlight_color", highlight_color)
        composite_shader.uniform_float(
            "mask_scale",
            2.0 if highlighted else 1.0,
        )
        composite_shader.uniform_float(
            "highlight_threshold",
            0.625 if highlighted else 2.0,
        )
        composite_shader.uniform_sampler(
            "mask_texture",
            offscreen_entry.offscreen.texture_color,
        )
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("ALPHA")
        composite_batch.draw(composite_shader)
    finally:
        gpu.state.viewport_set(*previous_viewport)
        gpu.state.blend_set(previous_blend)
        gpu.state.depth_test_set(previous_depth)


def _draw_overlay():
    from . import settings as settings_module

    context = bpy.context
    area = context.area
    region = context.region
    space = context.space_data
    if (
        area is None
        or region is None
        or area.type != "IMAGE_EDITOR"
        or region.type != "WINDOW"
        or not (
            area.ui_type == "UV" or getattr(space, "ui_mode", "") == "UV"
        )
    ):
        return
    scene = context.scene
    scene_settings = getattr(scene, "uv_padding_overlay", None)
    global_settings = settings_module.get_preferences(context)
    if (
        scene_settings is None
        or global_settings is None
        or not global_settings.enabled
        or scene_settings.margin_px <= 0.0
    ):
        return
    entry = _CACHE.get(scene.as_pointer())
    if entry is None:
        return
    _upload_pending_batch(entry)
    if entry.batch is None:
        return
    entry.last_used = time.monotonic()
    view = region.view2d
    minimum = view.region_to_view(0, 0)
    maximum = view.region_to_view(region.width, region.height)
    view_rect = (minimum[0], minimum[1], maximum[0], maximum[1])
    color = (
        float(global_settings.color[0]),
        float(global_settings.color[1]),
        float(global_settings.color[2]),
        float(global_settings.color[3])
        if len(global_settings.color) > 3
        else 0.25,
    )
    highlight_color = (
        float(global_settings.highlight_color[0]),
        float(global_settings.highlight_color[1]),
        float(global_settings.highlight_color[2]),
        float(global_settings.highlight_color[3])
        if len(global_settings.highlight_color) > 3
        else 0.65,
    )
    if global_settings.render_mode in {"UNIFIED", "HIGHLIGHTED"}:
        _draw_composited(
            context,
            entry,
            view_rect,
            color,
            highlight_color,
            global_settings.render_mode == "HIGHLIGHTED",
        )
    else:
        _draw_layered(entry, view_rect, color)


def _update_scene_cache(scene):
    """Read a stable edit BMesh snapshot and queue GPU geometry."""

    from . import settings as settings_module

    scene_settings = getattr(scene, "uv_padding_overlay", None)
    global_settings = settings_module.get_preferences()
    if (
        scene_settings is None
        or global_settings is None
        or not global_settings.enabled
        or scene_settings.margin_px <= 0.0
    ):
        _CACHE.pop(scene.as_pointer(), None)
        return False
    band_width = compute_band_width(
        scene_settings.margin_px,
        scene_settings.texture_resolution,
    )
    if band_width <= 0.0:
        _CACHE.pop(scene.as_pointer(), None)
        return False
    sources = _collect_sources_for_scene(scene)
    before = _CACHE.get(scene.as_pointer())
    before_revision = before.revision if before is not None else -1
    before_pending = before.pending_positions if before is not None else None
    entry = _ensure_entry(
        scene,
        sources,
        band_width,
        global_settings.selected_only,
        bool(scene.tool_settings.use_uv_select_sync),
        global_settings.corner_segments,
        tuple(scene.tool_settings.mesh_select_mode),
    )
    return (
        before is None
        or entry.revision != before_revision
        or entry.pending_positions is not before_pending
    )


def _active_transform_operator():
    """Return a running transform operator, if Blender exposes one.

    Blender 5.x exposes each window's modal operator list.  Reading edit
    BMesh while a transform owns it is unsafe in provisional builds, even
    from a main-thread timer.  Blender 4.2 lacks this API in some builds, so
    dependency-graph quiet-time remains the compatibility fallback.
    """

    window_manager = getattr(bpy.context, "window_manager", None)
    if window_manager is None:
        return None
    try:
        windows = tuple(window_manager.windows)
    except (ReferenceError, RuntimeError):
        return None
    for window in windows:
        try:
            operators = getattr(window, "modal_operators", ()) or ()
            for operator in operators:
                identifier = str(getattr(operator, "bl_idname", ""))
                if identifier.startswith("TRANSFORM_OT_"):
                    return operator
        except (ReferenceError, RuntimeError):
            continue
    return None


def _bmesh_snapshot_is_safe(now=None):
    """Gate all edit-BMesh reads until modal transforms have gone quiet."""

    global _LAST_TRANSFORM_ACTIVITY
    if now is None:
        now = time.monotonic()
    if _active_transform_operator() is not None:
        _LAST_TRANSFORM_ACTIVITY = now
        return False
    last_activity = max(_LAST_INVALIDATION, _LAST_TRANSFORM_ACTIVITY)
    return now - last_activity >= _BMESH_QUIET_DELAY


def _update_timer():
    """Coalesce updates and read BMesh only after transforms release it."""

    if not _REGISTERED:
        return None
    now = time.monotonic()
    if not _bmesh_snapshot_is_safe(now):
        return _UPDATE_INTERVAL
    changed = False
    try:
        window_manager = getattr(bpy.context, "window_manager", None)
        scenes = {}
        if window_manager is not None:
            for window in window_manager.windows:
                scene = window.scene
                scenes[scene.as_pointer()] = scene
        for scene in scenes.values():
            try:
                changed = _update_scene_cache(scene) or changed
            except (ReferenceError, RuntimeError, ValueError):
                _CACHE.pop(scene.as_pointer(), None)
        if changed:
            tag_uv_editors_for_redraw()
    except Exception:
        # A timer must never destabilize Blender. Unexpected state is retried
        # after the current editor operation has completed.
        _CACHE.clear()
    if time.monotonic() - _LAST_INVALIDATION < _IDLE_VERIFY_DELAY:
        return _UPDATE_INTERVAL
    return _IDLE_INTERVAL


def _ensure_update_timer():
    if not _REGISTERED:
        return
    if not bpy.app.timers.is_registered(_update_timer):
        bpy.app.timers.register(
            _update_timer,
            first_interval=0.0,
            persistent=True,
        )


@persistent
def _depsgraph_update_post(_scene, depsgraph):
    relevant = False
    for update in depsgraph.updates:
        data = update.id
        if isinstance(data, bpy.types.Mesh):
            relevant = True
            break
        if isinstance(data, bpy.types.Object) and data.type == "MESH":
            relevant = True
            break
    if relevant:
        invalidate_geometry()


@persistent
def _reset_after_file_change(_unused):
    if _RUNTIME_RECORD is not None:
        bpy.app.driver_namespace[_RUNTIME_NAMESPACE_KEY] = _RUNTIME_RECORD
    invalidate_geometry(clear=True)
    _ensure_update_timer()


def _append_handler(collection, handler):
    if handler not in collection:
        collection.append(handler)


def _remove_handler(collection, handler):
    if handler in collection:
        collection.remove(handler)


def _cleanup_runtime_record(record):
    """Remove callbacks captured before a module reload.

    Extension upgrades can re-execute this module while an older draw callback
    is still registered.  Module globals are then insufficient because the old
    opaque draw-handle reference may already have been overwritten.  A plain
    record in ``driver_namespace`` preserves the exact callback identities and
    handle across reloads.
    """

    if not isinstance(record, dict):
        return
    timer = record.get("timer")
    if timer is not None:
        try:
            if bpy.app.timers.is_registered(timer):
                bpy.app.timers.unregister(timer)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    for collection_name, handler in record.get("handlers", ()):
        collection = getattr(bpy.app.handlers, collection_name, None)
        if collection is None:
            continue
        try:
            if handler in collection:
                collection.remove(handler)
        except (ReferenceError, RuntimeError, ValueError):
            pass
    draw_handle = record.get("draw_handle")
    if draw_handle is not None:
        try:
            bpy.types.SpaceImageEditor.draw_handler_remove(
                draw_handle,
                "WINDOW",
            )
        except (ReferenceError, RuntimeError, ValueError):
            pass
    record.clear()


def register():
    global _DRAW_HANDLE, _REGISTERED, _RUNTIME_RECORD
    previous = bpy.app.driver_namespace.get(_RUNTIME_NAMESPACE_KEY)
    if previous is not None:
        _cleanup_runtime_record(previous)
    _DRAW_HANDLE = None
    _REGISTERED = True
    _DRAW_HANDLE = bpy.types.SpaceImageEditor.draw_handler_add(
        _draw_overlay,
        (),
        "WINDOW",
        "POST_VIEW",
    )
    _append_handler(bpy.app.handlers.depsgraph_update_post, _depsgraph_update_post)
    _append_handler(bpy.app.handlers.undo_post, _reset_after_file_change)
    _append_handler(bpy.app.handlers.redo_post, _reset_after_file_change)
    _append_handler(bpy.app.handlers.load_post, _reset_after_file_change)
    _RUNTIME_RECORD = {
        "token": _RUNTIME_TOKEN,
        "draw_handle": _DRAW_HANDLE,
        "timer": _update_timer,
        "handlers": (
            ("depsgraph_update_post", _depsgraph_update_post),
            ("undo_post", _reset_after_file_change),
            ("redo_post", _reset_after_file_change),
            ("load_post", _reset_after_file_change),
        ),
    }
    bpy.app.driver_namespace[_RUNTIME_NAMESPACE_KEY] = _RUNTIME_RECORD
    _ensure_update_timer()
    invalidate_geometry(clear=True)


def unregister():
    global _DRAW_HANDLE, _SHADER, _COMPOSITE_SHADER
    global _COMPOSITE_BATCH, _REGISTERED, _RUNTIME_RECORD
    _REGISTERED = False
    if bpy.app.timers.is_registered(_deferred_style_redraw):
        bpy.app.timers.unregister(_deferred_style_redraw)
    record = bpy.app.driver_namespace.get(_RUNTIME_NAMESPACE_KEY)
    if isinstance(record, dict) and record.get("token") is _RUNTIME_TOKEN:
        _cleanup_runtime_record(record)
        bpy.app.driver_namespace.pop(_RUNTIME_NAMESPACE_KEY, None)
    else:
        if bpy.app.timers.is_registered(_update_timer):
            bpy.app.timers.unregister(_update_timer)
        _remove_handler(bpy.app.handlers.load_post, _reset_after_file_change)
        _remove_handler(bpy.app.handlers.redo_post, _reset_after_file_change)
        _remove_handler(bpy.app.handlers.undo_post, _reset_after_file_change)
        _remove_handler(
            bpy.app.handlers.depsgraph_update_post,
            _depsgraph_update_post,
        )
        if _DRAW_HANDLE is not None:
            bpy.types.SpaceImageEditor.draw_handler_remove(
                _DRAW_HANDLE,
                "WINDOW",
            )
    _DRAW_HANDLE = None
    _RUNTIME_RECORD = None
    _CACHE.clear()
    for offscreen_entry in _OFFSCREENS.values():
        _free_offscreen_entry(offscreen_entry)
    _OFFSCREENS.clear()
    _SHADER = None
    _COMPOSITE_SHADER = None
    _COMPOSITE_BATCH = None
    tag_uv_editors_for_redraw()
