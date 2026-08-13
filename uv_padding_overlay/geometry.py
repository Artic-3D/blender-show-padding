"""UV island discovery and padding-band geometry generation.

This module deliberately contains no viewport or GPU state.  Its main entry
point accepts a live edit BMesh and returns indexed triangles in UV space.
"""

from __future__ import annotations

import math


_EPSILON = 1.0e-12
_UV_QUANTIZE = 100_000_000.0
_HASH_MASK = (1 << 64) - 1
_HASH_OFFSET = 1_469_598_103_934_665_603
_HASH_PRIME = 1_099_511_628_211


class _UnionFind:
    __slots__ = ("parent", "rank")

    def __init__(self, size):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item):
        parent = self.parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, first, second):
        root_a = self.find(first)
        root_b = self.find(second)
        if root_a == root_b:
            return
        rank = self.rank
        if rank[root_a] < rank[root_b]:
            root_a, root_b = root_b, root_a
        self.parent[root_b] = root_a
        if rank[root_a] == rank[root_b]:
            rank[root_a] += 1


class _EdgeUse:
    __slots__ = (
        "face_index",
        "bm_face_index",
        "loop_index",
        "start",
        "end",
        "start_key",
        "end_key",
        "orientation",
        "tangent",
        "normal",
        "root",
        "inner_start",
        "inner_end",
        "outer_start",
        "outer_end",
    )

    def __init__(
        self,
        face_index,
        bm_face_index,
        loop_index,
        start,
        end,
        start_key,
        end_key,
        orientation,
    ):
        self.face_index = face_index
        self.bm_face_index = bm_face_index
        self.loop_index = loop_index
        self.start = start
        self.end = end
        self.start_key = start_key
        self.end_key = end_key
        self.orientation = orientation
        self.tangent = None
        self.normal = None
        self.root = -1
        self.inner_start = -1
        self.inner_end = -1
        self.outer_start = -1
        self.outer_end = -1


class OverlayTopology:
    """Cached UV boundary references for coordinate-only refreshes."""

    __slots__ = ("boundary_uses", "mesh_counts", "visible_faces")

    def __init__(self, boundary_uses, mesh_counts, visible_faces):
        self.boundary_uses = tuple(boundary_uses)
        self.mesh_counts = tuple(mesh_counts)
        self.visible_faces = int(visible_faces)


def compute_band_width(margin_px, texture_resolution):
    """Return the outward UV distance for one side of a shared margin."""

    resolution = max(1, int(texture_resolution))
    return max(0.0, float(margin_px)) / (2.0 * resolution)


def _quantize(value):
    return int(round(float(value) * _UV_QUANTIZE))


def _uv_tuple(loop, uv_layer):
    uv = loop[uv_layer].uv
    return float(uv.x), float(uv.y)


def _endpoint_key(vertex_index, uv):
    return vertex_index, _quantize(uv[0]), _quantize(uv[1])


def _edge_key(loop, start, end):
    start_vertex = loop.vert.index
    end_vertex = loop.link_loop_next.vert.index
    start_uv = (_quantize(start[0]), _quantize(start[1]))
    end_uv = (_quantize(end[0]), _quantize(end[1]))
    if start_vertex < end_vertex:
        ordered = start_vertex, end_vertex, start_uv, end_uv
    elif start_vertex > end_vertex:
        ordered = end_vertex, start_vertex, end_uv, start_uv
    else:
        ordered_uv = sorted((start_uv, end_uv))
        ordered = start_vertex, end_vertex, ordered_uv[0], ordered_uv[1]
    return (loop.edge.index,) + ordered


def _face_orientation(uvs):
    area_twice = 0.0
    for index, current in enumerate(uvs):
        following = uvs[(index + 1) % len(uvs)]
        area_twice += current[0] * following[1] - following[0] * current[1]
    return 1 if area_twice >= 0.0 else -1


