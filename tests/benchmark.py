"""100k-loop geometry and cached GPU draw benchmark."""

from __future__ import annotations

import os
import statistics
import sys
import time

import bmesh
import bpy
import gpu
from gpu.types import GPUOffScreen
from gpu_extras.batch import batch_for_shader


WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

from uv_padding_overlay import geometry, overlay


GRID_X = 250
GRID_Y = 100


def make_grid(seamed):
    bm = bmesh.new()
    vertices = []
    for y in range(GRID_Y + 1):
        row = []
        for x in range(GRID_X + 1):
            row.append(bm.verts.new((float(x), float(y), 0.0)))
        vertices.append(row)
    uv_layer = bm.loops.layers.uv.new("UVMap")
    for y in range(GRID_Y):
        for x in range(GRID_X):
            face = bm.faces.new(
                (
                    vertices[y][x],
                    vertices[y][x + 1],
                    vertices[y + 1][x + 1],
                    vertices[y + 1][x],
                )
            )
            if seamed:
                base_x = float(x * 2)
                base_y = float(y * 2)
                uvs = (
                    (base_x, base_y),
                    (base_x + 1.0, base_y),
                    (base_x + 1.0, base_y + 1.0),
                    (base_x, base_y + 1.0),
                )
            else:
                uvs = (
                    (float(x), float(y)),
                    (float(x + 1), float(y)),
                    (float(x + 1), float(y + 1)),
                    (float(x), float(y + 1)),
                )
            for loop, uv in zip(face.loops, uvs):
                loop[uv_layer].uv = uv
    bm.normal_update()
    return bm, uv_layer


def benchmark_case(label, seamed):
    bm, uv_layer = make_grid(seamed)
    try:
        samples = []
        result = None
        for _iteration in range(3):
            started = time.perf_counter()
            result = geometry.build_overlay_geometry_with_template(
                bm,
                uv_layer,
                geometry.compute_band_width(8.0, 2048),
            )
            samples.append((time.perf_counter() - started) * 1000.0)
        positions, triangles, stats, _signature, template = result
        refresh_samples = []
        for _iteration in range(10):
            started = time.perf_counter()
            geometry.rebuild_overlay_geometry(
                bm,
                template,
                uv_layer,
                geometry.compute_band_width(8.0, 2048),
            )
            refresh_samples.append((time.perf_counter() - started) * 1000.0)
        print(
            "GEOMETRY",
            label,
            {
                "uv_loops": GRID_X * GRID_Y * 4,
                "median_ms": round(statistics.median(samples), 3),
                "coordinate_refresh_ms": round(
                    statistics.median(refresh_samples), 3
                ),
                "positions": len(positions),
                "triangles": len(triangles),
                **stats,
            },
        )
        return positions, triangles
    finally:
        bm.free()


def benchmark_cached_draw(positions, triangles):
    if bpy.app.background:
        print("CACHED_DRAW", {"skipped": "GPU unavailable in background mode"})
        return
    shader = overlay._ensure_shader()
    batch = batch_for_shader(
        shader,
        "TRIS",
        {"position": positions},
        indices=triangles,
    )
    offscreen = GPUOffScreen(1024, 1024)
    try:
        samples = []
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0.0, 0.0, 0.0, 0.0))
            shader.bind()
            shader.uniform_float("view_rect", (-1.0, -1.0, 512.0, 256.0))
            shader.uniform_float("color", (1.0, 0.05, 0.35, 0.5))
            gpu.state.blend_set("ALPHA")
            for _iteration in range(30):
                started = time.perf_counter()
                batch.draw(shader)
                samples.append((time.perf_counter() - started) * 1000.0)
            gpu.state.blend_set("NONE")
        print(
            "CACHED_DRAW",
            {
                "median_ms": round(statistics.median(samples), 3),
                "target_ms": 16.7,
            },
        )
    finally:
        offscreen.free()


def main():
    typical_positions, typical_triangles = benchmark_case("connected", False)
    benchmark_cached_draw(typical_positions, typical_triangles)
    stress_positions, stress_triangles = benchmark_case("fully_seamed", True)
    benchmark_cached_draw(stress_positions, stress_triangles)


if __name__ == "__main__":
    main()
