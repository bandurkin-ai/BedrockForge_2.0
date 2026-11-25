"""The isosurface in bricks (подшаг 19.5; ТЗ 8.9.3 steps 1–7, Т8.9-1 geometry; ADR 0026 решения 12, 13): the sphere
(vertices, volume, χ = 2, a closed oriented manifold), the plane to 1e-6·h, bricks in any order / chunks / threads
bit for bit, no seams between bricks, NaN → +H, an empty field, the store of brick values, the exits of the band."""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from bf_core.geo import checks, meshes, solids
from bf_core.geo.iso import IsoError, IsoMesh, isosurface

CORE = Path(__file__).resolve().parents[1]
R = 50.0
BOX = ((-60.0, -60.0, -60.0), (60.0, 60.0, 60.0))
UTM = np.array([512_340.0, 6_071_250.0, 310.0])


def sphere(p: np.ndarray) -> np.ndarray:
    return np.linalg.norm(p, axis=1) - R


def run(field=sphere, box=BOX, h=2.0, **kw) -> IsoMesh:
    return isosurface(field, box[0], box[1], h, **kw)


def euler(m: IsoMesh) -> int:
    t = m.triangles
    e = np.unique(np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1), axis=0)
    return len(m.vertices) - len(e) + len(t)


def assert_closed(m: IsoMesh) -> None:
    """Every edge in exactly two triangles, the normals agree and are outward (the volume is positive)."""
    assert len(m.triangles) > 0
    assert meshes.edge_summary(m.triangles, len(m.vertices)) == (0, 0)
    assert checks.orientation_conflicts(m.triangles, len(m.vertices)) == 0
    local = m.vertices - m.vertices.mean(axis=0)
    assert solids.signed_volume(local, m.triangles) > 0.0


def same(a: IsoMesh, b: IsoMesh) -> bool:
    return a.vertices.tobytes() == b.vertices.tobytes() and a.triangles.tobytes() == b.triangles.tobytes()


# --- Т8.9-1: the sphere --------------------------------------------------------------------------------------


@pytest.mark.parametrize("levels", [1, 2, 3])
def test_sphere_geometry(levels: int) -> None:
    h = 2.0
    m = run(h=h, levels=levels)
    r = np.linalg.norm(m.vertices, axis=1)
    assert np.abs(r - R).max() <= 0.25 * h
    assert_closed(m)
    assert euler(m) == 2
    vol = solids.signed_volume(m.vertices, m.triangles)
    assert vol == pytest.approx(4.0 / 3.0 * math.pi * R**3, rel=0.01)
    assert m.vertices.dtype == np.float64 and m.triangles.dtype == np.int32


def test_sphere_in_project_coordinates() -> None:
    """UTM coordinates: the vertices are float64 in the coordinates of the project, not of the grid."""
    c = UTM

    def field(p: np.ndarray) -> np.ndarray:
        return np.linalg.norm(p - c, axis=1) - R

    m = isosurface(field, c - 60.0, c + 60.0, 2.0)
    assert np.abs(np.linalg.norm(m.vertices - c, axis=1) - R).max() <= 0.5
    assert_closed(m)
    assert euler(m) == 2


def test_nodes_exactly_on_the_surface() -> None:
    """R = 50 on a lattice of 2 m has nodes with f = 0 exactly (30–40–50): +1e-9·h, no hole is left."""
    m = run(h=2.0, levels=2)
    assert meshes.edge_summary(m.triangles, len(m.vertices)) == (0, 0)
    m1 = run(h=1.0, levels=3)  # the nodes on the surface again, bricks of 8 m
    assert meshes.edge_summary(m1.triangles, len(m1.vertices)) == (0, 0)
    assert euler(m1) == 2


# --- the plane ------------------------------------------------------------------------------------------------


def test_plane_to_1e6_h() -> None:
    n = np.array([0.36, -0.48, 0.8])
    n /= np.linalg.norm(n)
    h = 2.0

    def field(p: np.ndarray) -> np.ndarray:
        return (p - np.array([3.1, -2.7, 1.3])) @ n

    m = run(field, h=h, levels=2)
    f = field(m.vertices)
    wall = np.abs(m.vertices).max(axis=1) > 60.0 - 1e-9  # the closing of the body at the box
    on_plane = np.abs(f) <= 1e-6 * h
    assert on_plane.sum() > 1000
    assert np.all(on_plane | (np.abs(m.vertices).max(axis=1) > 59.0) | wall)
    # the body (f < 0) is the half of the box: closed beyond the box by +H
    assert_closed(m)


# --- bit for bit: bricks in any order, chunks, threads ----------------------------------------------------------


def test_bricks_in_any_order_chunks_and_threads_bit_for_bit() -> None:
    ref = run(levels=2)
    rng = np.random.default_rng(7)

    def shuffled(lin: np.ndarray) -> np.ndarray:
        return rng.permutation(lin)

    for kw in (
        {"brick_order": shuffled, "chunk_bricks": 1},
        {"brick_order": shuffled, "chunk_bricks": 7},
        {"brick_order": lambda lin: lin[::-1], "chunk_bricks": 50},
        {"threads": 4, "chunk_bricks": 13, "brick_order": shuffled},
        {"threads": 3},
    ):
        assert same(run(levels=2, **kw), ref), kw  # type: ignore[arg-type]