def _face_is_touched(face, uv_layer, uv_select_sync):
    sample_uv = face.loops[0][uv_layer]
    # Blender 5.3's provisional BMesh API no longer exposes per-loop UV
    # selection flags. Fall back to mesh element selection in that build;
    # synchronized selection uses the same path in every supported version.
    if uv_select_sync or not hasattr(sample_uv, "select"):
        if face.select:
            return True
        return any(loop.vert.select or loop.edge.select for loop in face.loops)
    for loop in face.loops:
        loop_uv = loop[uv_layer]
        if bool(getattr(loop_uv, "select", False)):
            return True
        if bool(getattr(loop_uv, "select_edge", False)):
            return True
    return False


def _mix_hash(current, value):
    current ^= int(value) & _HASH_MASK
    return (current * _HASH_PRIME) & _HASH_MASK


def _prepare_indices(bm):
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()


def state_signature(bm, uv_layer, selected_only=False, uv_select_sync=False):
    """Return a compact fingerprint of state that affects the overlay."""

    _prepare_indices(bm)
    fingerprint = _HASH_OFFSET
    fingerprint = _mix_hash(fingerprint, len(bm.verts))
    fingerprint = _mix_hash(fingerprint, len(bm.edges))
    fingerprint = _mix_hash(fingerprint, len(bm.faces))
    for face in bm.faces:
        fingerprint = _mix_hash(fingerprint, face.index)
        fingerprint = _mix_hash(fingerprint, int(face.hide))
        if face.hide:
            continue
        if selected_only:
            fingerprint = _mix_hash(
                fingerprint,
                int(_face_is_touched(face, uv_layer, uv_select_sync)),
            )
        for loop in face.loops:
            uv = loop[uv_layer].uv
            fingerprint = _mix_hash(fingerprint, loop.edge.index)
            fingerprint = _mix_hash(fingerprint, _quantize(uv.x))
            fingerprint = _mix_hash(fingerprint, _quantize(uv.y))
    return fingerprint


def _add_position(positions, point):
    positions.append((float(point[0]), float(point[1])))
    return len(positions) - 1


def _append_arc(
    positions,
    triangles,
    center_index,
    start_index,
    end_index,
    corner_segments=2,
    direction=0,
):
    """Append an indexed circular fan between existing radius endpoints.

    ``direction`` is +1 for counter-clockwise, -1 for clockwise, and zero for
    the shortest arc.  Arcs never exceed pi radians; invalid long arcs are
    ignored so folded or degenerate UVs cannot generate huge fans.
    """

    center = positions[center_index]
    start = positions[start_index]
    end = positions[end_index]
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    if direction > 0:
        delta = (end_angle - start_angle) % (2.0 * math.pi)
    elif direction < 0:
        delta = -((start_angle - end_angle) % (2.0 * math.pi))
    else:
        delta = (end_angle - start_angle + math.pi) % (2.0 * math.pi) - math.pi
    if abs(delta) <= _EPSILON or abs(delta) > math.pi + 1.0e-7:
        return
    radius = math.hypot(start[0] - center[0], start[1] - center[1])
    segments_per_quarter = max(1, int(corner_segments))
    arc_step = math.pi / (2.0 * segments_per_quarter)
    steps = max(1, int(math.ceil(abs(delta) / arc_step)))
    previous = start_index
    for step in range(1, steps + 1):
        if step == steps:
            current = end_index
        else:
            angle = start_angle + delta * (step / steps)
            current = _add_position(
                positions,
                (
                    center[0] + math.cos(angle) * radius,
                    center[1] + math.sin(angle) * radius,
                ),
            )
        triangles.append((center_index, previous, current))
        previous = current


def _append_open_cap(
    positions,
    triangles,
    use,
    at_start,
    band_width,
    corner_segments,
):
    tangent = use.tangent
    if at_start:
        center_index = use.inner_start
        outer_index = use.outer_start
        cap_point = (
            use.start[0] - tangent[0] * band_width,
            use.start[1] - tangent[1] * band_width,
        )
        cap_index = _add_position(positions, cap_point)
        _append_arc(
            positions,
            triangles,
            center_index,
            outer_index,
            cap_index,
            corner_segments=corner_segments,
            direction=0,
        )
    else:
        center_index = use.inner_end
        outer_index = use.outer_end
        cap_point = (
            use.end[0] + tangent[0] * band_width,
            use.end[1] + tangent[1] * band_width,
        )
        cap_index = _add_position(positions, cap_point)
        _append_arc(
            positions,
            triangles,
            center_index,
            cap_index,
            outer_index,
            corner_segments=corner_segments,
            direction=0,
        )


