"""The shell of a block model by faces (подшаг 17.22; ADR 0023 §11, decision 16; docs/progress/e17-api.md §3):
the tests of BF (`exposed_mask`: 3 × 3 × 3, halves of the middle, a parent not filled), sub-blocks of different
levels and the rule «full neighbour», the mask of the filter (a row that does not pass draws nothing and hides
nothing), a variable step, a turned grid, Т8.7-9 (100 × 100 × 100 of one colour — 60 000 faces), the order of the
faces, the same bits with 1, 2, 4 and 8 threads, a bitmap and a search over P giving the same answers, a random
model against a plain reference of the rule, and the 422 of a broken model."""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import shell
from bf_core.blocks.schema import GridV2
from bf_core.blocks.shell import Faces, count_faces, exposed, face_mask


def make_grid(counts: Any, size: Any = (10.0, 10.0, 10.0), div: Any = (1, 1, 1), rotation: float = 0.0) -> GridV2:
    return GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": list(counts), "size": list(size), "div": list(div),
                      "rotation": rotation, "order": "F"})  # fmt: skip


def rows_of(items: list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]) -> tuple[Any, Any, Any]:
    items = sorted(items, key=lambda t: (t[0], t[1][2], t[1][1], t[1][0]))
    p = np.array([t[0] for t in items], dtype=np.uint64)
    return p, np.array([t[1] for t in items], dtype=np.uint8), np.array([t[2] for t in items], dtype=np.uint8)


def per_row(f: Faces, n: int) -> np.ndarray:
    return np.bincount(f.row, minlength=n)


def check_order(f: Faces) -> None:
    key = list(zip(f.a.tolist(), f.g[:, 0].tolist(), f.k.tolist(), f.g[:, 3].tolist(), f.g[:, 1].tolist(), strict=True))
    assert key == sorted(key)


# --- the tests of BF (`exposed_mask`) ---------------------------------------------------------------------------


def test_bf_whole_parents_only_the_middle_is_closed() -> None:
    grid = make_grid((3, 3, 3))
    p = np.arange(27, dtype=np.uint64)
    f = exposed(grid, p, None, None, np.ones(27, bool))
    n = per_row(f, 27)
    assert (n > 0).sum() == 26 and n[13] == 0
    assert len(f) == 6 * 9  # the six sides of the cube, 9 faces each
    check_order(f)


def test_bf_halves_of_the_middle_and_an_open_neighbour() -> None:
    grid = make_grid((3, 3, 3), div=(2, 1, 1))
    items = [(q, (0, 0, 0), (2, 1, 1)) for q in range(27) if q != 13]
    items += [(13, (0, 0, 0), (1, 1, 1)), (13, (1, 0, 0), (1, 1, 1))]
    p, s, w = rows_of(items)
    middle = np.flatnonzero(p == 13)
    f = exposed(grid, p, s, w, np.ones(len(p), bool))
    assert per_row(f, len(p))[middle].tolist() == [0, 0]  # closed on every side: the parent is full
    # the top neighbour (1, 1, 2) = 22 removed: both halves show their top face (a = 5)
    keep = p != 22
    f2 = exposed(grid, p[keep], s[keep], w[keep], np.ones(int(keep.sum()), bool))
    mid2 = np.flatnonzero(p[keep] == 13)
    assert [sorted(f2.a[f2.row == r].tolist()) for r in mid2] == [[5], [5]]
    # a parent not filled by its sub-blocks (one half only): the face toward the missing half (+x, a = 1) is shown,
    # and the open top; the others lie on the boundary of the parent against full neighbours
    keep = (p != 22) & ~((p == 13) & (s[:, 0] == 1))
    f3 = exposed(grid, p[keep], s[keep], w[keep], np.ones(int(keep.sum()), bool))
    r = int(np.flatnonzero(p[keep] == 13)[0])
    assert sorted(f3.a[f3.row == r].tolist()) == [1, 5]


# --- sub-blocks of different levels ------------------------------------------------------------------------------


def three_in_a_row(middle_rows: int = 8, skip_last: bool = False) -> tuple[GridV2, Any, Any, Any]:
    grid = make_grid((3, 1, 1), div=(2, 2, 2))
    items = [(0, (0, 0, 0), (2, 2, 2))]
    cells = list(itertools.product((0, 1), repeat=3))[:middle_rows]
    items += [(1, (x, y, z), (1, 1, 1)) for x, y, z in cells]
    if not skip_last:
        items.append((2, (0, 0, 0), (2, 2, 2)))
    p, s, w = rows_of(items)
    return grid, p, s, w


