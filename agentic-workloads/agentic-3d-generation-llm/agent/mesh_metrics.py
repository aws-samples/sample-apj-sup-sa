# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Runs inside Blender (imported by blender_harness.py): mesh-quality counts for the scene the
# generated script built, measured on evaluated (modifiers applied), world-space meshes.
from collections import deque

import bmesh
import bpy

DUPLICATE_DISTANCE = 1e-5  # metres: vertices closer than this count as overlapping duplicates
ZERO_AREA = 1e-12          # square metres
MAX_FACES = 2_000_000      # skip the per-face checks on huge meshes to stay inside the time limit


def _flipped_faces(bm) -> int:
    """Faces whose winding disagrees with the majority of their connected piece."""
    seen = set()
    flipped = 0
    for seed in bm.faces:
        if seed.index in seen:
            continue
        # Breadth-first walk: a neighbour across a non-contiguous edge has the opposite winding.
        side = {seed.index: False}
        queue = deque([seed])
        while queue:
            face = queue.popleft()
            for edge in face.edges:
                if len(edge.link_faces) != 2:
                    continue
                other = edge.link_faces[0] if edge.link_faces[1] == face else edge.link_faces[1]
                if other.index in side:
                    continue
                side[other.index] = side[face.index] ^ (not edge.is_contiguous)
                queue.append(other)
        seen.update(side)
        flips = sum(side.values())
        flipped += min(flips, len(side) - flips)
    return flipped


def _pieces(bm) -> int:
    """Connected pieces (islands) joined by shared vertices."""
    parent = list(range(len(bm.verts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for edge in bm.edges:
        a, b = find(edge.verts[0].index), find(edge.verts[1].index)
        if a != b:
            parent[a] = b
    return len({find(v.index) for v in bm.verts})


def _hole_loops(bm) -> int:
    """Open boundaries, counted as loops of boundary edges (edges with exactly one face)."""
    boundary = {e for e in bm.edges if len(e.link_faces) == 1}
    loops = 0
    while boundary:
        loops += 1
        stack = [boundary.pop()]
        while stack:
            edge = stack.pop()
            for vert in edge.verts:
                for nxt in vert.link_edges:
                    if nxt in boundary:
                        boundary.remove(nxt)
                        stack.append(nxt)
    return loops


def measure() -> dict:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    totals = {
        "objects": 0, "pieces": 0, "vertices": 0, "faces": 0, "triangles": 0,
        "holes": 0, "boundaryEdges": 0, "nonManifoldEdges": 0, "flippedFaces": 0,
        "looseVertices": 0, "looseEdges": 0, "zeroAreaFaces": 0, "duplicateVertices": 0,
    }
    skipped = False
    for obj in bpy.context.scene.objects:
        if obj.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            continue
        bm = bmesh.new()
        evaluated = obj.evaluated_get(depsgraph)
        try:
            # to_mesh() works for curves, text and metaballs too (bmesh.from_object only takes meshes),
            # and includes modifiers, the same geometry the GLB exporter writes.
            try:
                mesh = evaluated.to_mesh()
            except RuntimeError:
                skipped = True
                continue
            if mesh is None:
                continue
            bm.from_mesh(mesh)
            bm.transform(evaluated.matrix_world)
            bm.verts.ensure_lookup_table()
            bm.verts.index_update()
            bm.faces.index_update()
            if not bm.verts:
                continue
            totals["objects"] += 1
            totals["vertices"] += len(bm.verts)
            totals["faces"] += len(bm.faces)
            totals["triangles"] += sum(len(f.verts) - 2 for f in bm.faces)
            totals["boundaryEdges"] += sum(1 for e in bm.edges if len(e.link_faces) == 1)
            totals["nonManifoldEdges"] += sum(1 for e in bm.edges if len(e.link_faces) > 2)
            totals["looseVertices"] += sum(1 for v in bm.verts if not v.link_faces)
            totals["looseEdges"] += sum(1 for e in bm.edges if not e.link_faces)
            totals["zeroAreaFaces"] += sum(1 for f in bm.faces if f.calc_area() < ZERO_AREA)
            totals["duplicateVertices"] += len(bmesh.ops.find_doubles(bm, verts=bm.verts, dist=DUPLICATE_DISTANCE)["targetmap"])
            totals["pieces"] += _pieces(bm)
            if len(bm.faces) <= MAX_FACES:
                totals["holes"] += _hole_loops(bm)
                totals["flippedFaces"] += _flipped_faces(bm)
            else:
                skipped = True
        except Exception:  # noqa: BLE001 - one odd object shouldn't lose the whole report
            skipped = True
        finally:
            bm.free()
            evaluated.to_mesh_clear()
    totals["watertight"] = totals["objects"] > 0 and totals["boundaryEdges"] == 0 and totals["nonManifoldEdges"] == 0
    if skipped:
        totals["partial"] = True
    return totals