def _concave_miter(previous, following, band_width):
    """Return the intersection of two outward-offset boundary lines.

    Rectangular strips overlap at a concave turn if each edge keeps its own
    outer endpoint. Making both endpoints meet at the offset-line intersection
    partitions that corner between the two strips, preventing a shell from
    alpha-blending with itself while preserving the full outline width.
    """

    first = (
        previous.end[0] + previous.normal[0] * band_width,
        previous.end[1] + previous.normal[1] * band_width,
    )
    second = (
        following.start[0] + following.normal[0] * band_width,
        following.start[1] + following.normal[1] * band_width,
    )
    first_tangent = previous.tangent
    second_tangent = following.tangent
    denominator = (
        first_tangent[0] * second_tangent[1]
        - first_tangent[1] * second_tangent[0]
    )
    if abs(denominator) <= _EPSILON:
        return None
    delta = second[0] - first[0], second[1] - first[1]
    distance = (
        delta[0] * second_tangent[1]
        - delta[1] * second_tangent[0]
    ) / denominator
    result = (
        first[0] + first_tangent[0] * distance,
        first[1] + first_tangent[1] * distance,
    )
    if not (math.isfinite(result[0]) and math.isfinite(result[1])):
        return None
    return result


def _geometry_from_boundary_uses(
    boundary_uses,
    band_width,
    stats,
    corner_segments=2,
):
    """Create indexed bands from boundary uses with current UV coordinates."""

    if not boundary_uses or band_width <= 0.0:
        stats["islands"] = 0
        stats["boundary_edges"] = 0
        stats["triangles"] = 0
        return [], [], stats

    positions = []
    triangles = []
    inner_vertices = {}
    starts = {}
    ends = {}
    included_roots = set()

    def inner_index(root, endpoint, point):
        key = root, endpoint
        result = inner_vertices.get(key)
        if result is None:
            result = _add_position(positions, point)
            inner_vertices[key] = result
        return result

    for use_index, use in enumerate(boundary_uses):
        included_roots.add(use.root)
        use.inner_start = inner_index(use.root, use.start_key, use.start)
        use.inner_end = inner_index(use.root, use.end_key, use.end)
        use.outer_start = _add_position(
            positions,
            (
                use.start[0] + use.normal[0] * band_width,
                use.start[1] + use.normal[1] * band_width,
            ),
        )
        use.outer_end = _add_position(
            positions,
            (
                use.end[0] + use.normal[0] * band_width,
                use.end[1] + use.normal[1] * band_width,
            ),
        )
        triangles.append((use.inner_start, use.inner_end, use.outer_end))
        triangles.append((use.inner_start, use.outer_end, use.outer_start))
        starts.setdefault((use.root, use.start_key), []).append(use_index)
        ends.setdefault((use.root, use.end_key), []).append(use_index)

    joined_starts = set()
    joined_ends = set()
    endpoint_keys = set(starts)
    endpoint_keys.update(ends)
    for endpoint in endpoint_keys:
        incoming = ends.get(endpoint, ())
        outgoing = starts.get(endpoint, ())
        if len(incoming) != 1 or len(outgoing) != 1:
            continue
        incoming_index = incoming[0]
        outgoing_index = outgoing[0]
        previous = boundary_uses[incoming_index]
        following = boundary_uses[outgoing_index]
        if previous.orientation != following.orientation:
            continue
        cross = (
            previous.tangent[0] * following.tangent[1]
            - previous.tangent[1] * following.tangent[0]
        )
        if cross * previous.orientation > 1.0e-10:
            _append_arc(
                positions,
                triangles,
                previous.inner_end,
                previous.outer_end,
                following.outer_start,
                corner_segments=corner_segments,
                direction=previous.orientation,
            )
        elif cross * previous.orientation < -1.0e-10:
            miter = _concave_miter(previous, following, band_width)
            if miter is not None:
                positions[previous.outer_end] = miter
                positions[following.outer_start] = miter
        joined_ends.add(incoming_index)
        joined_starts.add(outgoing_index)

    for use_index, use in enumerate(boundary_uses):
        if use_index not in joined_starts:
            _append_open_cap(
                positions,
                triangles,
                use,
                True,
                band_width,
                corner_segments,
            )
        if use_index not in joined_ends:
            _append_open_cap(
                positions,
                triangles,
                use,
                False,
                band_width,
                corner_segments,
            )

    stats["islands"] = len(included_roots)
    stats["boundary_edges"] = len(boundary_uses)
    stats["triangles"] = len(triangles)
    return positions, triangles, stats


