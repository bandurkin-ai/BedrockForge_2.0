"""Merge, LOD and the legend of the block shell (подшаг 17.23; ADR 0023 §11, decisions 16–17; ТЗ 8.7.6):
Т8.7-9 (100 × 100 × 100 of one class: 60 000 faces → 6 rectangles), the area kept exactly and the class never
mixed (a raster of the sub-cells before and after), a greedy case by hand, a variable step and a turned grid,
the same bits with 1, 2, 4 and 8 threads, LOD — the mean over the volume and the mode, the faces within the limit,
the decision before the faces are built — and the 256 buckets of the legend."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import lod, shell
from bf_core.blocks.lod import coarse_grid, coarsen, view
from bf_core.blocks.schema import GridV2
from bf_core.blocks.shell import Faces, exposed
from bf_core.blocks.shell_merge import (
    NO_CLASS,
    break_bins,
    break_class,
    merge_faces,
    scale_bins,
    scale_class,
)
from tests.test_blocks_shell import make_grid, random_model, same


def ones(n: int) -> np.ndarray:
    return np.ones(n, dtype=bool)


def raster(f: Faces) -> dict[tuple[int, int, int, int], int]:
    """{(a, plane, u, v): class} of every sub-cell of every face; a cell taken twice fails the test."""
    out: dict[tuple[int, int, int, int], int] = {}
    for a, k, g in zip(f.a.tolist(), f.k.tolist(), f.g.tolist(), strict=True):
        for u in range(g[1], g[2]):
            for v in range(g[3], g[4]):
                assert (a, g[0], u, v) not in out
                out[(a, g[0], u, v)] = k
    return out


def check_order(f: Faces) -> None:
    key = list(zip(f.a.tolist(), f.g[:, 0].tolist(), f.k.tolist(), f.g[:, 3].tolist(), f.g[:, 1].tolist(), strict=True))
    assert key == sorted(key)


# --- Т8.7-9 and the merge by hand --------------------------------------------------------------------------------


def test_t87_9_a_full_model_of_100_cubed_merges_into_6_rectangles() -> None:
    grid = make_grid((100, 100, 100), size=(1.0, 1.0, 1.0))
    n = 100**3
    f = exposed(grid, np.arange(n, dtype=np.uint64), None, None, ones(n), np.zeros(n, np.uint16))
    assert len(f) == 60_000
    m = merge_faces(grid, f)
    assert len(m) == 6 and m.a.tolist() == [0, 1, 2, 3, 4, 5]
    assert (m.h == 50.0).all()
    for i, a in enumerate(m.a.tolist()):
        axis = a >> 1
        assert m.c[i, axis] == (100.0 if a & 1 else 0.0)
        assert m.g[i, 1:].tolist() == [0, 100, 0, 100] and m.g[i, 0] == (100 if a & 1 else 0)
        assert [m.c[i, x] for x in range(3) if x != axis] == [50.0, 50.0]
    check_order(m)


def test_a_ring_of_unit_blocks_merges_greedily_along_u_then_along_v() -> None:
    grid = make_grid((3, 3, 1), size=(1.0, 1.0, 1.0))
    p = np.array([q for q in range(9) if q != 4], dtype=np.uint64)
    f = exposed(grid, p, None, None, ones(8), shell=False)
    top = merge_faces(grid, f)
    top = Faces(*(getattr(top, n)[top.a == 5] for n in ("c", "h", "a", "k", "row", "g")))
    # rows of the top: [0–3 × 0–1], [0–1 × 1–2] and [2–3 × 1–2] (apart), [0–3 × 2–3]: u first, then v (no join)
    assert sorted(map(tuple, top.g[:, 1:].tolist())) == [(0, 1, 1, 2), (0, 3, 0, 1), (0, 3, 2, 3), (2, 3, 1, 2)]
    assert int(((top.g[:, 2] - top.g[:, 1]) * (top.g[:, 4] - top.g[:, 3])).sum()) == 8


def test_a_column_joins_along_v_after_the_runs_along_u() -> None:
    grid = make_grid((2, 4, 1), size=(1.0, 1.0, 1.0))
    f = exposed(grid, np.arange(8, dtype=np.uint64), None, None, ones(8))
    m = merge_faces(grid, f)
    top = m.g[m.a == 5]
    assert top.shape[0] == 1 and top[0, 1:].tolist() == [0, 2, 0, 4]
    # the faces on x = 0 and x = 2 are 4 × 1 columns (joined along v): one rectangle each
    assert (m.a == 0).sum() == 1 and (m.a == 1).sum() == 1


def test_nothing_to_join_keeps_the_faces_and_their_bits() -> None:
    grid = make_grid((4, 4, 1), size=(2.5, 2.5, 2.5))
    p = np.arange(16, dtype=np.uint64)
    klass = (((p % 4) + (p // 4)) % 2).astype(np.uint16)  # a chess board: no two neighbours of one class
    f = exposed(grid, p, None, None, ones(16), klass, shell=False)
    assert same(f, merge_faces(grid, f))


def test_empty_and_single_face_are_returned_as_they_are() -> None:
    grid = make_grid((1, 1, 1))
    assert len(merge_faces(grid, exposed(grid, np.zeros(0, np.uint64), None, None, ones(0)))) == 0
    one = shell.faces_of_block(grid, 0)
    assert len(merge_faces(grid, one)) == 6


# --- the area, the classes, the bits -----------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(6))
def test_area_and_class_are_kept_exactly_in_random_models(seed: int) -> None:
    grid, p, s, w, passed = random_model(seed)
    rng = np.random.default_rng(seed + 100)
    klass = rng.integers(0, 3, len(p)).astype(np.uint16)
    f = exposed(grid, p, s, w, passed, klass)
    m = merge_faces(grid, f)
    assert len(m) < len(f)
    assert raster(m) == raster(f)  # the same sub-cells in the same planes with the same classes, none twice
    area = lambda x: (x.g[:, 2] - x.g[:, 1]) * (x.g[:, 4] - x.g[:, 3])  # noqa: E731
    assert int(area(m).sum()) == int(area(f).sum())
    assert (m.h * 2 > 0).all()
    check_order(m)
    # a merged face is a rectangle of its own geometry: c, h follow from g
    d = np.asarray(grid.sub.div)
    sc = np.asarray(grid.regular()) / d
    for i in range(len(m)):
        a = int(m.a[i]) >> 1
        u, v = (a + 1) % 3, (a + 2) % 3
        assert m.c[i, a] == m.g[i, 0] / d[a] * grid.regular()[a]
        assert abs(m.c[i, u] - (m.g[i, 1] + m.g[i, 2]) / 2 * sc[u]) < 1e-9
        assert abs(m.h[i, 1] - (m.g[i, 4] - m.g[i, 3]) / 2 * sc[v]) < 1e-9


def test_classes_do_not_mix_even_when_they_touch_in_one_plane() -> None:
    grid = make_grid((6, 1, 1), size=(1.0, 1.0, 1.0))
    klass = np.array([1, 1, 2, 2, 1, 1], dtype=np.uint16)
    f = exposed(grid, np.arange(6, dtype=np.uint64), None, None, ones(6), klass, shell=False)
    m = merge_faces(grid, f)
    top = m.g[m.a == 5]
    assert sorted(top[:, 1:3].tolist()) == [[0, 2], [2, 4], [4, 6]]
    assert sorted(m.k[m.a == 5].tolist()) == [1, 1, 2]


def test_the_same_bits_with_1_2_4_and_8_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "STRIP_ROWS", 7)
    grid, p, s, w, passed = random_model(11)
    klass = (np.arange(len(p)) % 3).astype(np.uint16)
    one = merge_faces(grid, exposed(grid, p, s, w, passed, klass, threads=1))
    assert len(one) > 50
    for t in (2, 4, 8):
        assert same(one, merge_faces(grid, exposed(grid, p, s, w, passed, klass, threads=t)))
        assert same(one, view(grid, p, s, w, passed, klass, threads=t).faces)


def test_a_variable_step_merges_with_the_coordinates_of_its_edges() -> None:
    steps = {"x": [1.0, 2.5, 4.0], "y": [3.0, 0.5], "z": [2.0, 2.0]}
    grid = GridV2.of({"corner": [0, 0, 0], "counts": [3, 2, 2], "tensors": steps, "order": "F"})
    n = 12
    f = exposed(grid, np.arange(n, dtype=np.uint64), None, None, ones(n))
    m = merge_faces(grid, f)
    assert len(f) > 6 and len(m) == 6
    ex = [np.concatenate(([0.0], np.cumsum(steps[a]))) for a in "xyz"]
    for i in range(6):
        a = int(m.a[i]) >> 1
        assert m.c[i, a] == ex[a][-1 if m.a[i] & 1 else 0]
        u, v = (a + 1) % 3, (a + 2) % 3
        assert m.c[i, u] == (ex[u][0] + ex[u][-1]) / 2 and m.h[i, 0] == (ex[u][-1] - ex[u][0]) / 2
        assert m.c[i, v] == (ex[v][0] + ex[v][-1]) / 2 and m.h[i, 1] == (ex[v][-1] - ex[v][0]) / 2


def test_a_turned_grid_merges_in_local_axes() -> None:
    grid = make_grid((8, 8, 8), size=(2.0, 2.0, 2.0), rotation=30.0)
    f = exposed(grid, np.arange(512, dtype=np.uint64), None, None, ones(512))
    assert len(f) == 384 and len(merge_faces(grid, f)) == 6


# --- LOD: coarse cells ------------------------------------------------------------------------------------------


def test_coarse_grid_regular_exact_and_cut_where_the_model_ends() -> None:
    g = coarse_grid(make_grid((4, 4, 2), size=(0.1, 3.0, 5.0)), 1)
    assert g.counts == (2, 2, 1) and g.size == (0.2, 6.0, 10.0) and g.tensors is None
    cut = coarse_grid(make_grid((5, 3, 2), size=(10.0, 10.0, 10.0)), 1)
    assert cut.counts == (3, 2, 1)
    assert [list(t) for t in cut.tensors or ()] == [[20.0, 20.0, 10.0], [20.0, 10.0], [20.0]]
    var = GridV2.of({"corner": [0, 0, 0], "counts": [3, 1, 1], "order": "F",
                     "tensors": {"x": [1.0, 2.0, 4.0], "y": [1.0], "z": [1.0]}})  # fmt: skip
    assert list(coarse_grid(var, 1).tensors[0]) == [3.0, 4.0]  # type: ignore[index]


def test_mean_over_the_volume_counts_sub_blocks_by_their_size() -> None:
    grid = make_grid((2, 1, 1), div=(2, 1, 1))
    p = np.array([0, 1, 1], dtype=np.uint64)
    s = np.array([[0, 0, 0], [0, 0, 0], [1, 0, 0]], dtype=np.uint8)
    w = np.array([[2, 1, 1], [1, 1, 1], [1, 1, 1]], dtype=np.uint8)
    c = coarsen(grid, p, s, w, ones(3), np.array([10, 0, 4], np.uint16), 1)
    assert c.p.tolist() == [0] and c.klass.tolist() == [6]  # (2·10 + 1·0 + 1·4) / 4
    assert c.grid.counts == (1, 1, 1) and c.level == 1


def test_mean_takes_only_the_rows_that_pass_and_skips_the_missing_class() -> None:
    grid = make_grid((2, 2, 1))
    klass = np.array([2, 100, NO_CLASS, 6], dtype=np.uint16)
    c = coarsen(grid, np.arange(4, dtype=np.uint64), None, None, np.array([1, 0, 1, 1], bool), klass, 1)
    assert c.klass.tolist() == [4]  # (2 + 6) / 2; the row that does not pass and the missing one do not count
    none = coarsen(grid, np.arange(4, dtype=np.uint64), None, None, np.array([0, 0, 1, 0], bool), klass, 1)
    assert none.klass.tolist() == [NO_CLASS]  # the only row has no class: the cell has none, not 0
    assert coarsen(grid, np.arange(4, dtype=np.uint64), None, None, np.zeros(4, bool), klass, 1).p.size == 0


def test_mean_of_the_values_and_the_class_of_the_mean() -> None:
    grid = make_grid((2, 2, 1))
    values = np.array([1.0, 3.0, np.nan, 8.0])
    c = coarsen(grid, np.arange(4, dtype=np.uint64), None, None, ones(4), None, 1, values=values,
                klass_of=lambda v: scale_class(v, 0.0, 16.0))  # fmt: skip
    assert c.klass.tolist() == [int(np.floor(256 * (4.0 / 16.0)))]  # mean of 1, 3, 8 = 4 (NaN does not count)
    allnan = coarsen(grid, np.arange(4, dtype=np.uint64), None, None, ones(4), None, 1, values=np.full(4, np.nan),
                     klass_of=lambda v: scale_class(v, 0.0, 16.0))  # fmt: skip
    assert allnan.klass.tolist() == [NO_CLASS]
    with pytest.raises(ApiError):
        coarsen(grid, np.arange(4, dtype=np.uint64), None, None, ones(4), None, 1, values=values)


def test_mode_is_the_class_with_the_most_volume_and_the_lower_code_on_a_tie() -> None:
    grid = make_grid((2, 2, 2))
    p = np.arange(8, dtype=np.uint64)
    c = coarsen(grid, p, None, None, ones(8), np.array([5, 5, 5, 9, 9, 7, 7, 7], np.uint16), 1, kind="mode")
    assert c.klass.tolist() == [5]  # 3 · 5, 2 · 9, 3 · 7: a tie of 5 and 7 → 5
    grid2 = make_grid((4, 2, 1))
    k = np.array([3, 3, 8, 8, 3, 3, 8, 9], np.uint16)
    c2 = coarsen(grid2, np.arange(8, dtype=np.uint64), None, None, ones(8), k, 1, kind="mode")
    assert c2.p.tolist() == [0, 1] and c2.klass.tolist() == [3, 8]


def test_mode_weights_by_the_volume_of_the_sub_blocks() -> None:
    grid = make_grid((1, 1, 1), div=(2, 2, 1))
    s = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.uint8)
    w = np.array([[1, 1, 1], [1, 1, 1], [2, 1, 1]], dtype=np.uint8)
    c = coarsen(grid, np.zeros(3, np.uint64), s, w, ones(3), np.array([1, 1, 2], np.uint16), 0 + 1, kind="mode")
    assert c.klass.tolist() == [1]  # 1·1 + 1·1 against 2·1: a tie → the lower
    c = coarsen(grid, np.zeros(3, np.uint64), s, w, ones(3), np.array([1, 4, 2], np.uint16), 1, kind="mode")
    assert c.klass.tolist() == [2]  # 2 takes the half, 1 and 4 a quarter each


def test_a_variable_step_weighs_by_the_volume_of_the_parent() -> None:
    grid = GridV2.of({"corner": [0, 0, 0], "counts": [2, 1, 1], "order": "F",
                      "tensors": {"x": [1.0, 3.0], "y": [1.0], "z": [1.0]}})  # fmt: skip
    c = coarsen(grid, np.arange(2, dtype=np.uint64), None, None, ones(2), np.array([0, 8], np.uint16), 1)
    assert c.klass.tolist() == [6]  # (1·0 + 3·8) / 4


# --- LOD: the view ----------------------------------------------------------------------------------------------


def test_t87_9_through_the_view_is_not_simplified() -> None:
    grid = make_grid((100, 100, 100), size=(1.0, 1.0, 1.0))
    n = 100**3
    v = view(grid, np.arange(n, dtype=np.uint64), None, None, ones(n), np.zeros(n, np.uint16))
    assert (len(v.faces), v.faces_raw, v.faces_built, v.lod, v.simplified) == (6, 60_000, 60_000, 0, False)
    raw = view(grid, np.arange(n, dtype=np.uint64), None, None, ones(n), merge=False)
    assert len(raw.faces) == 60_000


def test_the_faces_stay_within_the_limit_and_the_surface_is_the_same() -> None:
    grid = make_grid((64, 64, 64), size=(1.0, 1.0, 1.0))
    n = 64**3
    p = np.arange(n, dtype=np.uint64)
    klass = (np.arange(n) // 7 % 5).astype(np.uint16)  # stripes of classes: nothing merges at level 0
    full = view(grid, p, None, None, ones(n), klass, limit=10**7, merge=False)
    assert full.lod == 0 and len(full.faces) == 6 * 64 * 64
    v = view(grid, p, None, None, ones(n), klass, limit=2000, merge=False)
    assert v.simplified and v.lod == 2 and v.faces_raw == 24_576 and len(v.faces) == v.faces_built <= 2000
    assert len(v.faces) == 6 * 16 * 16
    for a in range(6):  # the same box: the coarse faces lie on the same six planes and cover the same area
        axis = a >> 1
        assert (v.faces.c[v.faces.a == a][:, axis] == (64.0 if a & 1 else 0.0)).all()
        assert float((4 * v.faces.h[v.faces.a == a].prod(axis=1)).sum()) == 64.0 * 64.0
    m = view(grid, p, None, None, ones(n), klass, limit=2000)
    assert m.lod == 2 and len(m.faces) <= len(v.faces)


def test_the_decision_is_taken_before_the_faces_are_built(monkeypatch: pytest.MonkeyPatch) -> None:
    grid = make_grid((32, 32, 32), size=(1.0, 1.0, 1.0))
    n = 32**3
    built: list[int] = []
    real = lod._build
    monkeypatch.setattr(lod, "_build", lambda *a, **k: built.append(1) or real(*a, **k))
    seen: list[int] = []
    real_exposed = lod.exposed
    monkeypatch.setattr(lod, "exposed", lambda *a, **k: seen.append(len(a[1])) or real_exposed(*a, **k))
    v = view(grid, np.arange(n, dtype=np.uint64), None, None, ones(n), limit=500)
    assert v.lod >= 2 and not built  # the 6144 faces of the model were never built
    assert seen and max(seen) <= 8**3 and len(v.faces) <= 500


def test_lod_with_a_filter_sub_blocks_and_the_mode_of_categories() -> None:
    grid, p, s, w, passed = random_model(3)
    klass = (np.arange(len(p)) % 4).astype(np.uint16)
    plain = view(grid, p, s, w, passed, klass, limit=10**6)
    assert not plain.simplified
    for kind in ("mean", "mode"):
        v = view(grid, p, s, w, passed, klass, limit=30, kind=kind)
        assert v.simplified and len(v.faces) <= 30 and v.lod >= 1
        assert set(v.faces.k.tolist()) <= {0, 1, 2, 3}  # a class never comes from nowhere
    with pytest.raises(ApiError):
        view(grid, p, s, w, passed, limit=0)


def test_the_top_level_is_one_cell_when_the_limit_is_below_six() -> None:
    grid = make_grid((8, 8, 8))
    v = view(grid, np.arange(512, dtype=np.uint64), None, None, ones(512), limit=3)
    assert v.lod == 4 and len(v.faces) == 6  # 2^4 ≥ 8: one cell is the least there is


def test_the_same_bits_of_lod_at_any_thread_count() -> None:
    grid = make_grid((20, 20, 20), size=(1.0, 2.0, 3.0))
    n = 20**3
    klass = (np.arange(n) % 9 // 2).astype(np.uint16)
    one = view(grid, np.arange(n, dtype=np.uint64), None, None, ones(n), klass, limit=500, threads=1)
    assert one.simplified
    for t in (2, 4, 8):
        assert same(one.faces, view(grid, np.arange(n, dtype=np.uint64), None, None, ones(n), klass, limit=500,
                                    threads=t).faces)  # fmt: skip


# --- the legend --------------------------------------------------------------------------------------------------


def test_scale_class_is_256_buckets_clamped() -> None:
    v = np.array([-5.0, 0.0, 0.01, 0.5, 9.99, 10.0, 99.0, np.nan])
    assert scale_class(v, 0.0, 10.0).tolist() == [0, 0, 0, 12, 255, 255, 255, NO_CLASS]
    assert scale_class(np.array([0.5, 0.0, 5.0]), 0.0, 1.0)[0] == 128


def test_scale_class_log_and_non_positive() -> None:
    k = scale_class(np.array([0.1, 1.01, 10.0, 0.0, -1.0, 0.01, np.nan]), 0.1, 10.0, log=True)
    assert k.tolist() == [0, 128, 255, 0, 0, 0, NO_CLASS]


def test_the_bins_match_the_classes() -> None:
    for lo, hi, log in ((0.1, 10.0, False), (0.1, 10.0, True), (-3.0, 5.0, False)):
        bins = scale_bins(lo, hi, log=log)
        assert len(bins) == 256 and bins[0][0] == lo and bins[-1][1] == hi
        assert all(bins[i][1] == bins[i + 1][0] and bins[i][0] < bins[i][1] for i in range(255))
        mid = np.array([(a + b) / 2 for a, b in bins])  # the middle of a bucket is in it
        assert scale_class(mid, lo, hi, log=log).tolist() == list(range(256))
    assert 0.13867 < scale_bins(0.1, 10.0)[0][1] < 0.13868


def test_a_wrong_scale_is_422() -> None:
    for lo, hi, log in ((1.0, 1.0, False), (5.0, 1.0, False), (0.0, 1.0, True), (float("nan"), 1.0, False)):
        with pytest.raises(ApiError) as e:
            scale_class(np.array([1.0]), lo, hi, log=log)
        assert e.value.status == 422 and e.value.code == "block_model_invalid"


def test_break_classes_and_their_bins() -> None:
    assert break_class(np.array([0.1, 0.5, 0.9, 1.0, 5.0, np.nan]), [0.5, 1.0]).tolist() == [0, 1, 1, 2, 2, NO_CLASS]
    assert break_bins([0.5, 1.0]) == [[None, 0.5], [0.5, 1.0], [1.0, None]]
    for bad in ([], [1.0, 1.0], [2.0, 1.0], list(range(256))):
        with pytest.raises(ApiError):
            break_class(np.array([1.0]), bad)