def test_a_field_with_ambiguous_cells_is_closed_and_bit_for_bit() -> None:
    """A gyroid-like field: saddles in the cells (the ambiguous cases of Lewiner, centre vertices) at the faces of
    bricks, whatever the size of the brick."""

    def field(p: np.ndarray) -> np.ndarray:
        q = p * (2.0 * math.pi / 37.0)
        return (
            np.sin(q[:, 0]) * np.cos(q[:, 1])
            + np.sin(q[:, 1]) * np.cos(q[:, 2])
            + np.sin(q[:, 2]) * np.cos(q[:, 0])
            - 0.2
        )

    ref = run(field, h=1.5, levels=2)
    assert meshes.edge_summary(ref.triangles, len(ref.vertices)) == (0, 0)
    assert checks.orientation_conflicts(ref.triangles, len(ref.vertices)) == 0
    for levels in (1, 3):
        other = run(field, h=1.5, levels=levels)
        assert meshes.edge_summary(other.triangles, len(other.vertices)) == (0, 0)
        assert checks.orientation_conflicts(other.triangles, len(other.vertices)) == 0
    rng = np.random.default_rng(3)
    assert same(run(field, h=1.5, levels=2, brick_order=rng.permutation, chunk_bricks=11, threads=2), ref)


def noise(p: np.ndarray) -> np.ndarray:
    x = np.sin(p @ np.array([12.9898, 78.233, 37.719])) * 43758.5453
    return (x - np.floor(x)) - 0.5


def test_white_noise_every_ambiguous_case_closed_and_bit_for_bit() -> None:
    """Values at random at every node: every case of the table, the centre vertices of Lewiner, nodes with a value
    next to 0 — the surface is closed and oriented in any size of the brick, and the same bits in any order."""
    box = ((-12.0,) * 3, (12.0,) * 3)
    ref = run(noise, box, h=1.0, levels=2)
    assert len(ref.triangles) > 20000
    assert meshes.edge_summary(ref.triangles, len(ref.vertices)) == (0, 0)
    assert checks.orientation_conflicts(ref.triangles, len(ref.vertices)) == 0
    for levels in (1, 3):
        other = run(noise, box, h=1.0, levels=levels)
        assert meshes.edge_summary(other.triangles, len(other.vertices)) == (0, 0)
        assert checks.orientation_conflicts(other.triangles, len(other.vertices)) == 0
    rng = np.random.default_rng(11)
    assert same(run(noise, box, h=1.0, levels=2, brick_order=rng.permutation, chunk_bricks=5, threads=3), ref)


def test_every_edge_of_a_multi_brick_surface_is_in_two_triangles() -> None:
    """No seams: bricks of 2 cells (L = 1), so the surface passes through thousands of faces of bricks."""
    m = run(levels=1, h=2.0)
    assert m.stats.meshed_bricks > 2000
    assert_closed(m)
    assert euler(m) == 2


# --- the border of the model, NaN, an empty field ---------------------------------------------------------------


def test_nan_beyond_the_patches_is_plus_h() -> None:
    """f = −1 inside a box of 60 m and NaN beyond: the body is closed where the field ends (8.9.3 step 6)."""

    def field(p: np.ndarray) -> np.ndarray:
        inside = (np.abs(p) < 30.0).all(axis=1)
        return np.where(inside, -1.0, np.nan)

    m = run(field, h=2.0, levels=2)
    assert_closed(m)
    assert euler(m) == 2
    # the last node with a value is at 28; the next one is +H = 8: the surface at 28 + 1/9 of a cell of 2 m
    assert np.abs(m.vertices).max() == pytest.approx(28.0 + 2.0 / 9.0)
    vol = solids.signed_volume(m.vertices, m.triangles)
    assert vol == pytest.approx((2 * (28.0 + 2.0 / 9.0)) ** 3, rel=1e-3)


def test_a_body_that_reaches_the_box_is_closed_beyond_it() -> None:
    m = run(lambda p: -np.ones(len(p)), h=2.0, levels=2)  # inside everywhere in the box
    assert_closed(m)
    assert np.abs(m.vertices).max() >= 60.0  # one cell beyond the box
    assert solids.signed_volume(m.vertices, m.triangles) >= 120.0**3


def test_no_crossing_is_an_empty_result_not_an_error() -> None:
    for f in (lambda p: np.ones(len(p)), lambda p: np.full(len(p), np.nan), lambda p: np.full(len(p), np.inf)):
        m = run(f, h=2.0)
        assert m.empty
        assert m.vertices.shape == (0, 3) and m.triangles.shape == (0, 3)
        assert m.stats.meshed_bricks == 0


