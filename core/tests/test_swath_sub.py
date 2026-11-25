"""Swath of a block model with a turn or sub-blocks and with a filter of the library (подшаг 17.10; ADR 0023
decision 21; ТЗ 7.30, 7.31, 7.35 Д-02): the share of a box below a plane against the closed formula; a model of
sub-blocks = the same model cut into its sub-cells (volume, tonnage and means to 1e−9; turned about Z, an oblique
frame); the windows on the edges of the blocks against a brute-force count; a filter = the blocks selected beforehand
(Э13 model and v2 model); the bits at 1, 2, 4, 8 threads; 422 `blocks_unsupported` is gone; swath of Э13 is the
tests `test_swath_api`, `test_swath_blocks`."""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pytest

from bf_core.blocks import store as bm
from bf_core.blocks.filters import store as filters
from bf_core.blocks.model import Model
from bf_core.db import dialect
from bf_core.projects.service import projects_of
from bf_core.stats import blocks_cache as bc
from bf_core.stats import routes_swath
from bf_core.stats import swath as sw
from bf_core.stats import swath_frame as sf
from bf_core.stats import swath_sub as ss
from bf_core.storage.blobs import BlobStore

FIELDS = [
    {"name": "AU", "dtype": "f8", "unit": "г/т", "role": "grade"},
    {"name": "DENS", "dtype": "f8", "role": "density"},
    {"name": "DOM", "dtype": "cat", "labels": ["руда", "порода", "дайка"], "role": "code"},
]
GRID = {"corner": [1000.5, 2000.25, -300.0], "counts": [6, 5, 4], "size": [10.0, 10.0, 5.0], "rotation": 30}
DIV = [2, 2, 2]