def test_levels_a_full_parent_of_sub_blocks_closes_the_whole_neighbours() -> None:
    grid, p, s, w = three_in_a_row()
    f = exposed(grid, p, s, w, np.ones(len(p), bool))
    n = per_row(f, len(p))
    assert n[p == 0].tolist() == [5] and n[p == 2].tolist() == [5]  # the face toward the middle parent is hidden
    assert n[p == 1].sum() == 16  # 4 sides × 4 faces; the inside and the two x sides are hidden
    assert len(f) == 26
    # every face of the middle one is on y or z (a = 2…5)
    assert (f.a[f.row == int(np.flatnonzero(p == 1)[0])] >= 2).all()


def test_levels_a_parent_not_filled_shows_everything_around_it() -> None:
    grid, p, s, w = three_in_a_row(middle_rows=7)
    f = exposed(grid, p, s, w, np.ones(len(p), bool))
    n = per_row(f, len(p))
    # extra faces are drawn, also those between its own sub-blocks; only the x sides against full parents are hidden
    assert n[p == 1].tolist() == [5] * 7
    assert n[p == 0].tolist() == [6] and n[p == 2].tolist() == [6]  # their neighbour is not full any more


def test_mask_a_row_that_does_not_pass_draws_nothing_and_hides_nothing() -> None:
    grid, p, s, w = three_in_a_row()
    passed = np.ones(len(p), bool)
    passed[p == 2] = False  # the last parent is cut by the filter
    f = exposed(grid, p, s, w, passed)
    n = per_row(f, len(p))
    assert n[p == 2].tolist() == [0]
    assert n[p == 0].tolist() == [5]  # the middle one is still full and passes
    assert n[p == 1].sum() == 20  # its +x side is open now: 4 more faces
    # one sub-block of the middle cut: the middle is not full — the first parent shows its face toward it
    passed = np.ones(len(p), bool)
    first = int(np.flatnonzero(p == 1)[0])
    passed[first] = False
    f = exposed(grid, p, s, w, passed)
    n = per_row(f, len(p))
    assert n[first] == 0 and n[p == 1].sum() == 35 and n[p == 0].tolist() == [6]
    # `shell=False`: all blocks — the six faces of every passed block
    m = face_mask(grid, p, s, w, passed, shell=False)
    assert count_faces(m) == 6 * int(passed.sum())


def test_class_goes_to_the_faces_and_the_order_is_by_class() -> None:
    grid = make_grid((4, 1, 1))
    p = np.arange(4, dtype=np.uint64)
    klass = np.array([7, 3, 3, 7], dtype=np.uint16)
    f = exposed(grid, p, None, None, np.ones(4, bool), klass)
    assert (f.k == klass[f.row]).all()
    check_order(f)
    top = f.a == 5
    assert f.k[top].tolist() == [3, 3, 7, 7]  # plane equal — by class, then v, u


def test_a_variable_step_and_the_coordinates() -> None:
    grid = GridV2.of({"corner": [0, 0, 0], "tensors": {"x": [1, 2, 4], "y": [5, 5], "z": [10]}, "order": "F"})
    p = np.arange(6, dtype=np.uint64)
    f = exposed(grid, p, None, None, np.ones(6, bool))
    assert len(f) == 22  # 12 on z, 4 on the ends of x, 6 on the ends of y
    top = (f.a == 5) & (f.row == 1)  # cell (1, 0, 0): x 1…3, y 0…5, z 0…10
    assert f.c[top].tolist() == [[2.0, 2.5, 10.0]] and f.h[top].tolist() == [[1.0, 2.5]]
    check_order(f)


def test_a_turned_grid_has_local_faces() -> None:
    p = np.arange(8, dtype=np.uint64)
    plain = exposed(make_grid((2, 2, 2)), p, None, None, np.ones(8, bool))
    turned = exposed(make_grid((2, 2, 2), rotation=30.0), p, None, None, np.ones(8, bool))
    assert np.array_equal(plain.c, turned.c) and np.array_equal(plain.a, turned.a)


# --- Т8.7-9 ------------------------------------------------------------------------------------------------------