def rebuild_overlay_geometry(
    bm,
    topology,
    uv_layer,
    band_width,
    corner_segments=2,
):
    """Refresh coordinates from a previously discovered boundary topology."""

    band_width = max(0.0, float(band_width))
    stats = {
        "visible_faces": topology.visible_faces,
        "islands": 0,
        "boundary_edges": 0,
        "triangles": 0,
        "degenerate_edges": 0,
    }
    refreshed_uses = []
    orientations = {}
    _prepare_indices(bm)
    if (len(bm.verts), len(bm.edges), len(bm.faces)) != topology.mesh_counts:
        raise ReferenceError("Cached BMesh topology no longer matches the mesh")
    for use in topology.boundary_uses:
        if use.bm_face_index < 0 or use.bm_face_index >= len(bm.faces):
            raise ReferenceError("Cached BMesh face index is no longer valid")
        face = bm.faces[use.bm_face_index]
        loops = face.loops
        if use.loop_index < 0 or use.loop_index >= len(loops):
            raise ReferenceError("Cached BMesh loop index is no longer valid")
        loop = loops[use.loop_index]
        start = _uv_tuple(loop, uv_layer)
        end = _uv_tuple(loop.link_loop_next, uv_layer)
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length = math.hypot(delta_x, delta_y)
        if length <= _EPSILON:
            stats["degenerate_edges"] += 1
            continue
        orientation = orientations.get(use.bm_face_index)
        if orientation is None:
            orientation = _face_orientation(
                [_uv_tuple(face_loop, uv_layer) for face_loop in face.loops]
            )
            orientations[use.bm_face_index] = orientation
        tangent = delta_x / length, delta_y / length
        if orientation > 0:
            normal = tangent[1], -tangent[0]
        else:
            normal = -tangent[1], tangent[0]
        use.start = start
        use.end = end
        use.orientation = orientation
        use.tangent = tangent
        use.normal = normal
        refreshed_uses.append(use)
    return _geometry_from_boundary_uses(
        refreshed_uses,
        band_width,
        stats,
        corner_segments,
    )


