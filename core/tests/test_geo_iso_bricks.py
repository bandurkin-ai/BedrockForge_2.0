"""The bricks of the isosurface (подшаг 19.5; `geo/iso_bricks.py`): the grid, the values of a brick (+H beyond the
model, NaN → +H, 0 → +1e-9·h, the store), the exits of a brick through its faces, edges and corners."""

from __future__ import annotations

import numpy as np
import pytest

from bf_core.geo.iso_bricks import Grid, IsoError, brick_values, exit_bricks, has_crossing, sanitize


def grid(levels: int = 2, h: float = 2.0, size: float = 60.0) -> Grid:
    return Grid.from_box((-size,) * 3, (size,) * 3, h, levels)


def test_the_grid_covers_the_box_with_whole_bricks() -> None:
    g = Grid.from_box((10.0, 20.0, 30.0), (10.0 + 61.0, 20.0 + 8.0, 30.0 + 1.0), 2.0, 2)
    assert g.blocks == (8, 1, 1)  # 31 cells → 8 bricks of 4; 4 cells → 1; 1 cell → 1
    assert g.cells == (32, 4, 4) and g.n == 5 and g.big_h == 8.0
    assert g.brick_dims == (10, 3, 3)
    assert g.points(np.array([[0, 0, 0], [1, 2, 3]])).tolist() == [[10.0, 20.0, 30.0], [12.0, 24.0, 36.0]]


def test_lin_and_unlin_are_inverse_and_follow_the_index_order() -> None:
    g = grid()
    c = np.array([[-1, -1, -1], [-1, -1, 0], [-1, 0, -1], [0, -1, -1], [15, 15, 15]])
    lin = g.lin(c)
    assert lin.tolist() == sorted(lin.tolist()) and lin[0] == 0 and len(set(lin.tolist())) == 5
    assert (g.unlin(lin) == c).all()


def test_the_token_changes_with_the_grid_and_not_with_anything_else() -> None:
    a = grid()
    assert a.token() == grid().token() and len(a.token()) == 16
    assert a.token() != grid(h=2.5).token() and a.token() != grid(levels=3).token()
    assert a.token() != Grid.from_box((-60.0, -60.0, -59.0), (60.0,) * 3, 2.0, 2).token()


def test_the_values_beyond_the_model_are_plus_h_and_the_field_sees_only_the_model() -> None:
    g = grid()
    asked: list[np.ndarray] = []

    def field(p: np.ndarray) -> np.ndarray:
        asked.append(p.copy())
        return -np.ones(len(p))

    counters: dict = {}
    v = brick_values(g, np.array([[-1, 0, 0], [0, 0, 0]]), field, None, counters)
    assert v.shape == (2, 5, 5, 5)
    assert (v[0, :4] == g.big_h).all() and (v[0, 4] == -1.0).all()  # the nodes −4 … −1 of x are beyond
    assert (v[1] == -1.0).all()
    pts = np.concatenate(asked)
    assert counters["nodes"] == len(pts) == 25 + 125
    assert pts.min() >= -60.0  # the model starts at lo


def test_not_finite_is_plus_h_and_zero_is_a_small_positive_number() -> None:
    g = grid()
    out = sanitize([np.nan, np.inf, -np.inf, 0.0, -0.0, -3.0, 2.0], g, 7)
    assert out[:3].tolist() == [8.0, 8.0, 8.0]
    assert out[3] == out[4] == pytest.approx(2e-9) and out[3] > 0
    assert out[5:].tolist() == [-3.0, 2.0]
    with pytest.raises(IsoError) as e:
        sanitize([1.0, 2.0], g, 3)
    assert e.value.code == "bad_field"


def test_the_store_is_asked_by_the_index_and_only_misses_reach_the_field() -> None:
    g = grid()
    store: dict[tuple[int, int, int], np.ndarray] = {}
    gets: list[tuple[int, int, int]] = []

    class Cache:
        def get(self, index: tuple[int, int, int]) -> np.ndarray | None:
            gets.append(index)
            return store.get(index)

        def put(self, index: tuple[int, int, int], values: np.ndarray) -> None:
            store[index] = values

    bricks = np.array([[3, 4, 5], [6, 7, 8]])
    counters: dict = {}
    a = brick_values(g, bricks, lambda p: p[:, 0] + 0.1, Cache(), counters)
    assert gets == [(3, 4, 5), (6, 7, 8)] and set(store) == {(3, 4, 5), (6, 7, 8)}
    assert store[(3, 4, 5)].shape == (5, 5, 5) and counters.get("cache_hits", 0) == 0
    counters = {}
    b = brick_values(g, bricks, lambda p: 1 / 0, Cache(), counters)  # type: ignore[arg-type,return-value]
    assert (a == b).all() and counters["cache_hits"] == 2 and "nodes" not in counters
    store[(3, 4, 5)] = np.zeros((2, 2, 2))  # a wrong shape is a miss
    c = brick_values(g, bricks, lambda p: p[:, 0] + 0.1, Cache(), {})
    assert (c == a).all()


def test_has_crossing() -> None:
    v = np.ones((3, 5, 5, 5))
    v[1, 2, 2, 2] = -1.0
    v[2] = -1.0
    assert has_crossing(v).tolist() == [False, True, False]


def positive_with(*nodes: tuple[int, int, int]) -> np.ndarray:
    v = np.ones((1, 5, 5, 5))
    for n in nodes:
        v[0, n[0], n[1], n[2]] = -1.0
    return v


def exits(*nodes: tuple[int, int, int]) -> set[tuple[int, int, int]]:
    g = grid(size=60.0)
    brick = np.array([[3, 3, 3]])
    lin = exit_bricks(g, brick, positive_with(*nodes))
    return {tuple(int(x) for x in b) for b in g.unlin(lin)}


def test_a_surface_inside_a_brick_has_no_exits() -> None:
    assert exits((2, 2, 2)) == set()


def test_a_crossing_in_a_face_has_the_brick_beyond_it() -> None:
    assert exits((4, 2, 2)) == {(4, 3, 3)}  # the node on the face x = high
    assert exits((0, 2, 2)) == {(2, 3, 3)}
    assert exits((2, 0, 2)) == {(3, 2, 3)}
    assert exits((2, 2, 4)) == {(3, 3, 4)}


def test_a_crossing_on_the_edge_or_corner_of_a_brick_has_all_the_bricks_round_it() -> None:
    assert exits((4, 4, 2)) == {(4, 3, 3), (3, 4, 3), (4, 4, 3)}
    assert exits((0, 0, 0)) == {
        (2, 3, 3),
        (3, 2, 3),
        (3, 3, 2),
        (2, 2, 3),
        (2, 3, 2),
        (3, 2, 2),
    }  # not the one on the diagonal: it has the corner only; the edges that lead to it are the neighbours'


def test_the_bricks_beyond_the_grid_are_not_exits() -> None:
    g = grid(size=8.0)  # M = 2: the bricks −1 … 2
    v = positive_with((0, 2, 2))
    assert exit_bricks(g, np.array([[-1, 0, 0]]), v).size == 0  # the brick −2 is not there
