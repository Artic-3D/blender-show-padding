"""Blender-hosted tests for UV Padding Overlay.

Run with:
    blender --background --factory-startup --python tests/run_tests.py
"""

from __future__ import annotations

import math
import importlib
import os
import sys
import tempfile
import traceback

import bmesh
import bpy


WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import uv_padding_overlay as addon
from uv_padding_overlay import geometry, overlay, settings as settings_module, ui


def assert_equal(actual, expected, message=""):
    if actual != expected:
        raise AssertionError(f"{message} expected {expected!r}, got {actual!r}")


def assert_close(actual, expected, tolerance=1.0e-9, message=""):
    if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance):
        raise AssertionError(f"{message} expected {expected!r}, got {actual!r}")


def make_bmesh(vertices, faces, face_uvs, name="UVMap"):
    bm = bmesh.new()
    bm_vertices = [bm.verts.new(co) for co in vertices]
    bm.verts.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.new(name)
    created_faces = []
    for indices, uvs in zip(faces, face_uvs):
        face = bm.faces.new([bm_vertices[index] for index in indices])
        created_faces.append(face)
        for loop, uv in zip(face.loops, uvs):
            loop[uv_layer].uv = uv
    bm.normal_update()
    return bm, uv_layer, created_faces


def square_fixture(offset=(0.0, 0.0), mirrored=False):
    vertices = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (1.0, 1.0, 0.0),
        (0.0, 1.0, 0.0),
    ]
    ox, oy = offset
    if mirrored:
        uvs = [(ox, oy), (ox, oy + 1.0), (ox + 1.0, oy + 1.0), (ox + 1.0, oy)]
    else:
        uvs = [(ox, oy), (ox + 1.0, oy), (ox + 1.0, oy + 1.0), (ox, oy + 1.0)]
    return make_bmesh(vertices, [(0, 1, 2, 3)], [uvs])