def _build_overlay_geometry_with_template(
    bm,
    uv_layer,
    band_width,
    selected_only=False,
    uv_select_sync=False,
    corner_segments=2,
):
    """Build indexed UV-space triangles for all included shell boundaries.

    Returns ``(positions, triangles, stats, signature)``.  Adjacent faces are
    joined into an island only when both endpoints of their shared mesh edge
    occupy the same UV coordinates.  That makes explicit UV seams boundaries
    without relying on the mesh edge's seam flag.
    """

    band_width = max(0.0, float(band_width))
    _prepare_indices(bm)
    fingerprint = _HASH_OFFSET
    fingerprint = _mix_hash(fingerprint, len(bm.verts))
    fingerprint = _mix_hash(fingerprint, len(bm.edges))
    fingerprint = _mix_hash(fingerprint, len(bm.faces))

    visible_faces = []
    face_touched = []
    edge_groups = {}

    for face in bm.faces:
        fingerprint = _mix_hash(fingerprint, face.index)
        fingerprint = _mix_hash(fingerprint, int(face.hide))
        if face.hide or len(face.loops) < 3:
            continue
        local_face_index = len(visible_faces)
        visible_faces.append(face)
        touched = (
            _face_is_touched(face, uv_layer, uv_select_sync)
            if selected_only
            else False
        )
        face_touched.append(touched)
        if selected_only:
            fingerprint = _mix_hash(fingerprint, int(touched))

        loops = list(face.loops)
        uvs = [_uv_tuple(loop, uv_layer) for loop in loops]
        orientation = _face_orientation(uvs)
        for index, loop in enumerate(loops):
            start = uvs[index]
            end = uvs[(index + 1) % len(uvs)]
            fingerprint = _mix_hash(fingerprint, loop.edge.index)
            fingerprint = _mix_hash(fingerprint, _quantize(start[0]))
            fingerprint = _mix_hash(fingerprint, _quantize(start[1]))
            start_key = _endpoint_key(loop.vert.index, start)
            end_key = _endpoint_key(loop.link_loop_next.vert.index, end)
            use = _EdgeUse(
                local_face_index,
                face.index,
                index,
                start,
                end,
                start_key,
                end_key,
                orientation,
            )
            edge_groups.setdefault(_edge_key(loop, start, end), []).append(use)

    stats = {
        "visible_faces": len(visible_faces),
        "islands": 0,
        "boundary_edges": 0,
        "triangles": 0,
        "degenerate_edges": 0,
    }
    mesh_counts = (len(bm.verts), len(bm.edges), len(bm.faces))
    if not visible_faces or band_width <= 0.0:
        topology = OverlayTopology([], mesh_counts, len(visible_faces))
        return [], [], stats, fingerprint, topology

    union_find = _UnionFind(len(visible_faces))
    for uses in edge_groups.values():
        if len(uses) > 1:
            first_face = uses[0].face_index
            for use in uses[1:]:
                union_find.union(first_face, use.face_index)

    selected_roots = set()
    if selected_only:
        for face_index, touched in enumerate(face_touched):
            if touched:
                selected_roots.add(union_find.find(face_index))

    boundary_uses = []
    for uses in edge_groups.values():
        if len(uses) != 1:
            continue
        use = uses[0]
        use.root = union_find.find(use.face_index)
        if selected_only and use.root not in selected_roots:
            continue
        delta_x = use.end[0] - use.start[0]
        delta_y = use.end[1] - use.start[1]
        length = math.hypot(delta_x, delta_y)
        if length <= _EPSILON:
            stats["degenerate_edges"] += 1
            continue
        tangent = delta_x / length, delta_y / length
        if use.orientation > 0:
            normal = tangent[1], -tangent[0]
        else:
            normal = -tangent[1], tangent[0]
        use.tangent = tangent
        use.normal = normal
        boundary_uses.append(use)

    topology = OverlayTopology(boundary_uses, mesh_counts, len(visible_faces))
    positions, triangles, stats = _geometry_from_boundary_uses(
        boundary_uses,
        band_width,
        stats,
        corner_segments,
    )
    return positions, triangles, stats, fingerprint, topology


def build_overlay_geometry(
    bm,
    uv_layer,
    band_width,
    selected_only=False,
    uv_select_sync=False,
    corner_segments=2,
):
    """Build overlay geometry without exposing the refresh template."""

    result = _build_overlay_geometry_with_template(
        bm,
        uv_layer,
        band_width,
        selected_only,
        uv_select_sync,
        corner_segments,
    )
    return result[:4]


def build_overlay_geometry_with_template(
    bm,
    uv_layer,
    band_width,
    selected_only=False,
    uv_select_sync=False,
    corner_segments=2,
):
    """Build overlay geometry and return a coordinate-refresh template."""

    return _build_overlay_geometry_with_template(
        bm,
        uv_layer,
        band_width,
        selected_only,
        uv_select_sync,
        corner_segments,
    )
