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
from uv_padding_overlay import geometry, overlay


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
):
    return geometry.build_overlay_geometry(
        bm,
        uv_layer,
        width,
        selected_only,
        uv_select_sync,
        corner_segments,
    )


def test_conversion():
    assert_close(geometry.compute_band_width(8.0, 2048), 0.001953125)
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
    addon.register()
    try:
        assert hasattr(bpy.types.Scene, "uv_padding_overlay")
        settings = bpy.context.scene.uv_padding_overlay
        assert_close(settings.margin_px, 8.0)
        assert_equal(settings.texture_resolution, 2048)
        assert_equal(settings.enabled, True)
        assert_equal(settings.selected_only, False)
        assert_close(settings.outline_width_px, 4.0)
        assert_equal(settings.corner_segments, 2)
        assert_equal(settings.render_mode, "LAYERED")
        assert_equal(
            settings.bl_rna.properties["render_mode"]
            .enum_items["UNIFIED"]
            .name,
            "Solid",
        )
        assert_equal(len(settings.color), 4)
        assert_close(settings.color[3], 0.5)
        settings.margin_px = 18.0
        assert_close(settings.outline_width_px, 9.0)
        settings.render_mode = "UNIFIED"
        settings.corner_segments = 7
        settings.color = (0.2, 0.3, 0.4, 0.27)
        assert_close(settings.color[3], 0.27, tolerance=1.0e-6)
        assert overlay._DRAW_HANDLE is not None
        assert bpy.app.timers.is_registered(overlay._update_timer)
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

        settings.margin_px = 12.5
        with tempfile.TemporaryDirectory() as temporary_directory:
            blend_path = os.path.join(temporary_directory, "settings.blend")
            bpy.ops.wm.save_as_mainfile(filepath=blend_path)
            settings.margin_px = 2.0
            bpy.ops.wm.open_mainfile(filepath=blend_path)
            assert_close(bpy.context.scene.uv_padding_overlay.margin_px, 12.5)
            assert_equal(
                bpy.context.scene.uv_padding_overlay.render_mode,
                "UNIFIED",
            )
            assert_equal(
                bpy.context.scene.uv_padding_overlay.corner_segments,
                7,
            )
            assert_close(
                bpy.context.scene.uv_padding_overlay.color[3],
                0.27,
                tolerance=1.0e-6,
            )
    finally:
        addon.unregister()
    assert not hasattr(bpy.types.Scene, "uv_padding_overlay")
    assert overlay._DRAW_HANDLE is None
    assert not bpy.app.timers.is_registered(overlay._update_timer)
    assert overlay._depsgraph_update_post not in bpy.app.handlers.depsgraph_update_post
    assert overlay._RUNTIME_NAMESPACE_KEY not in bpy.app.driver_namespace


TESTS = (
    test_conversion,
    test_single_square_and_mirror,
    test_roundness_segment_count,
    test_connected_faces_and_seam,
    test_hole_and_udim,
    test_concave_join_has_no_self_overlap,
    test_selection_and_hidden_faces,
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
