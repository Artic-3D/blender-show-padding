"""Manually calculated distance-field overlay for empty UV space."""

from __future__ import annotations

import math
import time

import bpy
import gpu
from bpy.types import Operator
from gpu.types import GPUShaderCreateInfo, GPUStageInterfaceInfo
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.geometry import tessellate_polygon


_BASE_PIXELS_PER_TILE = 512
_MAIN_UDIM_BOUNDS = (0.0, 0.0, 1.0, 1.0)
_HIGHLIGHT_FRACTION = 0.01
_INFINITY = 1.0e20

_CACHE = {}
_SHADER = None


class EmptinessError(RuntimeError):
    """A concise, user-facing calculation failure."""


class _CacheEntry:
    __slots__ = (
        "texture",
        "batch",
        "bounds",
        "width",
        "height",
        "pixels_per_uv",
        "max_distance_uv",
        "padding_width_uv",
        "highlight_threshold",
        "visible_faces",
        "triangles",
        "calculation_ms",
    )

    def __init__(
        self,
        texture,
        batch,
        bounds,
        width,
        height,
        pixels_per_uv,
        max_distance_uv,
        padding_width_uv,
        highlight_threshold,
        visible_faces,
        triangles,
        calculation_ms,
    ):
        self.texture = texture
        self.batch = batch
        self.bounds = bounds
        self.width = int(width)
        self.height = int(height)
        self.pixels_per_uv = int(pixels_per_uv)
        self.max_distance_uv = float(max_distance_uv)
        self.padding_width_uv = float(padding_width_uv)
        self.highlight_threshold = float(highlight_threshold)
        self.visible_faces = int(visible_faces)
        self.triangles = int(triangles)
        self.calculation_ms = float(calculation_ms)


def _tag_redraw():
    from . import overlay

    overlay.tag_uv_editors_for_redraw()


def get_entry(scene):
    if scene is None:
        return None
    return _CACHE.get(scene.as_pointer())


def clear_scene(scene, *, redraw=True):
    if scene is None:
        return False
    removed = _CACHE.pop(scene.as_pointer(), None) is not None
    if redraw:
        _tag_redraw()
    return removed


def clear_all(*, redraw=True):
    had_entries = bool(_CACHE)
    _CACHE.clear()
    if redraw:
        _tag_redraw()
    return had_entries


def _visible_uv_triangles(scene):
    """Copy visible UV face triangles that can touch the main UDIM."""

    from . import overlay

    sources = overlay._collect_sources_for_scene(scene)
    if not sources:
        objects = overlay._source_objects_for_scene(scene)
        if not objects:
            raise EmptinessError("Enter mesh Edit Mode")
        raise EmptinessError("No active UV map")

    positions = []
    triangles = []
    visible_faces = 0

    for _mesh, bm, uv_layer, _layer_name in sources:
        for face in bm.faces:
            if face.hide or len(face.loops) < 3:
                continue
            polygon = []
            for loop in face.loops:
                uv = loop[uv_layer].uv
                u = float(uv.x)
                v = float(uv.y)
                if not (math.isfinite(u) and math.isfinite(v)):
                    polygon = []
                    break
                polygon.append(Vector((u, v, 0.0)))
            if len(polygon) < 3:
                continue
            try:
                face_triangles = tessellate_polygon([polygon])
            except (RuntimeError, ValueError):
                face_triangles = ()
            face_is_in_main_udim = False
            for triangle in face_triangles:
                triangle_points = []
                for point in triangle:
                    # Blender 4.2 returns the input Vectors, while the 5.3
                    # alpha tessellator returns indices into the polygon.
                    if not hasattr(point, "x"):
                        point = polygon[int(point)]
                    triangle_points.append(point)
                if not _triangle_touches_main_udim(triangle_points):
                    continue
                offset = len(positions)
                for point in triangle_points:
                    positions.append((float(point.x), float(point.y)))
                triangles.append((offset, offset + 1, offset + 2))
                face_is_in_main_udim = True
            if face_is_in_main_udim:
                visible_faces += 1

    if visible_faces == 0:
        raise EmptinessError("No visible UV geometry in the main UDIM")
    if not triangles:
        raise EmptinessError("Visible UV geometry is degenerate")
    return positions, triangles, visible_faces


