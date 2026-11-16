"""Hole marks: crossings with wireframes, entry and exit of block models."""

import numpy as np

from app.geo.intersect import BlockTarget, MeshTarget, hole_segments, new_budget
from app.geo.trajectory import build_trajectory


def vertical(x=0.0, y=0.0, z=100.0, td=100.0):
    return build_trajectory("H1", x, y, z, td, None, -90.0, [])


def test_wireframe_crossings_at_their_depths():
    traj = vertical()
    # Two horizontal squares at z = 70 and z = 40 (two triangles each, sharing an edge under the hole).
    v = np.array([[-5, -5, 70], [5, -5, 70], [5, 5, 70], [-5, 5, 70],
                  [-5, -5, 40], [5, -5, 40], [5, 5, 40], [-5, 5, 40]], float)
    t = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]])
    points, md = hole_segments(traj)
    hits = MeshTarget(v, t).hits(points, md)
    assert [round(h.depth, 3) for h in hits] == [30.0, 60.0]  # the shared diagonal gives one hit, not two
    assert np.allclose(hits[0].xyz, (0.0, 0.0, 70.0))
    assert MeshTarget(v + [100, 0, 0], t).hits(points, md) == []  # beside the hole


def test_block_model_entry_and_exit():
    traj = vertical()
    # A column of 10 m blocks from z = 20 to z = 60 under the collar.
    centres = np.array([[0, 0, 25], [0, 0, 35], [0, 0, 45], [0, 0, 55]], float)
    sizes = np.full((4, 3), 10.0)
    target = BlockTarget(centres, sizes, origin=[-5, -5, 20], parent=[10, 10, 10], counts=[1, 1, 4])
    hits = target.hits(traj, new_budget())
    assert [h.event for h in hits] == ["in", "out"]
    assert abs(hits[0].depth - 40) < 0.002 and abs(hits[1].depth - 80) < 0.002


def test_tiny_blocks_beyond_the_budget_are_skipped():
    traj = vertical()
    target = BlockTarget(np.array([[0, 0, 50.0]]), np.full((1, 3), 0.5), origin=[-0.25, -0.25, 49.75], parent=[0.5] * 3, counts=[1, 1, 1])
    budget = [10, 0]
    assert target.hits(traj, budget) == []
    assert budget[1] == 1
