"""The profile of the blocks at a contact (Д-28, ТД28-2, ТД28-3; `geostat/estimate/contact.py`, `stats.contact.
signed_chart`): step data (1 on one side, 3 on the other) and a hard boundary give the means 1 and 3 in every step
(1e-12); the distance transform of the grid of the parents against a full search over the centres; the contact
half a step from a flat boundary, whole cells or sub-blocks; the distance of the centres to a cube — analytic
(1e-9 m); the same format of the answer as the chart of the samples (Э13)."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import geometry
from bf_core.blocks import schema as bs
from bf_core.blocks import store as bm
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.geo.intersect import MeshTarget
from bf_core.geostat.estimate import contact as ec
from bf_core.stats import contact as sc
from bf_core.stats.weighted import StatsError
from bf_core.storage.blobs import BlobStore
from tests.meshutil import cube
from tests.test_blocks_fields_store import bc, blocks_router, project  # noqa: F401  (fixtures)
from tests.test_geo_sdf import box_sdf

TOL = 1e-12
LABELS = ["A", "B", "C"]
FIELDS = [
    {"name": "EST", "dtype": "f8"},
    {"name": "STATUS", "dtype": "i1"},
    {"name": "DOM", "dtype": "cat", "labels": LABELS, "role": "code"},
]


def grid_of(counts: Any, size: Any, corner: Any = (0.0, 0.0, 0.0), div: Any = None) -> bs.GridV2:
    raw = {"corner": list(corner), "counts": list(counts), "size": list(size)}
    return bs.GridV2.of({**raw, "div": list(div)} if div else raw)


def make(
    p: Any, store: BlobStore, grid: dict[str, Any], idx: Any, values: dict[str, Any], s: Any = None, w: Any = None,
    fields: Any = FIELDS,
) -> Model:  # fmt: skip
    with bm.CellsWriterV2(store, grid, fields, row_group=300) as wr:
        wr.write(np.asarray(idx, dtype=np.uint64), values, s, w)
        staged = wr.finish()
    try:
        with dialect.write_tx(p.engine) as conn:
            model_id = bm.commit_block_model(conn, store, staged, name="Контакт")
    finally:
        staged.discard()
    with dialect.read_tx(p.engine) as conn:
        return Model.open(conn, store, model_id)


def cell_ijk(counts: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    nx, ny, nz = counts
    k, j, i = np.meshgrid(np.arange(nz), np.arange(ny), np.arange(nx), indexing="ij")
    return i.ravel(), j.ravel(), k.ravel()  # i fastest: the order of P


# --- the distance transform of the grid of the parents ---------------------------------------------------------------


def test_centre_distance_is_the_full_search_over_the_centres() -> None:
    counts, size = (9, 7, 6), (3.0, 2.0, 1.5)
    rng = np.random.default_rng(4)
    target = rng.random(9 * 7 * 6) < 0.04
    target[0] = True
    i, j, k = (a.astype(np.intp) for a in cell_ijk(counts))
    d, m = ec.centre_distance(counts, size, target, (i, j, k))
    centres = np.column_stack([i * size[0], j * size[1], k * size[2]]).astype(np.float64)
    hit = np.flatnonzero(target)
    full = np.linalg.norm(centres[:, None, :] - centres[hit][None, :, :], axis=2)  # [cells, targets]
    assert np.abs(d - full.min(axis=1)).max() < 1e-9
    # the shift m is that of one of the nearest cells (a tie may be broken either way)
    shift = np.abs(np.stack([i, j, k], axis=1)[:, None, :] - np.stack([i, j, k], axis=1)[hit][None, :, :]).max(axis=2)
    for c in range(len(d)):
        assert m[c] in shift[c][full[c] <= full[c].min() + 1e-9]
    assert (m[target] == 0).all() and (d[target] == 0.0).all()
    with pytest.raises(ValueError):
        ec.centre_distance(counts, size, np.zeros(9 * 7 * 6, dtype=bool), (i[:1], j[:1], k[:1]))


@pytest.mark.parametrize("axis", [0, 1, 2])
def test_a_flat_contact_is_half_a_step_from_the_cells_next_to_it(axis: int) -> None:
    counts, size = [6, 5, 7], (2.0, 1.0, 0.5)
    cut = 3
    i, j, k = cell_ijk(counts)
    c = np.stack([i, j, k])[axis]
    codes = np.where(c < cut, 0, 1).astype(np.int32)
    p = (i + counts[0] * (j + counts[1] * k)).astype(np.uint64)
    dist, side = ec.row_distance(grid_of(counts, size), p, codes, 0, 1)
    h = size[axis]
    want = np.where(c < cut, (cut - c - 0.5) * h, (c - cut + 0.5) * h)
    assert np.abs(dist - want).max() < TOL
    assert np.array_equal(side, codes.astype(np.int8))
    assert (dist > 0).all()


def test_rows_of_another_domain_or_without_the_other_one_are_at_no_contact() -> None:
    counts = [4, 3, 2]
    i, j, k = cell_ijk(counts)
    p = (i + 4 * (j + 3 * k)).astype(np.uint64)
    codes = np.where(i < 2, 0, 1).astype(np.int32)
    codes[(i == 3) & (j == 0)] = 2  # a third domain
    codes[(i == 0) & (j == 2)] = -1  # an empty cell
    dist, side = ec.row_distance(grid_of(counts, (1, 1, 1)), p, codes, 0, 1)
    assert (side[codes == 2] == -1).all() and np.isnan(dist[codes == 2]).all()
    assert (side[codes == -1] == -1).all() and np.isnan(dist[codes == -1]).all()
    only_a = np.where(codes == 1, 2, codes).astype(np.int32)  # no B at all: no contact, never a distance of 0
    dist, side = ec.row_distance(grid_of(counts, (1, 1, 1)), p, only_a, 0, 1)
    assert (side == -1).all() and np.isnan(dist).all()
    with pytest.raises(ApiError) as e:
        ec.row_distance(grid_of(counts, (1, 1, 1)), p, codes, 1, 1)
    assert e.value.code == "bad_param"
    with pytest.raises(ApiError) as e:
        ec.row_distance(bs.GridV2.of({"corner": [0, 0, 0], "counts": [2, 1, 1], "tensors": {"x": [1, 2], "y": [1],
                                                                                           "z": [1]}}),
                        np.array([0, 1], dtype=np.uint64), np.array([0, 1], dtype=np.int32), 0, 1)  # fmt: skip
    assert e.value.code == "variable_grid"


def test_a_grid_of_too_many_cells_is_refused_before_anything_is_allocated() -> None:
    grid = grid_of([1000, 1000, 100], (1, 1, 1))  # 100 million cells: ≈ 2 GB of masks and distances
    with pytest.raises(ApiError) as e:
        ec.row_distance(grid, np.array([0, 1], dtype=np.uint64), np.array([0, 1], dtype=np.int32), 0, 1)
    assert (e.value.status, e.value.code) == (422, "grid_too_large")
    assert e.value.message and e.value.hint and any("а" <= ch.lower() <= "я" for ch in e.value.message)


def test_diagonal_neighbours_are_half_way() -> None:
    """Two cells that touch by an edge only: the pair is one cell away along every axis it moves on (m = 1)."""
    counts = [2, 2, 1]
    p = np.array([0, 3], dtype=np.uint64)  # (0, 0) and (1, 1)
    dist, _ = ec.row_distance(grid_of(counts, (2.0, 2.0, 2.0)), p, np.array([0, 1], dtype=np.int32), 0, 1)
    assert np.allclose(dist, 0.5 * math.sqrt(8.0), atol=TOL)


# --- ТД28-2: step data, a hard boundary -----------------------------------------------------------------------------


def step_model(p: Any, store: BlobStore) -> tuple[Model, np.ndarray]:
    """A model 10×8×12 of cells 2 × 1 × 0.5 m (volume 1 m³): A for i < 5 with EST = 1, B with EST = 3; STATUS 0 on
    all but a layer of blocks (STATUS 2, EST 99: not estimated) that the profile must not see."""
    counts, size = [10, 8, 12], [2.0, 1.0, 0.5]
    i, j, k = cell_ijk(counts)
    idx = i + 10 * (j + 8 * k)
    est = np.where(i < 5, 1.0, 3.0)
    status = np.zeros(len(idx))
    bad = (j == 7) & (k % 2 == 0)
    est[bad], status[bad] = 99.0, 2.0
    values = {"EST": est, "STATUS": status, "DOM": np.where(i < 5, 0, 1).astype(np.int32)}
    return make(p, store, {"corner": [100.0, 200.0, 50.0], "counts": counts, "size": size}, idx, values), bad


def test_td28_2_the_means_of_a_step_function_are_its_values(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    model, bad = step_model(p, BlobStore(p.folder))
    got = ec.blocks_profile(model, field="EST", a="A", b="B", domain_field="DOM", status_field="STATUS")
    edges = got["edges"]
    assert edges == [float(x) for x in range(-20, 21)]
    mean, n, volume = got["mean"], got["n"], got["volume"]
    cells_in_layer = 8 * 12 - 6  # blocks with STATUS 0 on a plane i = const (6 of the layer j = 7 are not)
    assert int(bad.sum()) == 6 * 10
    for q in range(len(n)):
        centre = 0.5 * (edges[q] + edges[q + 1])
        if n[q]:
            assert abs(mean[q] - (1.0 if centre < 0 else 3.0)) < TOL, q
            assert volume[q] == pytest.approx(n[q] * 1.0, abs=1e-9)
        else:
            assert mean[q] is None and volume[q] == 0.0  # a step without blocks has no mean, never 0
    # the distances are 1, 3, 5, 7, 9 m (the cell 2 m wide, half of it to the contact): the steps [1, 2), [3, 4) …
    filled = [q for q in range(len(n)) if n[q]]
    assert [edges[q] for q in filled] == [-10.0, -8.0, -6.0, -4.0, -2.0, 1.0, 3.0, 5.0, 7.0, 9.0]
    assert sum(n) == 5 * 2 * cells_in_layer
    assert got["a"]["mean"] == pytest.approx(1.0, abs=TOL) and got["b"]["mean"] == pytest.approx(3.0, abs=TOL)
    assert got["a"]["n"] == got["b"]["n"] == 5 * cells_in_layer and got["a"]["label"] == "A"
    c = got["counts"]
    assert c["rows"] == 960 and c["not_estimated"] == int(bad.sum()) and c["in_steps"] == sum(n)
    assert c["no_contact"] == 0 and c["beyond"] == 0 and got["weighted"] is True
    assert got["model_id"] == model.id and got["field"] == "EST" and "contacts" not in got


def test_without_status_every_block_counts_and_the_distance_cuts(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    model, bad = step_model(p, BlobStore(p.folder))
    got = ec.blocks_profile(model, field="EST", a="A", b="B", domain_field="DOM", distance=4.0, step=2.0)
    assert got["edges"] == [-4.0, -2.0, 0.0, 2.0, 4.0]
    # |d| = 1, 3 inside D = 4: the layers of 1 and 3 m of each side, 96 blocks each; the layers 5, 7, 9 m are beyond
    assert got["n"] == [96, 96, 96, 96]
    assert got["counts"]["beyond"] == 960 - 4 * 96 and got["counts"]["rows"] == 960
    assert "not_estimated" not in got["counts"]
    # without the filter the 99 of the 6 blocks a layer (j = 7, k even) is in the mean: (90 · c + 6 · 99) / 96
    assert got["mean"][0] == pytest.approx((90 * 1.0 + 6 * 99.0) / 96.0, rel=1e-12) == got["mean"][1]
    assert got["mean"][2] == pytest.approx((90 * 3.0 + 6 * 99.0) / 96.0, rel=1e-12) == got["mean"][3]


def test_the_mean_is_by_volume_and_equals_a_plain_numpy_reference(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    store = BlobStore(p.folder)
    counts, size = [12, 9, 7], [3.0, 2.0, 1.0]
    i, j, k = cell_ijk(counts)
    idx = i + 12 * (j + 9 * k)
    rng = np.random.default_rng(8)
    dom = (rng.random(len(idx)) < 0.45).astype(np.int32)
    dom[rng.random(len(idx)) < 0.1] = 2
    est = rng.lognormal(0.0, 1.0, len(idx))
    est[rng.random(len(idx)) < 0.1] = np.nan
    frac = rng.random(len(idx))
    frac[rng.random(len(idx)) < 0.05] = np.nan
    fields = [*FIELDS, {"name": "FRAC", "dtype": "f8", "role": "frac"}]
    status = np.where(rng.random(len(idx)) < 0.8, 0.0, 1.0)
    model = make(p, store, {"corner": [0.0, 0.0, 0.0], "counts": counts, "size": size}, idx,
                 {"EST": est, "STATUS": status, "DOM": dom, "FRAC": frac}, fields=fields)  # fmt: skip
    got = ec.blocks_profile(model, field="EST", a="A", b="B", domain_field="DOM", status_field="STATUS",
                            distance=12.0, step=1.5)  # fmt: skip
    d = ec.block_distance(model, field="DOM", a="A", b="B")
    vol = 6.0 * frac
    ok = np.isfinite(d) & np.isfinite(est) & (status == 0.0) & np.isfinite(vol) & (vol > 0) & (np.abs(d) <= 12.0)
    q = np.floor((np.nan_to_num(np.abs(d)) + 1e-6) / 1.5).astype(int)
    q = np.minimum(q, 7)
    slot = np.where(d < 0, 7 - q, 8 + q)
    for s in range(16):
        take = ok & (slot == s)
        assert got["n"][s] == int(take.sum())
        if take.any():
            assert got["mean"][s] == pytest.approx((vol[take] * est[take]).sum() / vol[take].sum(), rel=1e-12)
            assert got["volume"][s] == pytest.approx(vol[take].sum(), rel=1e-12)
        else:
            assert got["mean"][s] is None
    c = got["counts"]
    assert c["no_weight"] == int((np.isfinite(d) & (np.abs(d) <= 12.0) & np.isfinite(est) & (status == 0.0)
                                  & ~(np.isfinite(vol) & (vol > 0))).sum())  # fmt: skip
    assert got["n"] != [0] * 16 and sum(got["n"]) == int(ok.sum())


def test_the_answer_has_the_format_of_the_chart_of_the_samples(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    model, _ = step_model(p, BlobStore(p.folder))
    got = ec.blocks_profile(model, field="EST", a="A", b="B", domain_field="DOM", status_field="STATUS")
    holes = np.array([0, 0, 0, 0], dtype=np.int32)
    frm = np.array([0.0, 1.0, 2.0, 3.0])
    chart = sc.contact_chart(holes, frm, frm + 1.0, np.array([0, 0, 1, 1], dtype=np.int32), ["A", "B"], "A", "B",
                             np.array([1.0, 1.0, 3.0, 3.0]))  # fmt: skip
    assert got["edges"] == chart["edges"] and len(got["mean"]) == len(chart["mean"]) == len(got["n"])
    assert set(chart) - set(got) == {"contacts", "holes"}
    assert set(got) - set(chart) == {"model_id", "field", "volume"}
    assert set(got["a"]) == set(chart["a"]) and got["conventions"].startswith("Профиль блоков")


# --- sub-blocks: by the parent -----------------------------------------------------------------------------------


def test_sub_blocks_take_the_distance_of_their_parent(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    store = BlobStore(p.folder)
    counts = [4, 3, 2]
    grid = {"corner": [0.0, 0.0, 0.0], "counts": counts, "size": [2.0, 2.0, 2.0], "div": [2, 2, 2]}
    i, j, k = cell_ijk(counts)
    par = np.repeat((i + 4 * (j + 3 * k)).astype(np.uint64), 2)
    ci = np.repeat(i, 2)
    s = np.tile(np.array([[0, 0, 0], [1, 0, 0]], dtype=np.uint8), (len(i), 1))
    w = np.tile(np.array([[1, 2, 2], [1, 2, 2]], dtype=np.uint8), (len(i), 1))
    values = {"EST": np.where(ci < 2, 1.0, 3.0), "STATUS": np.zeros(len(par)),
              "DOM": np.where(ci < 2, 0, 1).astype(np.int32)}  # fmt: skip
    model = make(p, store, grid, par, values, s, w)
    d = ec.block_distance(model, field="DOM", a="A", b="B")
    want = np.where(ci < 2, -(2 - ci - 0.5) * 2.0, (ci - 2 + 0.5) * 2.0)
    assert np.abs(d - want).max() < TOL  # both halves of a parent: the distance of the parent
    got = ec.blocks_profile(model, field="EST", a="A", b="B", domain_field="DOM", status_field="STATUS")
    assert sum(got["n"]) == len(par) and got["a"]["mean"] == pytest.approx(1.0, abs=TOL)
    assert sum(got["volume"]) == pytest.approx(len(par) * 4.0, abs=1e-9)  # a sub-block of 1 × 2 × 2 m


def test_a_parent_that_holds_both_domains_is_at_the_contact(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    store = BlobStore(p.folder)
    grid = {"corner": [0.0, 0.0, 0.0], "counts": [3, 1, 1], "size": [2.0, 2.0, 2.0], "div": [2, 1, 1]}
    par = np.array([0, 1, 1, 2], dtype=np.uint64)
    s = np.array([[0, 0, 0], [0, 0, 0], [1, 0, 0], [0, 0, 0]], dtype=np.uint8)
    w = np.array([[2, 1, 1], [1, 1, 1], [1, 1, 1], [2, 1, 1]], dtype=np.uint8)
    values = {"EST": np.ones(4), "STATUS": np.zeros(4), "DOM": np.array([0, 0, 1, 1], dtype=np.int32)}
    model = make(p, store, grid, par, values, s, w)
    d = ec.block_distance(model, field="DOM", a="A", b="B")
    assert d[1] == 0.0 and math.copysign(1.0, d[1]) == -1.0 and d[2] == 0.0 and math.copysign(1.0, d[2]) == 1.0
    assert d[0] == pytest.approx(-1.0, abs=TOL) and d[3] == pytest.approx(1.0, abs=TOL)


# --- the domain of a wireframe: ТД28-3 -----------------------------------------------------------------------------


def cube_model(p: Any, store: BlobStore) -> Model:
    counts = [14, 14, 14]
    i, j, k = cell_ijk(counts)
    idx = i + 14 * (j + 14 * k)
    grid = {"corner": [-2.0, -2.0, -2.0], "counts": counts, "size": [1.0, 1.0, 1.0]}
    m0 = make(p, store, grid, idx, {"EST": np.zeros(len(idx)), "STATUS": np.zeros(len(idx)),
                                    "DOM": np.zeros(len(idx), dtype=np.int32)})  # fmt: skip
    c = m0.centres()
    inside = (c > 0).all(axis=1) & (c < 10).all(axis=1)
    return make(p, store, grid, idx, {"EST": np.where(inside, 1.0, 3.0), "STATUS": np.zeros(len(idx)),
                                      "DOM": np.zeros(len(idx), dtype=np.int32)})  # fmt: skip


def test_td28_3_the_distance_of_a_centre_to_a_cube_is_analytic(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    model = cube_model(p, BlobStore(p.folder))
    v, t = cube(2, 10.0, np.zeros(3))
    target = MeshTarget(v, t, solid="outward")
    d = ec.wireframe_distance(model, target)
    want = -box_sdf(model.centres(), np.zeros(3), 10.0)  # outside (A) below 0, inside (B) above 0
    assert np.abs(d - want).max() < 1e-9
    assert (d > 0).sum() == 1000 and (d < 0).sum() == 14**3 - 1000


def test_td28_2_the_means_of_a_step_function_at_a_wireframe(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    model = cube_model(p, BlobStore(p.folder))
    v, t = cube(2, 10.0, np.zeros(3))
    got = ec.blocks_profile(model, field="EST", wireframe=MeshTarget(v, t, solid="outward"),
                            status_field="STATUS", distance=10.0, step=0.5)  # fmt: skip
    assert got["a"]["label"] == "снаружи" and got["b"]["label"] == "внутри"
    assert got["a"]["mean"] == pytest.approx(3.0, abs=TOL) and got["b"]["mean"] == pytest.approx(1.0, abs=TOL)
    for q, edge in enumerate(got["edges"][:-1]):
        if got["n"][q]:
            assert abs(got["mean"][q] - (3.0 if edge < 0 else 1.0)) < TOL
        else:
            assert got["mean"][q] is None
    assert got["b"]["n"] == 1000 and got["a"]["n"] == 14**3 - 1000
    assert sum(got["volume"]) == pytest.approx(14.0**3, abs=1e-9)
    whole = ec.blocks_profile(model, field="EST", wireframe=MeshTarget(v, t, solid="outward"),
                              status_field="STATUS", distance=10.0, step=0.5)  # fmt: skip
    assert whole == got
    short = ec.blocks_profile(model, field="EST", wireframe=MeshTarget(v, t, solid="outward"),
                              status_field="STATUS", distance=3.0, step=1.0)  # fmt: skip
    assert short["counts"]["beyond"] > 0 and short["counts"]["in_steps"] == sum(short["n"])
    inside = [q for q, e in enumerate(got["edges"][:-1]) if e >= 0 and got["n"][q]]
    assert [got["edges"][q] for q in inside] == [0.5, 1.5, 2.5, 3.5, 4.5]  # the layers of 1 m, 0.5 m a step


# --- refusals ------------------------------------------------------------------------------------------------------


def test_refusals_are_russian_and_name_the_parameter(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    model, _ = step_model(p, BlobStore(p.folder))
    base: dict[str, Any] = {"field": "EST", "a": "A", "b": "B", "domain_field": "DOM"}

    def refused(**over: Any) -> ApiError:
        with pytest.raises(ApiError) as e:
            ec.blocks_profile(model, **{**base, **over})
        assert e.value.message and e.value.hint
        return e.value

    assert refused(a="Z").extra == {"param": "a"}
    assert refused(b=None).extra == {"param": "b"}
    assert refused(b="A").extra == {"param": "b"}
    assert refused(field="DOM").code == "field_not_number"
    assert refused(domain_field="EST").code == "field_not_category"
    assert refused(status_field="DOM").code == "field_not_number"
    assert refused(field="нет такого").code == "block_field_not_found"
    assert refused(domain_field=None).extra == {"param": "domain_field"}
    v, t = cube(1, 4.0, np.zeros(3))
    assert refused(wireframe=MeshTarget(v, t)).code == "bad_param"  # both a field and a wireframe
    with pytest.raises(StatsError) as e:
        ec.blocks_profile(model, **{**base, "step": 0})
    assert e.value.code == "bad_param"
    with pytest.raises(StatsError) as e:  # nothing of C at all: no block in the steps
        ec.blocks_profile(model, **{**base, "a": "C", "b": "B"})
    assert e.value.code == "no_values"


def test_tick_runs_to_one_and_may_cancel(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    p = project(bc)
    model, _ = step_model(p, BlobStore(p.folder))
    ticks: list[float] = []
    ec.blocks_profile(model, field="EST", a="A", b="B", domain_field="DOM", tick=ticks.append)
    assert ticks and ticks[-1] == pytest.approx(1.0) and ticks == sorted(ticks)

    class Stop(Exception):
        pass

    def stop(_f: float) -> None:
        raise Stop

    with pytest.raises(Stop):
        ec.blocks_profile(model, field="EST", a="A", b="B", domain_field="DOM", tick=stop)


def test_geometry_helpers_agree_with_the_rows_of_the_model(bc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    """`row_distance` reads the cells by P as the model does: a model with a gap (not every parent is a row)."""
    p = project(bc)
    store = BlobStore(p.folder)
    counts = [5, 4, 3]
    i, j, k = cell_ijk(counts)
    idx = i + 5 * (j + 4 * k)
    keep = (idx % 3) != 1
    values = {"EST": np.ones(keep.sum()), "STATUS": np.zeros(keep.sum()),
              "DOM": np.where(i[keep] < 2, 0, 1).astype(np.int32)}  # fmt: skip
    model = make(p, store, {"corner": [0.0, 0.0, 0.0], "counts": counts, "size": [1.0, 1.0, 1.0]}, idx[keep], values)
    d = ec.block_distance(model, field="DOM", a="A", b="B")
    ijk = np.stack([a.astype(np.int64) for a in geometry.row_ijk(model.grid, model.p())], axis=1)
    codes = model.columns(["DOM"])["DOM"]
    assert d.shape == (model.rows,) == (int(keep.sum()),)
    for r in range(model.rows):  # the full search over the cells that are there: the nearest of the other domain
        other = np.flatnonzero(codes != codes[r])
        far = np.linalg.norm(ijk[other] - ijk[r], axis=1)
        near = other[far <= far.min() + 1e-9]
        m = np.abs(ijk[near] - ijk[r]).max(axis=1)
        allowed = far.min() * (1.0 - 0.5 / m)
        assert np.abs(abs(d[r]) - allowed).min() < TOL and (d[r] < 0) == (codes[r] == 0)


def test_warm() -> None:
    ec.warm()