def _triangle_touches_main_udim(triangle):
    minimum_u = min(float(point.x) for point in triangle)
    minimum_v = min(float(point.y) for point in triangle)
    maximum_u = max(float(point.x) for point in triangle)
    maximum_v = max(float(point.y) for point in triangle)
    return not (
        maximum_u < 0.0
        or maximum_v < 0.0
        or minimum_u > 1.0
        or minimum_v > 1.0
    )


def _main_udim_domain():
    return (
        _MAIN_UDIM_BOUNDS,
        _BASE_PIXELS_PER_TILE,
        _BASE_PIXELS_PER_TILE,
        _BASE_PIXELS_PER_TILE,
    )


def _rasterize_occupancy(positions, triangles, bounds, width, height):
    """Rasterize UV faces into a compact CPU boolean mask via the GPU."""

    from . import overlay

    shader = overlay._ensure_shader()
    batch = batch_for_shader(
        shader,
        "TRIS",
        {"position": positions},
        indices=triangles,
    )
    offscreen = gpu.types.GPUOffScreen(width, height, format="RGBA8")
    previous_viewport = gpu.state.viewport_get()
    previous_blend = gpu.state.blend_get()
    previous_depth = gpu.state.depth_test_get()
    try:
        with offscreen.bind():
            gpu.state.viewport_set(0, 0, width, height)
            gpu.state.active_framebuffer_get().clear(
                color=(0.0, 0.0, 0.0, 0.0)
            )
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")
            shader.bind()
            shader.uniform_float("view_rect", bounds)
            shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
            batch.draw(shader)
            pixels = offscreen.texture_color.read()
            occupancy = [False] * (width * height)
            index = 0
            for y in range(height):
                row = pixels[y]
                for x in range(width):
                    occupancy[index] = int(row[x][3]) != 0
                    index += 1
            return occupancy
    finally:
        gpu.state.viewport_set(*previous_viewport)
        gpu.state.blend_set(previous_blend)
        gpu.state.depth_test_set(previous_depth)
        try:
            offscreen.free()
        except (ReferenceError, RuntimeError):
            pass


def _distance_transform_1d(values):
    """Squared Euclidean distance transform in linear time."""

    count = len(values)
    if count == 0:
        return []
    if not any(value < _INFINITY * 0.5 for value in values):
        return [_INFINITY] * count

    sites = [0] * count
    intersections = [0.0] * (count + 1)
    distances = [0.0] * count
    first = next(
        index
        for index, value in enumerate(values)
        if value < _INFINITY * 0.5
    )
    envelope_size = 0
    sites[0] = first
    intersections[0] = -math.inf
    intersections[1] = math.inf

    for query in range(first + 1, count):
        if values[query] >= _INFINITY * 0.5:
            continue
        while True:
            site = sites[envelope_size]
            intersection = (
                (values[query] + query * query)
                - (values[site] + site * site)
            ) / (2.0 * (query - site))
            if intersection > intersections[envelope_size]:
                break
            envelope_size -= 1
        envelope_size += 1
        sites[envelope_size] = query
        intersections[envelope_size] = intersection
        intersections[envelope_size + 1] = math.inf

    envelope_index = 0
    for query in range(count):
        while intersections[envelope_index + 1] < query:
            envelope_index += 1
        site = sites[envelope_index]
        delta = query - site
        distances[query] = delta * delta + values[site]
    return distances