def test_a_zero_field_is_not_a_surface_on_the_nodes() -> None:
    """f = 0 at a node becomes +1e-9·h: a plane through the nodes is one surface, closed, no hole."""
    m = run(lambda p: p[:, 2] + 0.0, h=2.0, levels=2)  # z = 0 passes through the nodes of the grid
    assert_closed(m)
    on = np.abs(m.vertices[:, 2]) < 1e-6
    assert on.sum() > 1000


# --- the store of the values of bricks ---------------------------------------------------------------------------


class DictCache:
    def __init__(self) -> None:
        self.data: dict[tuple[int, int, int], np.ndarray] = {}
        self.puts = 0

    def get(self, index: tuple[int, int, int]) -> np.ndarray | None:
        return self.data.get(index)

    def put(self, index: tuple[int, int, int], values: np.ndarray) -> None:
        self.puts += 1
        self.data[index] = values


def test_the_store_gives_the_same_surface_without_the_field() -> None:
    ref = run(levels=2)
    cache = DictCache()
    first = run(levels=2, cache=cache)
    assert same(first, ref)
    assert cache.puts == len(cache.data) > 0 and first.stats.cache_hits == 0

    def no_field(p: np.ndarray) -> np.ndarray:
        raise AssertionError("the bricks are in the store")

    # the coarse grid is the field's (it is not stored): only the bricks come from the store
    called = []

    def counting(p: np.ndarray) -> np.ndarray:
        called.append(len(p))
        return sphere(p)

    second = run(counting, levels=2, cache=cache)
    assert same(second, ref)
    assert second.stats.nodes == 0 and second.stats.cache_hits == first.stats.band_bricks + first.stats.extra_bricks
    assert sum(called) == second.stats.coarse_nodes
    # half of the store gone: the same bits (a brick is the same from the store and from the field)
    keys = sorted(cache.data)
    for k in keys[::2]:
        del cache.data[k]
    third = run(levels=2, cache=cache)
    assert same(third, ref)
    assert 0 < third.stats.nodes < first.stats.nodes


# --- the band and its exits ----------------------------------------------------------------------------------------


def test_the_surface_that_leaves_the_band_brings_the_next_bricks_in() -> None:
    """With no dilation of the band only the cells with a change of sign are bricks; the surface that crosses a face
    of a brick towards a cell without a change of sign (a ball cuts the face of a cell between its corners) brings
    that cell in (step 5). The sphere is one of those that do."""
    c = np.array([0.5, 0.2, 0.1])

    def field(p: np.ndarray) -> np.ndarray:
        return np.linalg.norm(p - c, axis=1) - 51.3

    plain = run(field, levels=2, dilate=0)
    wide = run(field, levels=2, dilate=1)
    assert plain.stats.extra_bricks > 0
    assert wide.stats.band_bricks > plain.stats.band_bricks
    assert same(plain, wide)
    assert_closed(plain)
    assert euler(plain) == 2


def test_near_adds_the_cells_close_to_the_surface() -> None:
    a = run(levels=2, dilate=0)
    b = run(levels=2, dilate=0, near=1.5)
    assert b.stats.band_bricks > a.stats.band_bricks
    assert same(a, b)


# --- progress, cancel, errors ------------------------------------------------------------------------------------


def test_tick_is_monotone_in_0_1_and_may_cancel() -> None:
    seen: list[float] = []
    run(levels=2, tick=lambda f, msg: seen.append(f))
    assert seen[0] == 0.0 and seen[-1] == 1.0 and seen == sorted(seen) and all(0.0 <= f <= 1.0 for f in seen)

    class Cancel(Exception):
        pass

    def stop(fraction: float, message: str) -> None:
        if fraction > 0.2:
            raise Cancel

    with pytest.raises(Cancel):
        run(levels=2, tick=stop)


def test_bad_parameters_and_a_bad_field_are_errors_in_russian() -> None:
    for kw in ({"h": 0.0}, {"levels": 0}, {"levels": 6}, {"box": ((0, 0, 0), (-1, 1, 1))}):
        with pytest.raises(IsoError) as e:
            run(**kw)  # type: ignore[arg-type]
        assert e.value.code in {"bad_param", "bad_box"}
        assert e.value.hint and any("а" <= ch <= "я" for ch in e.value.message.lower())
    with pytest.raises(IsoError) as e:
        run(lambda p: np.zeros(3))
    assert e.value.code == "bad_field" and e.value.as_dict()["hint"]
    with pytest.raises(IsoError) as e:
        run(box=((0, 0, 0), (1e7, 1e7, 1e7)), h=0.01, levels=2)
    assert e.value.code == "iso_too_large"


def test_numba_and_skimage_are_not_loaded_by_the_import() -> None:
    code = "import bf_core.geo.iso, sys; raise SystemExit(3 if {'numba', 'skimage'} & set(sys.modules) else 0)"
    run_ = subprocess.run([sys.executable, "-c", code], cwd=CORE, capture_output=True, text=True, timeout=120)
    assert run_.returncode == 0, run_.stderr[-300:]