def test_t87_9_a_full_model_of_100_cubed_has_60000_faces() -> None:
    grid = make_grid((100, 100, 100), size=(1.0, 1.0, 1.0))
    n = 100**3
    f = exposed(grid, np.arange(n, dtype=np.uint64), None, None, np.ones(n, bool), np.zeros(n, np.uint16))
    assert len(f) == 60_000  # before the merge of 17.23: 6 rectangles after it
    assert np.bincount(f.a, minlength=6).tolist() == [10_000] * 6
    assert (f.h == 0.5).all()
    assert float((4 * f.h[:, 0] * f.h[:, 1]).sum()) == 60_000.0
    check_order(f)
    # the planes: the sides of the cube
    for a in range(6):
        axis, side = a >> 1, a & 1
        assert (f.c[f.a == a][:, axis] == (100.0 if side else 0.0)).all()


# --- the same bits and the same answers --------------------------------------------------------------------------


def random_model(seed: int) -> tuple[GridV2, Any, Any, Any, np.ndarray]:
    rng = np.random.default_rng(seed)
    counts, div = (4, 3, 3), (4, 2, 4)
    grid = make_grid(counts, div=div)
    items: list[tuple[int, tuple[int, int, int], tuple[int, int, int]]] = []

    def split(p: int, s: tuple[int, ...], w: tuple[int, ...], depth: int) -> None:
        axes = [a for a in range(3) if w[a] > 1]
        if not axes or depth == 0 or rng.random() < 0.25:
            if rng.random() < 0.9:  # a hole now and then
                items.append((p, (s[0], s[1], s[2]), (w[0], w[1], w[2])))
            return
        a = int(rng.choice(axes))
        h = w[a] // 2
        w1 = tuple(h if i == a else w[i] for i in range(3))
        s2 = tuple(s[i] + h if i == a else s[i] for i in range(3))
        split(p, s, w1, depth - 1)
        split(p, s2, w1, depth - 1)

    for p in range(counts[0] * counts[1] * counts[2]):
        kind = rng.random()
        if kind < 0.15:
            continue  # not in the model
        if kind < 0.6:
            items.append((p, (0, 0, 0), div))  # a whole parent
        else:
            split(p, (0, 0, 0), div, 6)
    pp, ss, ww = rows_of(items)
    return grid, pp, ss, ww, rng.random(len(pp)) < 0.85