def _distance_field(
    occupancy,
    width,
    height,
    padding_radius_pixels=0.0,
):
    """Return distances measured outward from the optional padding edge."""

    if width <= 0 or height <= 0 or len(occupancy) != width * height:
        raise ValueError("Occupancy dimensions do not match")
    if not any(occupancy):
        raise EmptinessError("UV geometry did not rasterize")
    padding_radius_pixels = max(0.0, float(padding_radius_pixels))

    horizontal = [_INFINITY] * (width * height)
    for y in range(height):
        offset = y * width
        values = [
            0.0 if occupancy[offset + x] else _INFINITY
            for x in range(width)
        ]
        horizontal[offset : offset + width] = _distance_transform_1d(values)

    squared = [_INFINITY] * (width * height)
    for x in range(width):
        values = [horizontal[y * width + x] for y in range(height)]
        transformed = _distance_transform_1d(values)
        for y, value in enumerate(transformed):
            squared[y * width + x] = value

    maximum_squared = max(
        (
            squared[index]
            for index, occupied in enumerate(occupancy)
            if not occupied and squared[index] < _INFINITY * 0.5
        ),
        default=0.0,
    )
    maximum_distance = max(
        0.0,
        math.sqrt(maximum_squared) - padding_radius_pixels,
    )
    if maximum_distance <= 0.0:
        return [0.0] * (width * height), 0.0, 2.0
    inverse_maximum = 1.0 / maximum_distance
    field = [0.0] * (width * height)
    empty_distances = []
    for index, value in enumerate(squared):
        if occupancy[index] or value >= _INFINITY * 0.5:
            continue
        distance = max(
            0.0,
            math.sqrt(value) - padding_radius_pixels,
        )
        if distance <= 0.0:
            continue
        empty_distances.append(distance)
        field[index] = min(1.0, distance * inverse_maximum)
    empty_distances.sort()
    highlight_count = max(
        1,
        math.ceil(len(empty_distances) * _HIGHLIGHT_FRACTION),
    )
    threshold_distance = empty_distances[-highlight_count]
    highlight_threshold = threshold_distance * inverse_maximum
    return field, maximum_distance, highlight_threshold


def _padding_width_for_scene(scene):
    """Return the active per-shell padding width, or zero when hidden."""

    from . import geometry, settings as settings_module

    global_settings = settings_module.get_preferences()
    scene_settings = getattr(scene, "uv_padding_overlay", None)
    if (
        global_settings is None
        or scene_settings is None
        or not global_settings.enabled
    ):
        return 0.0
    return geometry.compute_band_width(
        scene_settings.margin_px,
        scene_settings.texture_resolution,
    )


def _ensure_shader():
    global _SHADER
    if _SHADER is not None:
        return _SHADER
    interface = GPUStageInterfaceInfo("uv_emptiness_overlay_interface")
    interface.smooth("VEC2", "texture_coordinate")
    info = GPUShaderCreateInfo()
    info.vertex_in(0, "VEC2", "position")
    info.vertex_in(1, "VEC2", "texture_uv")
    info.push_constant("VEC4", "view_rect")
    info.push_constant("FLOAT", "highlight_threshold")
    info.push_constant("VEC4", "color")
    info.sampler(0, "FLOAT_2D", "field_texture")
    info.vertex_out(interface)
    info.fragment_out(0, "VEC4", "fragColor")
    info.vertex_source(
        """
        void main()
        {
            vec2 span = max(view_rect.zw - view_rect.xy, vec2(1e-20));
            vec2 unit_position = (position - view_rect.xy) / span;
            texture_coordinate = texture_uv;
            gl_Position = vec4(unit_position * 2.0 - 1.0, 0.0, 1.0);
        }
        """
    )
    info.fragment_source(
        """
        void main()
        {
            float distance_value = clamp(
                texture(field_texture, texture_coordinate).r,
                0.0,
                1.0
            );
            float is_emptiest = step(
                highlight_threshold,
                distance_value
            );
            float opacity = mix(
                distance_value * color.a,
                1.0,
                is_emptiest
            );
            fragColor = vec4(color.rgb, opacity);
        }
        """
    )
    _SHADER = gpu.shader.create_from_info(info)
    return _SHADER


def _display_batch(bounds):
    minimum_u, minimum_v, maximum_u, maximum_v = bounds
    shader = _ensure_shader()
    return batch_for_shader(
        shader,
        "TRIS",
        {
            "position": (
                (minimum_u, minimum_v),
                (maximum_u, minimum_v),
                (maximum_u, maximum_v),
                (minimum_u, maximum_v),
            ),
            "texture_uv": (
                (0.0, 0.0),
                (1.0, 0.0),
                (1.0, 1.0),
                (0.0, 1.0),
            ),
        },
        indices=((0, 1, 2), (0, 2, 3)),
    )


def _configure_texture_sampling(texture):
    """Enable newer sampler controls when the Blender API provides them.

    Blender 4.2 GPUTexture exposes neither method and uses its backend default
    sampler state. Blender 5.x lets the extension request filtering and edge
    extension explicitly.
    """

    filter_mode = getattr(texture, "filter_mode", None)
    if callable(filter_mode):
        filter_mode(True)
    extend_mode = getattr(texture, "extend_mode", None)
    if callable(extend_mode):
        extend_mode("EXTEND")