def adjacent_fixture(seam=False):
    vertices = [
        (0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (2.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
        (2.0, 1.0, 0.0),
    ]
    first = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    if seam:
        second = [(2.0, 0.0), (3.0, 0.0), (3.0, 1.0), (2.0, 1.0)]
    else:
        second = [(1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0)]
    return make_bmesh(
        vertices,
        [(0, 1, 4, 3), (1, 2, 5, 4)],
        [first, second],
    )


def concave_fixture(mirrored=False):
    points = [
        (0.0, 0.0),
        (2.0, 0.0),
        (2.0, 1.0),
        (1.0, 1.0),
        (1.0, 2.0),
        (0.0, 2.0),
    ]
    if mirrored:
        points = [(-x, y) for x, y in points]
    vertices = [(x, y, 0.0) for x, y in points]
    uvs = [points]
    return make_bmesh(vertices, [(0, 1, 2, 3, 4, 5)], uvs)


def ring_fixture():
    vertices_2d = [
        (0.0, 0.0),
        (3.0, 0.0),
        (3.0, 3.0),
        (0.0, 3.0),
        (1.0, 1.0),
        (2.0, 1.0),
        (2.0, 2.0),
        (1.0, 2.0),
    ]
    vertices = [(x, y, 0.0) for x, y in vertices_2d]
    faces = [
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    ]
    face_uvs = [[vertices_2d[index] for index in face] for face in faces]
    return make_bmesh(vertices, faces, face_uvs)


def build(
    bm,
    uv_layer,
    width=0.1,
    selected_only=False,
    uv_select_sync=False,
    corner_segments=2,
    mesh_select_mode=None,
):
    return geometry.build_overlay_geometry(
        bm,
        uv_layer,
        width,
        selected_only,
        uv_select_sync,
        corner_segments,
        mesh_select_mode,
    )


def test_conversion():
    assert_close(geometry.compute_band_width(8.0, 2048), 0.001953125)


def test_power_of_two_steps():
    assert_close(ui._adjacent_power_of_two(1.0, 1), 2.0)
    assert_close(ui._adjacent_power_of_two(2.0, 1), 4.0)
    assert_close(ui._adjacent_power_of_two(8.0, -1), 4.0)
    assert_close(ui._adjacent_power_of_two(10.0, -1), 8.0)
    assert_close(ui._adjacent_power_of_two(10.0, 1), 16.0)
    assert_close(ui._adjacent_power_of_two(7.5, -1), 4.0)
    assert_close(ui._adjacent_power_of_two(7.5, 1), 8.0)
    assert_close(geometry.compute_band_width(-2.0, 2048), 0.0)
    assert_close(geometry.compute_band_width(8.0, 0), 4.0)
    overlap_alpha = 0.5 + 0.5 * (1.0 - 0.5)
    assert_close(overlap_alpha, 0.75)


def test_single_square_and_mirror():
    for mirrored in (False, True):
        bm, uv_layer, _faces = square_fixture(mirrored=mirrored)
        try:
            positions, triangles, stats, _signature = build(bm, uv_layer)
            assert_equal(stats["visible_faces"], 1)
            assert_equal(stats["islands"], 1)
            assert_equal(stats["boundary_edges"], 4)
            assert_equal(stats["degenerate_edges"], 0)
            assert_equal(stats["triangles"], len(triangles))
            xs = [point[0] for point in positions]
            ys = [point[1] for point in positions]
            assert_close(min(xs), -0.1)
            assert_close(max(xs), 1.1)
            assert_close(min(ys), -0.1)
            assert_close(max(ys), 1.1)
        finally:
            bm.free()


def test_roundness_segment_count():
    bm, uv_layer, _faces = square_fixture()
    try:
        _positions, triangles, stats, _signature = build(
            bm,
            uv_layer,
            corner_segments=1,
        )
        assert_equal(len(triangles), 12)
        assert_equal(stats["triangles"], 12)

        _positions, triangles, stats, _signature = build(
            bm,
            uv_layer,
            corner_segments=5,
        )
        assert_equal(len(triangles), 28)
        assert_equal(stats["triangles"], 28)
    finally:
        bm.free()


def test_outer_outline_segments():
    bm, uv_layer, _faces = square_fixture()
    try:
        (
            positions,
            triangles,
            stats,
            _signature,
            topology,
        ) = geometry.build_overlay_geometry_with_template(
            bm,
            uv_layer,
            0.1,
            corner_segments=2,
        )
        assert_equal(stats["triangles"], len(triangles))
        assert_equal(len(topology.outer_segments), 12)
        inner_indices = {
            index
            for use in topology.boundary_uses
            for index in (use.inner_start, use.inner_end)
        }
        for first, second in topology.outer_segments:
            if first in inner_indices or second in inner_indices:
                raise AssertionError("Closed-shell outline contains an inner edge")
            midpoint = (
                (positions[first][0] + positions[second][0]) * 0.5,
                (positions[first][1] + positions[second][1]) * 0.5,
            )
            if 0.0 < midpoint[0] < 1.0 and 0.0 < midpoint[1] < 1.0:
                raise AssertionError("Outer outline entered the UV shell")
    finally:
        bm.free()


def test_connected_faces_and_seam():
    bm, uv_layer, _faces = adjacent_fixture(seam=False)
    try:
        _positions, _triangles, stats, _signature = build(bm, uv_layer)
        assert_equal(stats["islands"], 1)
        assert_equal(stats["boundary_edges"], 6)
    finally:
        bm.free()

    bm, uv_layer, _faces = adjacent_fixture(seam=True)
    try:
        _positions, _triangles, stats, _signature = build(bm, uv_layer)
        assert_equal(stats["islands"], 2)
        assert_equal(stats["boundary_edges"], 8)
    finally:
        bm.free()


def test_hole_and_udim():
    bm, uv_layer, _faces = ring_fixture()
    try:
        _positions, _triangles, stats, _signature = build(bm, uv_layer)
        assert_equal(stats["islands"], 1)
        assert_equal(stats["boundary_edges"], 8)
    finally:
        bm.free()

    bm, uv_layer, _faces = square_fixture(offset=(3.0, -2.0))
    try:
        positions, _triangles, stats, _signature = build(bm, uv_layer)
        assert_equal(stats["islands"], 1)
        assert_close(min(point[0] for point in positions), 2.9)
        assert_close(min(point[1] for point in positions), -2.1)
    finally:
        bm.free()


def _strictly_inside_triangle(point, triangle, tolerance=1.0e-9):
    signs = []
    for index, first in enumerate(triangle):
        second = triangle[(index + 1) % 3]
        signs.append(
            (second[0] - first[0]) * (point[1] - first[1])
            - (second[1] - first[1]) * (point[0] - first[0])
        )
    return all(value > tolerance for value in signs) or all(
        value < -tolerance for value in signs
    )


def test_concave_join_has_no_self_overlap():
    for mirrored in (False, True):
        bm, uv_layer, _faces = concave_fixture(mirrored=mirrored)
        try:
            positions, triangles, stats, _signature = build(
                bm,
                uv_layer,
                width=0.2,
            )
            assert_equal(stats["islands"], 1)
            assert_equal(stats["boundary_edges"], 6)
            # Both untrimmed edge strips used to cover this point inside the
            # concave corner, producing 75% opacity for one 50% shell.
            direction = -1.0 if mirrored else 1.0
            sample = (direction * 1.08, 1.15)
            coverage = sum(
                _strictly_inside_triangle(
                    sample,
                    tuple(positions[index] for index in triangle),
                )
                for triangle in triangles
            )
            assert_equal(coverage, 1)
            matching_miters = sum(
                math.isclose(
                    point[0],
                    direction * 1.2,
                    abs_tol=1.0e-9,
                )
                and math.isclose(point[1], 1.2, abs_tol=1.0e-9)
                for point in positions
            )
            assert_equal(matching_miters, 2)
        finally:
            bm.free()


def test_selection_and_hidden_faces():
    bm, uv_layer, faces = adjacent_fixture(seam=False)
    try:
        positions, _triangles, stats, _signature = build(
            bm,
            uv_layer,
            selected_only=True,
        )
        assert_equal(len(positions), 0)
        assert_equal(stats["islands"], 0)
        sample_uv = faces[0].loops[0][uv_layer]
        if hasattr(sample_uv, "select"):
            sample_uv.select = True
        else:
            faces[0].select_set(True)
        positions, _triangles, stats, _signature = build(
            bm,
            uv_layer,
            selected_only=True,
        )
        assert len(positions) > 0
        assert_equal(stats["islands"], 1)
        assert_equal(stats["boundary_edges"], 6)

        if hasattr(sample_uv, "select"):
            sample_uv.select = False
        else:
            faces[0].select_set(False)
        faces[1].verts[0].select_set(True)
        positions, _triangles, stats, _signature = build(
            bm,
            uv_layer,
            selected_only=True,
            uv_select_sync=True,
        )
        assert len(positions) > 0
        assert_equal(stats["islands"], 1)

        faces[1].hide_set(True)
        _positions, _triangles, stats, _signature = build(bm, uv_layer)
        assert_equal(stats["visible_faces"], 1)
        assert_equal(stats["boundary_edges"], 4)
    finally:
        bm.free()


def test_sync_selection_respects_mesh_select_mode():
    bm, uv_layer, faces = adjacent_fixture(seam=True)
    try:
        # Selecting one face also selects its vertices and edges. In Face mode
        # those implicit flags must not leak selection into the adjacent shell.
        faces[0].select_set(True)
        _positions, _triangles, stats, _signature = build(
            bm,
            uv_layer,
            selected_only=True,
            uv_select_sync=True,
            mesh_select_mode=(False, False, True),
        )
        assert_equal(stats["islands"], 1)
        assert_equal(stats["boundary_edges"], 4)

        for mode in (
            (False, True, False),
            (True, False, False),
        ):
            _positions, _triangles, stats, _signature = build(
                bm,
                uv_layer,
                selected_only=True,
                uv_select_sync=True,
                mesh_select_mode=mode,
            )
            assert_equal(stats["islands"], 1)
            assert_equal(stats["boundary_edges"], 4)

        # A standalone shared vertex has no fully selected face to identify
        # its UV occurrence, so both possible shells remain conservatively
        # included rather than guessing.
        faces[0].select_set(False)
        faces[0].verts[1].select_set(True)
        _positions, _triangles, stats, _signature = build(
            bm,
            uv_layer,
            selected_only=True,
            uv_select_sync=True,
            mesh_select_mode=(True, False, False),
        )
        assert_equal(stats["islands"], 2)
        assert_equal(stats["boundary_edges"], 8)
    finally:
        bm.free()


def test_zero_and_degenerate():
    bm, uv_layer, _faces = square_fixture()
    try:
        positions, triangles, stats, _signature = build(bm, uv_layer, width=0.0)
        assert_equal(positions, [])
        assert_equal(triangles, [])
        assert_equal(stats["boundary_edges"], 0)
    finally:
        bm.free()

    vertices = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
    uvs = [[(0.0, 0.0), (0.0, 0.0), (0.0, 1.0)]]
    bm, uv_layer, _faces = make_bmesh(vertices, [(0, 1, 2)], uvs)
    try:
        _positions, _triangles, stats, _signature = build(bm, uv_layer)
        assert_equal(stats["degenerate_edges"], 1)
        assert_equal(stats["boundary_edges"], 2)
    finally:
        bm.free()


def test_signature_changes():
    bm, uv_layer, faces = square_fixture()
    try:
        before = geometry.state_signature(bm, uv_layer)
        faces[0].loops[0][uv_layer].uv.x += 0.25
        after = geometry.state_signature(bm, uv_layer)
        if before == after:
            raise AssertionError("UV coordinate update did not change signature")
        unselected = geometry.state_signature(bm, uv_layer, selected_only=True)
        sample_uv = faces[0].loops[0][uv_layer]
        if hasattr(sample_uv, "select"):
            sample_uv.select = True
        else:
            faces[0].select_set(True)
        selected = geometry.state_signature(bm, uv_layer, selected_only=True)
        if unselected == selected:
            raise AssertionError("UV selection update did not change signature")
    finally:
        bm.free()


def test_cached_coordinate_refresh():
    bm, uv_layer, faces = square_fixture()
    try:
        (
            _positions,
            _triangles,
            _stats,
            _signature,
            topology,
        ) = geometry.build_overlay_geometry_with_template(
            bm,
            uv_layer,
            0.1,
        )
        for iteration in range(250):
            for loop in faces[0].loops:
                loop[uv_layer].uv.x += 0.008
            positions, triangles, stats = geometry.rebuild_overlay_geometry(
                bm,
                topology,
                uv_layer,
                0.2,
                5,
            )
        assert_close(min(point[0] for point in positions), 1.8, tolerance=2.0e-5)
        assert_close(max(point[0] for point in positions), 3.2, tolerance=2.0e-5)
        assert_equal(stats["boundary_edges"], 4)
        assert_equal(stats["triangles"], len(triangles))
        assert_equal(stats["triangles"], 28)
        assert_equal(len(topology.outer_segments), 24)
        if any(
            index >= len(positions)
            for segment in topology.outer_segments
            for index in segment
        ):
            raise AssertionError("Refreshed outline index is out of range")
        for use in topology.boundary_uses:
            if hasattr(use, "loop"):
                raise AssertionError("Topology cache retained a live BMesh loop")
            assert isinstance(use.bm_face_index, int)
            assert isinstance(use.loop_index, int)
    finally:
        bm.free()


def test_draw_callback_is_bmesh_free():
    forbidden_names = {
        "bmesh",
        "_collect_sources_for_scene",
        "_ensure_entry",
        "state_signature",
    }
    used_names = set(overlay._draw_overlay.__code__.co_names)
    overlap = forbidden_names.intersection(used_names)
    if overlap:
        raise AssertionError(f"Draw callback reads live mesh state: {sorted(overlap)}")


def test_transform_quiet_gate():
    original_probe = overlay._active_transform_operator
    original_invalidation = overlay._LAST_INVALIDATION
    original_transform = overlay._LAST_TRANSFORM_ACTIVITY
    try:
        overlay._LAST_INVALIDATION = 0.0
        overlay._LAST_TRANSFORM_ACTIVITY = 0.0
        overlay._active_transform_operator = lambda: object()
        assert_equal(overlay._bmesh_snapshot_is_safe(10.0), False)
        assert_close(overlay._LAST_TRANSFORM_ACTIVITY, 10.0)

        overlay._active_transform_operator = lambda: None
        assert_equal(
            overlay._bmesh_snapshot_is_safe(
                10.0 + overlay._BMESH_QUIET_DELAY * 0.5
            ),
            False,
        )
        assert_equal(
            overlay._bmesh_snapshot_is_safe(
                10.0 + overlay._BMESH_QUIET_DELAY + 0.001
            ),
            True,
        )
    finally:
        overlay._active_transform_operator = original_probe
        overlay._LAST_INVALIDATION = original_invalidation
        overlay._LAST_TRANSFORM_ACTIVITY = original_transform


def test_lifecycle_and_persistence():
    preferences = bpy.context.preferences
    created_addon_entry = False
    if preferences.addons.get(settings_module.ADDON_ID) is None:
        addon_entry = preferences.addons.new()
        addon_entry.module = settings_module.ADDON_ID
        created_addon_entry = True
    addon.register()
    try:
        settings_module.migrate_legacy_scene_settings()
        assert hasattr(bpy.types.Scene, "uv_padding_overlay")
        scene_settings = bpy.context.scene.uv_padding_overlay
        global_settings = settings_module.get_preferences()
        if global_settings is None:
            raise AssertionError("Global extension preferences are unavailable")

        scene_property_names = set(scene_settings.bl_rna.properties.keys())
        for name in ("margin_px", "texture_resolution", "outline_width_px"):
            if name not in scene_property_names:
                raise AssertionError(f"Missing scene property: {name}")
        for name in (
            "enabled",
            "selected_only",
            "corner_segments",
            "render_mode",
            "thin_width",
            "color",
            "highlight_color",
        ):
            if name in scene_property_names:
                raise AssertionError(f"Global property stored on Scene: {name}")

        assert_close(scene_settings.margin_px, 8.0)
        assert_equal(scene_settings.texture_resolution, 2048)
        assert_close(scene_settings.outline_width_px, 4.0)
        scene_settings.margin_px = 10.0
        assert_equal(
            bpy.ops.uv_padding_overlay.step_power_of_two(
                property_name="margin_px",
                direction=-1,
            ),
            {"FINISHED"},
        )
        assert_close(scene_settings.margin_px, 8.0)
        scene_settings.margin_px = 7.5
        assert_close(scene_settings.margin_px, 7.5)
        bpy.ops.uv_padding_overlay.step_power_of_two(
            property_name="margin_px",
            direction=1,
        )
        assert_close(scene_settings.margin_px, 8.0)
        scene_settings.texture_resolution = 3000
        bpy.ops.uv_padding_overlay.step_power_of_two(
            property_name="texture_resolution",
            direction=-1,
        )
        assert_equal(scene_settings.texture_resolution, 2048)
        assert_equal(global_settings.enabled, True)
        assert_equal(
            ui.UVPADDING_OT_toggle_padding.bl_label,
            "Show Padding",
        )
        if "INTERNAL" in ui.UVPADDING_OT_toggle_padding.bl_options:
            raise AssertionError("Show Padding must remain searchable")
        assert_equal(
            bpy.ops.uv_padding_overlay.toggle_padding(),
            {"FINISHED"},
        )
        assert_equal(global_settings.enabled, False)
        assert_equal(
            bpy.ops.uv_padding_overlay.toggle_padding(),
            {"FINISHED"},
        )
        assert_equal(global_settings.enabled, True)
        assert_equal(global_settings.selected_only, False)
        assert_equal(global_settings.corner_segments, 2)
        assert_equal(global_settings.render_mode, "HIGHLIGHTED")
        assert_equal(global_settings.thin_width, 1)
        global_settings.thin_width = 12
        assert_equal(global_settings.thin_width, 8)
        scene_settings.margin_px = 3.75
        assert_equal(global_settings.thin_width, 3)
        global_settings.thin_width = 9
        assert_equal(global_settings.thin_width, 3)
        scene_settings.margin_px = 0.5
        assert_equal(global_settings.thin_width, 0)
        scene_settings.margin_px = 8.0
        global_settings.thin_width = 1
        assert_equal(
            global_settings.bl_rna.properties["render_mode"].name,
            "Mode",
        )
        assert_equal(
            global_settings.bl_rna.properties["render_mode"]
            .enum_items["HIGHLIGHTED"]
            .name,
            "Highlighted",
        )
        assert_equal(
            global_settings.bl_rna.properties["render_mode"]
            .enum_items["THIN_HIGHLIGHTED"]
            .name,
            "Thin Highlighted",
        )
        assert_equal(
            global_settings.bl_rna.properties["render_mode"]
            .enum_items["LAYERED"]
            .name,
            "Stacked",
        )
        assert_equal(
            global_settings.bl_rna.properties["render_mode"]
            .enum_items["UNIFIED"]
            .name,
            "Solid",
        )
        assert_equal(len(global_settings.color), 4)
        assert_close(global_settings.color[3], 0.25)
        assert_equal(len(global_settings.highlight_color), 4)
        assert_close(
            global_settings.highlight_color[3],
            0.65,
            tolerance=1.0e-6,
        )
        assert_equal(global_settings.storage_version, 1)

        # Simulate raw values left by the v1.2 scene PropertyGroup and verify
        # that a first-run migration adopts and removes them.
        global_settings.storage_version = 0
        scene_settings["selected_only"] = True
        scene_settings["corner_segments"] = 9
        scene_settings["render_mode"] = "UNIFIED"
        scene_settings["color"] = [0.1, 0.2, 0.3, 0.4]
        assert_equal(settings_module.migrate_legacy_scene_settings(), True)
        assert_equal(global_settings.storage_version, 1)
        assert_equal(global_settings.selected_only, True)
        assert_equal(global_settings.corner_segments, 9)
        assert_equal(global_settings.render_mode, "UNIFIED")
        assert_close(global_settings.color[3], 0.4, tolerance=1.0e-6)
        for name in settings_module._GLOBAL_SETTING_NAMES:
            if name in scene_settings:
                raise AssertionError(f"Legacy scene value was not removed: {name}")

        scene_settings.margin_px = 18.0
        assert_close(scene_settings.outline_width_px, 9.0)
        global_settings.render_mode = "UNIFIED"
        global_settings.corner_segments = 7
        global_settings.thin_width = 4
        global_settings.selected_only = False
        global_settings.color = (0.2, 0.3, 0.4, 0.27)
        global_settings.highlight_color = (0.9, 0.8, 0.1, 0.72)
        assert_close(global_settings.color[3], 0.27, tolerance=1.0e-6)
        assert_close(
            global_settings.highlight_color[3],
            0.72,
            tolerance=1.0e-6,
        )
        assert overlay._DRAW_HANDLE is not None
        assert bpy.app.timers.is_registered(overlay._update_timer)
        overlay.request_style_redraw()
        assert bpy.app.timers.is_registered(overlay._deferred_style_redraw)
        # Background tests have no UI event loop between these assertions.
        bpy.app.timers.unregister(overlay._deferred_style_redraw)
        assert overlay._depsgraph_update_post in bpy.app.handlers.depsgraph_update_post
        runtime = bpy.app.driver_namespace.get(
            overlay._RUNTIME_NAMESPACE_KEY
        )
        assert runtime is overlay._RUNTIME_RECORD
        assert runtime["draw_handle"] is overlay._DRAW_HANDLE

        # Simulate the top-level-only reload performed by Blender's extension
        # installer. The package must retire its old callbacks, reload every
        # submodule, and be ready for the installer's subsequent register().
        old_runtime = runtime
        importlib.reload(addon)
        assert_equal(old_runtime, {})
        assert not hasattr(bpy.types.Scene, "uv_padding_overlay")
        addon.register()
        global_settings = settings_module.get_preferences()
        if global_settings is None:
            raise AssertionError("Preferences were lost during extension reload")
        assert_equal(global_settings.render_mode, "UNIFIED")
        assert_equal(global_settings.corner_segments, 7)
        assert_equal(global_settings.thin_width, 4)
        assert_close(global_settings.color[3], 0.27, tolerance=1.0e-6)
        assert_close(
            global_settings.highlight_color[3],
            0.72,
            tolerance=1.0e-6,
        )
        replacement = bpy.app.driver_namespace.get(
            overlay._RUNTIME_NAMESPACE_KEY
        )
        assert replacement is overlay._RUNTIME_RECORD
        assert replacement is not old_runtime
        assert_equal(
            bpy.app.handlers.depsgraph_update_post.count(
                overlay._depsgraph_update_post
            ),
            1,
        )
        # Blender deliberately disables GPU drawing functions in background
        # mode. Shader compilation is covered by the interactive MCP test.
        if not bpy.app.background:
            shader = overlay._ensure_shader()
            if shader is None:
                raise AssertionError("GPU shader was not created")

        scene_settings = bpy.context.scene.uv_padding_overlay
        scene_settings.margin_px = 12.5
        scene_settings.texture_resolution = 4096
        with tempfile.TemporaryDirectory() as temporary_directory:
            blend_path = os.path.join(temporary_directory, "settings.blend")
            bpy.ops.wm.save_as_mainfile(filepath=blend_path)
            scene_settings.margin_px = 2.0
            scene_settings.texture_resolution = 1024
            global_settings.enabled = False
            global_settings.selected_only = True
            global_settings.render_mode = "LAYERED"
            global_settings.corner_segments = 3
            global_settings.thin_width = 2
            global_settings.color = (0.8, 0.7, 0.6, 0.19)
            global_settings.highlight_color = (0.1, 0.8, 0.9, 0.71)
            bpy.ops.wm.open_mainfile(filepath=blend_path)
            restored_scene_settings = bpy.context.scene.uv_padding_overlay
            restored_global_settings = settings_module.get_preferences()
            assert_close(restored_scene_settings.margin_px, 12.5)
            assert_equal(restored_scene_settings.texture_resolution, 4096)
            assert_equal(restored_global_settings.enabled, False)
            assert_equal(restored_global_settings.selected_only, True)
            assert_equal(
                restored_global_settings.render_mode,
                "LAYERED",
            )
            assert_equal(
                restored_global_settings.corner_segments,
                3,
            )
            assert_equal(restored_global_settings.thin_width, 2)
            assert_close(
                restored_global_settings.color[3],
                0.19,
                tolerance=1.0e-6,
            )
            assert_close(
                restored_global_settings.highlight_color[3],
                0.71,
                tolerance=1.0e-6,
            )
    finally:
        addon.unregister()
        if created_addon_entry:
            addon_entry = preferences.addons.get(settings_module.ADDON_ID)
            if addon_entry is not None:
                preferences.addons.remove(addon_entry)
    assert not hasattr(bpy.types.Scene, "uv_padding_overlay")
    assert overlay._DRAW_HANDLE is None
    assert not bpy.app.timers.is_registered(overlay._update_timer)
    assert not bpy.app.timers.is_registered(overlay._deferred_style_redraw)
    assert overlay._depsgraph_update_post not in bpy.app.handlers.depsgraph_update_post
    assert overlay._RUNTIME_NAMESPACE_KEY not in bpy.app.driver_namespace


TESTS = (
    test_conversion,
    test_power_of_two_steps,
    test_single_square_and_mirror,
    test_roundness_segment_count,
    test_outer_outline_segments,
    test_connected_faces_and_seam,
    test_hole_and_udim,
    test_concave_join_has_no_self_overlap,
    test_selection_and_hidden_faces,
    test_sync_selection_respects_mesh_select_mode,
    test_zero_and_degenerate,
    test_signature_changes,
    test_cached_coordinate_refresh,
    test_draw_callback_is_bmesh_free,
    test_transform_quiet_gate,
    test_lifecycle_and_persistence,
)


def main():
    failures = []
    for test in TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception:
            failures.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    if failures:
        raise SystemExit(f"FAILED: {', '.join(failures)}")
    print(f"ALL TESTS PASSED ({len(TESTS)})")


if __name__ == "__main__":
    main()