def reference(grid: GridV2, p: Any, s: Any, w: Any, passed: Any) -> set[tuple[int, int]]:
    """The rule of the module docstring, written plainly: the set of (row, a)."""
    nx, ny, nz = grid.counts
    div = grid.sub.div
    total = div[0] * div[1] * div[2]
    vol: dict[int, int] = {}
    allp: dict[int, bool] = {}
    for r in range(len(p)):
        vol[int(p[r])] = vol.get(int(p[r]), 0) + int(np.prod(w[r].astype(int)))
        allp[int(p[r])] = allp.get(int(p[r]), True) and bool(passed[r])
    full = {q for q in vol if vol[q] == total and allp[q]}
    out = set()
    for r in range(len(p)):
        if not passed[r]:
            continue
        q = int(p[r])
        ijk = (q % nx, (q // nx) % ny, q // (nx * ny))
        for a in range(3):
            for side in (0, 1):
                inside = s[r][a] > 0 if side == 0 else s[r][a] + w[r][a] < div[a]
                if inside:
                    hidden = q in full
                else:
                    n = list(ijk)
                    n[a] += 1 if side else -1
                    hidden = (0 <= n[a] < (nx, ny, nz)[a]) and (n[0] + nx * (n[1] + ny * n[2])) in full
                if not hidden:
                    out.add((r, 2 * a + side))
    return out


@pytest.mark.parametrize("seed", range(6))
def test_random_models_against_the_plain_reference(seed: int) -> None:
    grid, p, s, w, passed = random_model(seed)
    f = exposed(grid, p, s, w, passed)
    assert set(zip(f.row.tolist(), f.a.tolist(), strict=True)) == reference(grid, p, s, w, passed)
    assert len(f) == len(set(zip(f.row.tolist(), f.a.tolist(), strict=True)))
    check_order(f)


def same(x: Faces, y: Faces) -> bool:
    return all(np.array_equal(getattr(x, n), getattr(y, n)) for n in ("c", "h", "a", "k", "row", "g"))


def test_the_same_bits_with_1_2_4_and_8_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "STRIP_ROWS", 7)  # many strips
    grid, p, s, w, passed = random_model(11)
    klass = (np.arange(len(p)) % 5).astype(np.uint16)
    one = exposed(grid, p, s, w, passed, klass, threads=1)
    assert len(one) > 100
    for t in (2, 4, 8):
        assert same(one, exposed(grid, p, s, w, passed, klass, threads=t))


def test_rows_in_any_order_give_the_same_faces() -> None:
    grid, p, s, w, passed = random_model(3)
    klass = (np.arange(len(p)) % 4).astype(np.uint16)
    base = exposed(grid, p, s, w, passed, klass)
    perm = np.random.default_rng(1).permutation(len(p))
    f = exposed(grid, p[perm], s[perm], w[perm], passed[perm], klass[perm])
    assert np.array_equal(perm[f.row], base.row) or (
        sorted(zip(perm[f.row].tolist(), f.a.tolist(), strict=True))
        == sorted(zip(base.row.tolist(), base.a.tolist(), strict=True))
    )
    assert np.array_equal(f.g[np.lexsort(f.g.T[::-1])], base.g[np.lexsort(base.g.T[::-1])])


def test_the_bitmap_and_the_search_over_p_agree(monkeypatch: pytest.MonkeyPatch) -> None:
    grid, p, s, w, passed = random_model(5)
    with_bits = exposed(grid, p, s, w, passed)
    monkeypatch.setattr(shell, "BITMAP_CELLS", 0)
    assert same(with_bits, exposed(grid, p, s, w, passed))


def test_no_rows_no_faces() -> None:
    grid = make_grid((2, 2, 2))
    f = exposed(grid, np.zeros(0, np.uint64), None, None, np.zeros(0, bool))
    assert len(f) == 0 and f.c.shape == (0, 3) and f.h.shape == (0, 2)
    none = exposed(grid, np.arange(8, dtype=np.uint64), None, None, np.zeros(8, bool))
    assert len(none) == 0  # nothing passes


def test_a_large_grid_does_not_need_memory_for_its_cells() -> None:
    grid = make_grid((1250, 1250, 1250))  # 1.95·10⁹ cells: above BITMAP_CELLS — the search over P
    p = np.array([0, 1, 1_000_000], dtype=np.uint64)
    f = exposed(grid, p, None, None, np.ones(3, bool))
    assert per_row(f, 3).tolist() == [5, 5, 6]  # blocks 0 and 1 touch: the two faces between them are hidden


@pytest.mark.parametrize("counts, div", [((40_000, 1, 1), (1, 1, 1)), ((1_000_000_000, 1, 1), (128, 1, 1))])
def test_the_order_of_the_faces_on_a_long_grid(counts: Any, div: Any) -> None:
    """More than 15 and than 32 bits of a coordinate: the other keys of the sort give the same order."""
    grid = make_grid(counts, size=(1.0, 1.0, 1.0), div=div)
    p = np.array([0, 1, 5, 6, 7, 39_999], dtype=np.uint64)
    klass = np.array([2, 1, 2, 1, 2, 1], dtype=np.uint16)
    f = exposed(grid, p, None, None, np.ones(6, bool), klass)
    assert len(f) == 6 * 6 - 6  # the pairs (0, 1) and (5, 6, 7) hide 2 + 4 faces
    check_order(f)
    # the geometry does not depend on the key: every face is one cell of the size 1 / div
    assert np.allclose(f.h[:, 0] * 2, 1.0) and np.allclose(f.h[:, 1] * 2, 1.0)


# --- 422 --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("what", ["outside", "sub"])
def test_a_broken_model_is_refused_with_422(what: str) -> None:
    grid = make_grid((2, 2, 2), div=(2, 2, 2))
    if what == "outside":
        args: tuple[Any, ...] = (np.array([8], dtype=np.uint64), None, None)
    else:
        args = (np.array([0], dtype=np.uint64), np.array([[1, 0, 0]], np.uint8), np.array([[2, 1, 1]], np.uint8))
    with pytest.raises(ApiError) as e:
        exposed(grid, *args, np.ones(1, bool))
    assert e.value.status == 422 and e.value.code == "block_model_invalid" and e.value.hint