@pytest.fixture()
def prj(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post("/api/v1/projects", json={"name": "Swath"}).status_code == 200
    bc.MODELS.clear()
    ss.MASKS.clear()
    return projects_of(pclient.core).require_current()


def rows_of(seed: int = 5, parents: int = 90) -> dict[str, np.ndarray]:
    """Rows (P, s, w) of a model with div 2·2·2: whole parents, halves along z, unit cubes (a few of 8)."""
    rng = np.random.default_rng(seed)
    p, s, w = [], [], []
    for par in np.sort(rng.choice(6 * 5 * 4, size=parents, replace=False)):
        kind = int(rng.integers(0, 3))
        if kind == 0:
            rows = [((0, 0, 0), (2, 2, 2))]
        elif kind == 1:
            rows = [((0, 0, 0), (2, 2, 1)), ((0, 0, 1), (2, 2, 1))]
        else:
            codes = np.unique(rng.integers(0, 8, int(rng.integers(1, 6))))  # (sz, sy, sx) ascending
            rows = [(((c & 1), (c >> 1) & 1, c >> 2), (1, 1, 1)) for c in codes]
        for o, ww in rows:
            p.append(par)
            s.append(o)
            w.append(ww)
    n = len(p)
    au = rng.lognormal(0.0, 1.0, n)
    au[rng.random(n) < 0.1] = np.nan
    return {"p": np.asarray(p, np.uint64), "s": np.asarray(s, np.int64), "w": np.asarray(w, np.int64), "au": au,
            "dens": rng.uniform(1.8, 3.2, n), "dom": rng.integers(-1, 3, n).astype(np.int32)}  # fmt: skip


def put(p: Any, grid: dict[str, Any], rows: dict[str, np.ndarray], name: str, sub: bool = True) -> int:
    store = BlobStore(p.folder)
    with bm.CellsWriterV2(store, grid, FIELDS, row_group=64) as wr:
        wr.write(rows["p"], {"AU": rows["au"], "DENS": rows["dens"], "DOM": rows["dom"]},
                 rows["s"] if sub else None, rows["w"] if sub else None)  # fmt: skip
        staged = wr.finish()
    try:
        with dialect.write_tx(p.engine) as conn:
            return bm.commit_block_model(conn, store, staged, name=name)
    finally:
        staged.discard()


def cells_of_rows(rows: dict[str, np.ndarray], counts: tuple[int, int, int]) -> dict[str, np.ndarray]:
    """The same rows cut into unit sub-cells of a model without sub-blocks (counts × div)."""
    nx, ny = counts[0] * 2, counts[1] * 2
    ps, au, dens, dom = [], [], [], []
    for r in range(len(rows["p"])):
        par = int(rows["p"][r])
        i, j, k = par % counts[0], (par // counts[0]) % counts[1], par // (counts[0] * counts[1])
        sx, sy, sz = rows["s"][r]
        wx, wy, wz = rows["w"][r]
        for c, b, a in itertools.product(range(wz), range(wy), range(wx)):
            ps.append((i * 2 + sx + a) + nx * ((j * 2 + sy + b) + ny * (k * 2 + sz + c)))
            au.append(rows["au"][r])
            dens.append(rows["dens"][r])
            dom.append(rows["dom"][r])
    order = np.argsort(ps)
    return {"p": np.asarray(ps, np.uint64)[order], "au": np.asarray(au)[order], "dens": np.asarray(dens)[order],
            "dom": np.asarray(dom, np.int32)[order]}  # fmt: skip


def pair(p: Any, rotation: float = 30.0) -> tuple[int, int, dict[str, np.ndarray]]:
    """A model with sub-blocks and its copy cut into the sub-cells (a regular model of the same axes)."""
    rows = rows_of()
    grid = {**GRID, "rotation": rotation}
    sub_id = put(p, {**grid, "div": DIV}, rows, "Субблоки")
    cut = cells_of_rows(rows, (6, 5, 4))
    cut_grid = {**grid, "counts": [12, 10, 8], "size": [5.0, 5.0, 2.5]}
    return sub_id, put(p, cut_grid, cut, "Подъячейки", sub=False), rows


def source(p: Any, model_id: int) -> ss.Source:
    return ss.open_source(p, model_id)


def cuboid_of(src: ss.Source, f: sf.Frame, width: float, anchor: float | None = None, **kw: Any) -> Any:
    ext = ss.bounds(src, f, **kw)
    return sw.cuboid(f, models=[ext], width=width, anchor=anchor)


# --- the share of a box below a plane ---------------------------------------------------------------------------


def closed_form(x: float, b: list[float]) -> float:
    """The distribution function of a sum of uniform terms (Σ (−1)^|S| (x − Σ_S b)₊^m / (m!·Π b))."""
    b = [v for v in b if v > 0.0]
    m = len(b)
    total = sum((-1) ** r * max(x - sum(b[i] for i in c), 0.0) ** m
                for r in range(m + 1) for c in itertools.combinations(range(m), r))  # fmt: skip
    return total / (math.factorial(m) * math.prod(b))


def test_cdf_is_the_distribution_of_a_sum_of_uniforms() -> None:
    rng = np.random.default_rng(1)
    worst = 0.0
    for _ in range(1500):
        m = int(rng.integers(1, 4))
        b = [*rng.uniform(0.2, 5.0, m), *([0.0] * (3 - m))]
        rng.shuffle(b)
        x = float(rng.uniform(-0.5, sum(b) + 0.5))
        ref = 0.0 if x <= 0.0 else 1.0 if x >= sum(b) else closed_form(x, b)
        worst = max(worst, abs(ss.cdf(x, b) - ref))
    assert worst < 1e-13
    for b in ([1.0, 1.0, 0.01], [3.0, 0.02, 1.0], [0.015, 0.7, 2.0]):  # a small amplitude: the nested rule
        for x in np.linspace(0.0, sum(b), 23):
            ref = 0.0 if x <= 0.0 else 1.0 if x >= sum(b) else closed_form(float(x), b)
            assert abs(ss.cdf(float(x), b) - ref) < 1e-11
    for tiny in (1e-6, 1e-9, 1e-12):  # a small amplitude changes the share by what it can: tiny / 6 at most
        xs = np.linspace(0.0, 5.0, 11)
        assert max(abs(ss.cdf(x, [3.0, 2.0, tiny]) - ss.cdf(x, [3.0, 2.0, 0.0])) for x in xs) < tiny
    assert ss.cdf(0.0, [1.0, 0.0, 0.0]) == 0.0 and ss.cdf(1.0, [1.0, 0.0, 0.0]) == 1.0


# --- a model of sub-blocks = its sub-cells ----------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [0.0, 30.0, 90.0])
@pytest.mark.parametrize("angles", [(90.0, 0.0, 0.0), (37.0, 0.0, 0.0), (20.0, -25.0, 15.0), (0.0, 90.0, 0.0)])
def test_subblocks_equal_their_subcells(prj, rotation: float, angles: tuple[float, float, float]) -> None:
    sub_id, cut_id, rows = pair(prj, rotation)
    a, b = source(prj, sub_id), source(prj, cut_id)
    f = sf.frame(*angles)
    ca, cb = (cuboid_of(s, f, 7.0) for s in (a, b))
    assert np.allclose(ss.bounds(a, f), ss.bounds(b, f), rtol=0, atol=1e-9)
    assert (ca.start, ca.width, ca.count) == pytest.approx((cb.start, cb.width, cb.count), abs=1e-9)
    for weight in ("volume", "tonnage"):
        x = ss.series(a, f, ca, field="AU", weight=weight, density="DENS")
        y = ss.series(b, f, ca, field="AU", weight=weight, density="DENS")
        assert np.allclose(x.weight, y.weight, rtol=1e-9, atol=0), weight
        assert np.allclose(x.mean, y.mean, rtol=1e-9, atol=0, equal_nan=True), weight
        assert x.total["weight"] == pytest.approx(y.total["weight"], rel=1e-9)
    # the volume of the model is split between the swaths, not lost or counted twice
    with dialect.read_tx(prj.engine) as conn:
        model = Model.open(conn, BlobStore(prj.folder), sub_id)
    ok = np.isfinite(rows["au"])
    vol = model.volume()[ok].sum()
    full = ss.series(a, f, ca, field="AU", weight="volume")
    assert full.total["weight"] == pytest.approx(vol, rel=1e-9) and full.counts["missing"] == int((~ok).sum())
    assert full.counts["rows"] == len(rows["p"]) == full.counts["selected"] + full.counts["missing"]


def test_a_turned_model_against_dense_points(prj) -> None:
    """The exact split against a brute-force one: points of a fine grid inside every block (1e−3)."""
    sub_id, _, _ = pair(prj, 30.0)
    src = source(prj, sub_id)
    f = sf.frame(37.0, 0.0, 0.0)
    c = cuboid_of(src, f, 7.0)
    got = ss.series(src, f, c, field="AU", weight="volume")
    model = src.model
    want = np.zeros(c.count)
    mid = (np.arange(16) + 0.5) / 16.0
    for ch in model.iter(["AU"]):
        for r in range(ch.n):
            if not np.isfinite(ch.cols["AU"][r]):
                continue
            lo_hi = model.boxes(np.array([ch.start + r]))[0]
            vol = float(model.volume(np.array([ch.start + r]))[0])
            q = np.array(list(itertools.product(*(lo_hi[a] + (lo_hi[a + 3] - lo_hi[a]) * mid for a in range(3)))))
            t = sf.project(model.grid.to_project(q), f.u)
            k = np.clip(np.floor((t - c.start) / c.width).astype(int), 0, c.count - 1)
            want += vol * np.bincount(k, minlength=c.count) / len(q)
    assert np.allclose(got.weight, want, rtol=0, atol=2e-3 * want.max())
    assert got.total["weight"] == pytest.approx(want.sum(), rel=1e-12)


def test_windows_on_the_edges_of_the_blocks(prj) -> None:
    """A swath of X of a model without a turn, the windows on the edges of the sub-cells: every window takes what
    the blocks have inside it, counted by the boxes; a block on an edge goes to no window twice."""
    rows = rows_of(seed=9)
    mid = put(prj, {**GRID, "rotation": 0, "div": DIV}, rows, "Прямая")
    src = source(prj, mid)
    f = sf.frame(90.0)
    ext = ss.bounds(src, f)
    c = sw.cuboid(f, models=[ext], width=5.0, anchor=1000.5)
    assert c.start == 1000.5 and c.width == 5.0
    got = ss.series(src, f, c, field="AU", weight="volume")
    boxes = src.model.boxes()
    vol = src.model.volume()
    ok = np.isfinite(rows["au"])
    want = np.zeros(c.count)
    cnt = np.zeros(c.count, dtype=int)
    for r in np.flatnonzero(ok):
        x0, x1 = boxes[r, 0] + 1000.5, boxes[r, 3] + 1000.5
        for k in range(c.count):
            lap = max(0.0, min(x1, c.start + (k + 1) * 5.0) - max(x0, c.start + k * 5.0))
            if lap > 1e-12:
                want[k] += vol[r] * lap / (x1 - x0)
                cnt[k] += 1
    assert np.allclose(got.weight, want, rtol=1e-12, atol=0)
    assert np.array_equal(got.count, cnt)
    assert got.counts["outside_box"] == 0


def test_the_cuboid_limits_cut_the_blocks(prj) -> None:
    sub_id, _, _ = pair(prj, 30.0)
    src = source(prj, sub_id)
    f = sf.frame(37.0, 0.0, 0.0)
    ext = ss.bounds(src, f)
    lo, hi = ext[0]
    mid = (lo + hi) / 2
    c = sw.cuboid(f, models=[ext], limits={"u": [mid, hi], "v": ext[1].tolist(), "w": ext[2].tolist()}, width=6.0)
    got = ss.series(src, f, c, field="AU", weight="volume")
    full = ss.series(src, f, sw.cuboid(f, models=[ext], width=6.0), field="AU", weight="volume")
    assert 0.3 * full.total["weight"] < got.total["weight"] < 0.7 * full.total["weight"]
    assert got.counts["outside_box"] > 0 and got.weight[0] > 0.0


def test_frac_is_the_share_of_the_volume(prj) -> None:
    rows = rows_of(seed=3, parents=20)
    fields = [*FIELDS, {"name": "FRAC", "dtype": "f8", "role": "frac"}]
    frac = np.random.default_rng(3).uniform(0.2, 1.0, len(rows["p"]))
    store = BlobStore(prj.folder)
    with bm.CellsWriterV2(store, {**GRID, "rotation": 0, "div": DIV}, fields, row_group=64) as wr:
        wr.write(rows["p"], {"AU": rows["au"], "DENS": rows["dens"], "DOM": rows["dom"], "FRAC": frac},
                 rows["s"], rows["w"])  # fmt: skip
        staged = wr.finish()
    with dialect.write_tx(prj.engine) as conn:
        mid = bm.commit_block_model(conn, store, staged, name="FRAC")
    staged.discard()
    src = source(prj, mid)
    assert src.frac == "FRAC"
    f = sf.frame(90.0)
    c = cuboid_of(src, f, 10.0)
    got = ss.series(src, f, c, field="AU", weight="volume")
    ok = np.isfinite(rows["au"])
    assert got.total["weight"] == pytest.approx(src.model.volume()[ok].sum(), rel=1e-12)
    none = ss.series(src, f, c, field="AU", weight="none")
    assert none.total["weight"] == pytest.approx(float(ok.sum()), rel=1e-12)  # a count: no FRAC


def test_the_bits_do_not_depend_on_the_threads(prj, monkeypatch: pytest.MonkeyPatch) -> None:
    sub_id, _, _ = pair(prj, 30.0)
    src = source(prj, sub_id)
    monkeypatch.setattr(ss, "GROUP", 32)  # 6 groups of the model
    f = sf.frame(20.0, -25.0, 15.0)
    c = cuboid_of(src, f, 6.0)
    first = ss.series(src, f, c, field="AU", weight="tonnage", density="DENS", threads=1)
    for threads in (2, 4, 8):
        other = ss.series(src, f, c, field="AU", weight="tonnage", density="DENS", threads=threads)
        assert np.array_equal(other.weight, first.weight) and np.array_equal(other.mean, first.mean, equal_nan=True)
        assert np.array_equal(ss.bounds(src, f, threads=threads), ss.bounds(src, f, threads=1))


def test_a_variable_step_with_a_turn(prj) -> None:
    """Tensors (OMF) and a turn: the boxes are the cells of the tensors."""
    grid = {"corner": [10.0, 20.0, 0.0], "counts": [3, 2, 2], "tensors": {"x": [2.0, 3.0, 4.0], "y": [5.0, 1.0],
            "z": [1.0, 2.0]}, "rotation": 90}  # fmt: skip
    rows = {"p": np.arange(12, dtype=np.uint64), "au": np.arange(12, dtype=np.float64) + 1.0,
            "dens": np.ones(12), "dom": np.zeros(12, dtype=np.int32)}  # fmt: skip
    mid = put(prj, grid, rows, "Тензоры", sub=False)
    src = source(prj, mid)
    f = sf.frame(90.0)
    c = cuboid_of(src, f, 3.0)
    got = ss.series(src, f, c, field="AU", weight="volume")
    assert got.total["weight"] == pytest.approx(9.0 * 6.0 * 3.0, rel=1e-12)  # Σ steps along x, y, z
    sizes = np.array([sx * sy * sz for sz in (1.0, 2.0) for sy in (5.0, 1.0) for sx in (2.0, 3.0, 4.0)])
    assert got.total["mean"] == pytest.approx(float((rows["au"] * sizes).sum() / sizes.sum()), rel=1e-12)
    # a turn by 90° puts the local y along the world x: the layers of y are the swaths, 5 m and 1 m wide
    assert (got.weight > 0).sum() >= 2


# --- the filter of the library -----------------------------------------------------------------------------------


def make_filter(p: Any, expr: str, name: str = "Ф") -> int:
    with dialect.write_tx(p.engine) as conn:
        return filters.create(conn, {"name": name, "expr": expr})


def plain_rows(n: int = 600, seed: int = 4) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    p = np.sort(rng.choice(12 * 10 * 8, size=n, replace=False)).astype(np.uint64)
    au = rng.lognormal(0.0, 1.0, n)
    au[rng.random(n) < 0.1] = np.nan
    return {"p": p, "au": au, "dens": rng.uniform(1.8, 3.2, n), "dom": rng.integers(-1, 3, n).astype(np.int32)}


PLAIN = {"corner": [100.0, 200.0, 0.0], "counts": [12, 10, 8], "size": [5.0, 5.0, 2.5], "rotation": 0}


def swath_of(p: Any, *series: dict[str, Any], **kw: Any) -> dict[str, Any]:
    body = {"v": 1, "direction": "x", "azimuth": 0, "dip": 0, "roll": 0, "width": None, "anchor": None,
            "limits": None, "series": list(series), **kw}  # fmt: skip
    return routes_swath.swath_of(p, body)


def blk(model_id: int, **kw: Any) -> dict[str, Any]:
    return {"kind": "blocks", "model_id": model_id, "field": "AU", "weight": "volume", "density": None,
            "filter": None, **kw}  # fmt: skip


@pytest.mark.parametrize("direction", ["x", "z"])
def test_filter_of_a_plain_model_is_the_blocks_selected_beforehand(prj, direction: str) -> None:
    rows = plain_rows()
    fid = make_filter(prj, "AU > 1")
    full = put(prj, PLAIN, rows, "Полная", sub=False)
    keep = np.nan_to_num(rows["au"], nan=-1.0) > 1.0
    part = put(prj, PLAIN, {k: v[keep] for k, v in rows.items()}, "Отобранная", sub=False)
    got = swath_of(prj, blk(full, filter_id=fid), direction=direction, width=10.0)
    want = swath_of(prj, blk(part), direction=direction, width=10.0)
    a, b = got["series"][0], want["series"][0]
    assert a["error"] is None and b["error"] is None
    assert got["box"] == want["box"] and got["count"] == want["count"]
    assert np.allclose(np.array(a["mean"], dtype=float), np.array(b["mean"], dtype=float), rtol=1e-12, atol=0,
                       equal_nan=True)  # fmt: skip
    assert np.allclose(np.array(a["weights"], dtype=float), np.array(b["weights"], dtype=float), rtol=1e-12,
                       atol=0, equal_nan=True)  # fmt: skip
    assert a["count"] == b["count"] and a["counts"]["filtered_out"] == len(rows["p"]) - int(keep.sum())


def test_filter_with_a_category_selection_and_the_cache_of_the_mask(prj) -> None:
    rows = plain_rows()
    fid = make_filter(prj, "AU > 1")
    full = put(prj, PLAIN, rows, "Полная", sub=False)
    both = (np.nan_to_num(rows["au"], nan=-1.0) > 1.0) & (rows["dom"] == 1)
    part = put(prj, PLAIN, {k: v[both] for k, v in rows.items()}, "Отобранная", sub=False)
    cat = {"field": "DOM", "labels": ["порода"]}
    got = swath_of(prj, blk(full, filter_id=fid, filter=cat), width=10.0)["series"][0]
    want = swath_of(prj, blk(part), width=10.0)["series"][0]
    assert np.allclose(np.array(got["mean"], dtype=float), np.array(want["mean"], dtype=float), rtol=1e-12, atol=0,
                       equal_nan=True)  # fmt: skip
    assert len(ss.MASKS) == 1
    held = next(iter(ss.MASKS.values()))[0]
    swath_of(prj, blk(full, filter_id=fid), direction="y")  # a turn of the box: the filter is not counted again
    assert len(ss.MASKS) == 1 and next(iter(ss.MASKS.values()))[0] is held and not held.flags.writeable


def test_the_masks_of_two_filters_are_made_at_the_same_time_and_one_of_a_key_once(prj, monkeypatch) -> None:
    import threading

    from bf_core.blocks import restrict

    mid = put(prj, PLAIN, plain_rows(), "М", sub=False)
    f1, f2 = make_filter(prj, "AU > 1", "Ф1"), make_filter(prj, "AU > 2", "Ф2")
    started, release, calls = threading.Event(), threading.Event(), []
    original = restrict.Restrictor.iter

    def slow(self, *a, **k):  # type: ignore[no-untyped-def]
        calls.append(1)
        if not started.is_set():
            started.set()
            assert release.wait(20)  # the first mask is being made until the test lets it go
        return original(self, *a, **k)

    monkeypatch.setattr(restrict.Restrictor, "iter", slow)
    out: dict[str, Any] = {}
    first = threading.Thread(target=lambda: out.update(a=ss.filter_mask(prj, mid, f1)))
    first.start()
    assert started.wait(20)
    other = threading.Thread(target=lambda: out.update(b=ss.filter_mask(prj, mid, f2)))
    same = threading.Thread(target=lambda: out.update(c=ss.filter_mask(prj, mid, f1)))
    other.start()
    other.join(10)
    assert not other.is_alive() and "b" in out  # another key is not held by the mask being made
    same.start()  # the same key waits for the first and takes its mask
    release.set()
    first.join(20)
    same.join(20)
    assert out["c"][0] is out["a"][0] and len(calls) == 2 and not ss._KEY_LOCKS


def test_filter_of_a_model_with_sub_blocks_is_the_rows_selected_beforehand(prj) -> None:
    rows = rows_of()
    grid = {**GRID, "div": DIV}
    full = put(prj, grid, rows, "Полная")
    fid = make_filter(prj, "AU > 1 AND DENS < 2.8")
    keep = (np.nan_to_num(rows["au"], nan=-1.0) > 1.0) & (rows["dens"] < 2.8)
    part = put(prj, grid, {k: v[keep] for k, v in rows.items()}, "Отобранная")
    a = swath_of(prj, blk(full, filter_id=fid), direction="angles", azimuth=37, dip=-20, roll=10, width=8.0)
    b = swath_of(prj, blk(part), direction="angles", azimuth=37, dip=-20, roll=10, width=8.0)
    x, y = a["series"][0], b["series"][0]
    assert x["error"] is None and y["error"] is None
    assert a["box"] == b["box"] and x["count"] == y["count"]
    assert np.allclose(np.array(x["mean"], dtype=float), np.array(y["mean"], dtype=float), rtol=1e-12, atol=0,
                       equal_nan=True)  # fmt: skip
    assert np.allclose(np.array(x["weights"], dtype=float), np.array(y["weights"], dtype=float), rtol=1e-12,
                       atol=0, equal_nan=True)  # fmt: skip
    assert x["counts"]["filtered_out"] == len(rows["p"]) - int(keep.sum())
    assert any("делятся между полосами" in n for n in a["notes"])


def test_the_filter_errors_are_those_of_the_series(prj) -> None:
    mid = put(prj, PLAIN, plain_rows(), "М", sub=False)
    lost = swath_of(prj, blk(mid), blk(mid, filter_id=987654))["series"][1]
    assert lost["error"]["code"] == "filter_not_found" and lost["error"]["hint"]
    needs = make_filter(prj, "ZZZ > 1", "Нужен ZZZ")
    missing = swath_of(prj, blk(mid), blk(mid, filter_id=needs))["series"][1]
    assert missing["error"]["code"] == "filter_field_missing" and missing["error"]["hint"]
    with pytest.raises(Exception) as e:  # not a number
        swath_of(prj, blk(mid, filter_id="x"))
    assert getattr(e.value, "code", None) == "bad_param"
    assert swath_of(prj, blk(mid, filter_id=None))["series"][0]["error"] is None


# --- 422 `blocks_unsupported` is gone ---------------------------------------------------------------------------


def test_a_turn_and_sub_blocks_are_read_by_swath(prj) -> None:
    sub_id, cut_id, _ = pair(prj, 30.0)
    plain = put(prj, PLAIN, plain_rows(), "Прямая", sub=False)
    with dialect.read_tx(prj.engine) as conn:
        assert bc.model_of(conn, sub_id).plain is False and bc.model_of(conn, cut_id).plain is False
        m = bc.model_of(conn, plain)
    assert m.plain and "rotation" not in m.grid and "div" not in m.grid
    for model_id in (sub_id, cut_id):
        out = swath_of(prj, blk(model_id), direction="angles", azimuth=37, dip=0, roll=0)
        s = out["series"][0]
        assert s["error"] is None and s["counts"]["used"] > 0 and out["count"] > 1
        assert out["block"] is not None and out["width"] > 0.0
    one = swath_of(prj, blk(sub_id, weight="tonnage", density="DENS"), width=7.0)["series"][0]
    two = swath_of(prj, blk(cut_id, weight="tonnage", density="DENS"), width=7.0)["series"][0]
    assert one["error"] is None
    assert np.allclose(np.array(one["weights"], dtype=float), np.array(two["weights"], dtype=float), rtol=1e-9,
                       equal_nan=True)  # fmt: skip


def test_samples_inside_the_blocks_of_a_turned_model_are_refused() -> None:
    blocks = routes_swath.Series(0, "blocks", "AU", "volume", 1)
    blocks.sub = object()
    inside = routes_swath.Series(1, "samples", "AU", "length", 2)
    inside.within = -1
    routes_swath._link([blocks, inside])
    assert inside.error is not None and inside.error["code"] == "blocks_unsupported" and inside.error["hint"]