def calculate_scene(scene):
    """Calculate and cache one immutable emptiness snapshot for a scene."""

    from . import overlay

    if scene is None:
        raise EmptinessError("No active scene")
    if overlay._active_transform_operator() is not None:
        raise EmptinessError("Finish the current UV transform first")

    started = time.perf_counter()
    positions, triangles, visible_faces = _visible_uv_triangles(scene)
    bounds, width, height, pixels_per_uv = _main_udim_domain()
    occupancy = _rasterize_occupancy(
        positions,
        triangles,
        bounds,
        width,
        height,
    )
    if not any(occupancy):
        raise EmptinessError("No visible UV geometry in the main UDIM")
    padding_width_uv = _padding_width_for_scene(scene)
    field, maximum_pixels, highlight_threshold = _distance_field(
        occupancy,
        width,
        height,
        padding_width_uv * pixels_per_uv,
    )
    texture = gpu.types.GPUTexture(
        (width, height),
        format="R32F",
        data=gpu.types.Buffer("FLOAT", len(field), field),
    )
    _configure_texture_sampling(texture)
    batch = _display_batch(bounds)
    entry = _CacheEntry(
        texture,
        batch,
        bounds,
        width,
        height,
        pixels_per_uv,
        maximum_pixels / pixels_per_uv,
        padding_width_uv,
        highlight_threshold,
        visible_faces,
        len(triangles),
        (time.perf_counter() - started) * 1000.0,
    )
    _CACHE[scene.as_pointer()] = entry
    _tag_redraw()
    return entry


def draw(scene, view_rect, color):
    """Draw only cached GPU data; never inspect edit BMesh here."""

    entry = get_entry(scene)
    if entry is None or entry.texture is None or entry.batch is None:
        return
    shader = _ensure_shader()
    previous_blend = gpu.state.blend_get()
    previous_depth = gpu.state.depth_test_get()
    try:
        shader.bind()
        shader.uniform_float("view_rect", view_rect)
        shader.uniform_float(
            "highlight_threshold",
            entry.highlight_threshold,
        )
        shader.uniform_float("color", color)
        shader.uniform_sampler("field_texture", entry.texture)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("ALPHA")
        entry.batch.draw(shader)
    finally:
        gpu.state.blend_set(previous_blend)
        gpu.state.depth_test_set(previous_depth)


class UVPADDING_OT_calculate_emptiness(Operator):
    bl_idname = "uv_padding_overlay.calculate_emptiness"
    bl_label = "Calculate Emptiness"
    bl_description = (
        "Calculate a field in empty UV space"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return getattr(context, "scene", None) is not None

    def execute(self, context):
        window = getattr(context, "window", None)
        if window is not None:
            window.cursor_modal_set("WAIT")
        try:
            entry = calculate_scene(context.scene)
        except EmptinessError as error:
            self.report({"INFO"}, str(error))
            return {"CANCELLED"}
        except (
            AttributeError,
            IndexError,
            ReferenceError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            self.report({"WARNING"}, f"Emptiness calculation failed: {error}")
            return {"CANCELLED"}
        finally:
            if window is not None:
                window.cursor_modal_restore()
        self.report(
            {"INFO"},
            (
                f"Emptiness: {entry.width} x {entry.height}, "
                f"max {entry.max_distance_uv:.4g} UV, "
                f"{entry.calculation_ms:.0f} ms"
            ),
        )
        return {"FINISHED"}


class UVPADDING_OT_clear_emptiness(Operator):
    bl_idname = "uv_padding_overlay.clear_emptiness"
    bl_label = "Clear Emptiness"
    bl_description = (
        "Clear the calculated emptiness overlay"
    )
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return getattr(context, "scene", None) is not None

    def execute(self, context):
        clear_scene(context.scene)
        return {"FINISHED"}


_CLASSES = (
    UVPADDING_OT_calculate_emptiness,
    UVPADDING_OT_clear_emptiness,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global _SHADER
    clear_all(redraw=False)
    _SHADER = None
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
