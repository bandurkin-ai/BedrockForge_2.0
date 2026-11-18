"""Parts and levels of detail of a line layer for 3D (ADR 0011 §3, ADR 0004 §2): Douglas–Peucker against a
reference written here and against a circle with a known answer; every dropped vertex within the tolerance of
its segment; ends, closed rings and empty heights kept; parts of at most 256 000 vertices and 16 km with a
whole-metre origin; float32 offsets within 0.25 mm; BFA1 `lines`, text labels, ETag and 304."""

from __future__ import annotations

import math
import time

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.formats.lineset import LineSet, from_lines, from_points
from bf_core.geo.simplify import MAX_RUN, compact, keep_mask, simplify
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from bf_core.viz import lines as vl
from tests.test_import_job import API

pytestmark = pytest.mark.windows

UTM = np.array([512_345.678_901, 6_234_567.123_456, 412.345_678])
F32 = 2.5e-4  # float32 rounding of an offset below 8192 m, per axis


# --- reference -------------------------------------------------------------------------------------------


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray, flat: bool = False) -> np.ndarray:
    """Distance of the points p (k×3) from the segment a–b; in plan when `flat`. Plain numpy."""
    if flat:
        p, a, b = p[:, :2], a[:2], b[:2]
    d = b - a
    len2 = float(d @ d)
    t = np.clip((p - a) @ d / len2, 0.0, 1.0) if len2 > 0 else np.zeros(len(p))
    return np.asarray(np.linalg.norm(p - a - t[:, None] * d, axis=1))


def _ref_keep(xyz: np.ndarray, offsets: np.ndarray, tol: float, closed: np.ndarray, max_run: int) -> np.ndarray:
    """The rules of `geo/simplify.py` written the slow way: numpy, one line and one chord at a time."""
    keep = np.zeros(len(xyz), dtype=bool)
    nan = np.isnan(xyz[:, 2])

    def farthest(lo: int, hi: int, flat: bool) -> tuple[int, float]:
        if hi - lo < 2:
            return -1, -1.0
        d = _seg_dist(xyz[lo + 1 : hi], xyz[lo], xyz[hi], flat)
        return lo + 1 + int(np.argmax(d)), float(d.max())

    def dp(lo: int, hi: int) -> None:
        todo = [(lo, hi)]  # a comb splits one vertex at a time: deeper than Python recursion goes
        while todo:
            lo, hi = todo.pop()
            at, d = farthest(lo, hi, bool(nan[lo]))
            if at >= 0 and d > tol:
                keep[at] = True
                todo += [(lo, at), (at, hi)]

    for line in range(len(offsets) - 1):
        a, b = int(offsets[line]), int(offsets[line + 1]) - 1
        keep[[a, b]] = True
        if b - a < 2:
            continue
        change = a + 1 + np.flatnonzero(nan[a + 1 : b + 1] != nan[a:b])
        keep[change] = keep[change - 1] = True
        keep[a + max_run : b : max_run] = True
        if closed[line] and b - a >= 3:
            flat = bool(len(change)) or bool(nan[a])
            d = _seg_dist(xyz[a + 1 : b], xyz[a], xyz[a], flat)
            far = a + 1 + int(np.argmax(d))
            keep[far] = True
            (m1, d1), (m2, d2) = farthest(a, far, flat), farthest(far, b, flat)
            if m1 >= 0 and (m2 < 0 or d1 >= d2):
                keep[m1] = True
            elif m2 >= 0:
                keep[m2] = True
        marks = a + np.flatnonzero(keep[a : b + 1])
        for lo, hi in zip(marks[:-1].tolist(), marks[1:].tolist(), strict=True):
            dp(lo, hi)
    return keep


def _worst(xyz: np.ndarray, offsets: np.ndarray, keep: np.ndarray) -> float:
    """Largest distance of a dropped vertex from the segment between the kept vertices around it (in plan
    where a height is empty)."""
    worst = 0.0
    for line in range(len(offsets) - 1):
        a, b = int(offsets[line]), int(offsets[line + 1])
        marks = a + np.flatnonzero(keep[a:b])
        for lo, hi in zip(marks[:-1].tolist(), marks[1:].tolist(), strict=True):
            if hi - lo > 1:
                flat = bool(np.isnan(xyz[lo : hi + 1, 2]).any())
                worst = max(worst, float(_seg_dist(xyz[lo + 1 : hi], xyz[lo], xyz[hi], flat).max()))
    return worst


def _walk(rng: np.random.Generator, n: int, step: float = 1.0, wobble: float = 0.05) -> np.ndarray:
    """A smooth contour: steps of `step` metres, the heading drifts by `wobble` radians a step."""
    heading = np.cumsum(rng.normal(0.0, wobble, n)) + rng.uniform(0, 2 * math.pi)
    xy = np.cumsum(np.column_stack([np.cos(heading), np.sin(heading)]) * step, axis=0)
    z = np.cumsum(rng.normal(0.0, 0.02 * step, n))
    return UTM + np.column_stack([xy, z])


def _ring(radius: float, n: int, centre: np.ndarray = UTM, tilt: float = 0.0) -> np.ndarray:
    t = np.arange(n) * (2 * math.pi / n)
    pts = centre + np.column_stack([radius * np.cos(t), radius * np.sin(t), tilt * radius * np.cos(t)])
    return np.vstack([pts, pts[:1]])


def _pack(lines: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    offsets = np.zeros(len(lines) + 1, dtype=np.int64)
    offsets[1:] = np.cumsum([len(v) for v in lines])
    return np.vstack(lines), offsets


def _hard_lines(rng: np.random.Generator) -> tuple[list[np.ndarray], list[int]]:
    """Lines that are hard for a simplifier, at UTM coordinates."""
    spur = UTM + np.array([[0, 0, 0], [100, 0, 0], [1, 0, 0]], dtype=float)  # runs past the end of its chord
    same = np.repeat(UTM[None, :], 7, axis=0)  # seven vertices in one place
    doubled = np.repeat(_walk(rng, 200), 2, axis=0)  # every vertex twice: segments of zero length
    no_z = _walk(rng, 500)
    no_z[:, 2] = math.nan
    mixed = _walk(rng, 600)
    mixed[100:250, 2] = math.nan
    mixed[400, 2] = math.nan
    straight = UTM + np.linspace(0.0, 1.0, 1000)[:, None] * np.array([900.0, -700.0, 35.0])
    comb = UTM + np.column_stack(
        [np.arange(3000.0), np.where(np.arange(3000) % 2, 1.0, -1.0) * np.linspace(60, 0, 3000), np.zeros(3000)]
    )
    tiny = _ring(0.3, 12)  # far below any tolerance
    flat_ring = np.vstack([UTM + [[0, 0, 0], [5, 0, 0], [10, 0, 0], [5, 0, 0]], UTM[None, :]])  # no area
    ring_no_z = _ring(80.0, 300)
    ring_no_z[:, 2] = math.nan
    ring_mixed = _ring(120.0, 400, tilt=0.3)
    ring_mixed[50:90, 2] = math.nan
    lines = [
        spur, same, doubled, no_z, mixed, straight, comb, UTM[None, :].copy(), UTM + [[0, 0, 0], [3, 4, 0.0]],
        _walk(rng, 5000, wobble=0.3), _walk(rng, 3 * MAX_RUN + 17, wobble=0.01), tiny, flat_ring, ring_no_z,
        ring_mixed, _ring(900.0, 4000, tilt=0.5), _ring(30.0, 3),
    ]  # fmt: skip
    closed = [0] * 11 + [1] * 6
    return lines, closed


# --- Douglas–Peucker -----------------------------------------------------------------------------------


@pytest.mark.parametrize("tol", [0.0, 0.05, 4.0, 32.0, 1e6])
def test_matches_the_reference_and_stays_within_the_tolerance(tol: float) -> None:
    lines, flags = _hard_lines(np.random.default_rng(3))
    xyz, offsets = _pack(lines)
    closed = np.array(flags, dtype=np.uint8)
    keep = keep_mask(xyz, offsets, tol, closed)
    np.testing.assert_array_equal(keep, _ref_keep(xyz, offsets, tol, closed, MAX_RUN))
    assert _worst(xyz, offsets, keep) <= tol  # the tolerance itself, no slack: the same float64 arithmetic
    out, out_off = simplify(xyz, offsets, tol, closed)
    np.testing.assert_array_equal(out_off, compact(offsets, keep))
    for i, (src, flag) in enumerate(zip(lines, flags, strict=True)):
        got = out[out_off[i] : out_off[i + 1]]
        assert got[0].tobytes() == src[0].tobytes() and got[-1].tobytes() == src[-1].tobytes()  # the ends
        if flag:  # a closed line stays one: the repeat of the first vertex and three corners
            assert len(got) >= 4 and got[-1].tobytes() == got[0].tobytes()
            LineSet("ring", got, [0, len(got)], [1])  # the model accepts it as closed
    if tol <= 32.0:
        assert keep[offsets[0] : offsets[1]].all()  # the spur: 99 m from its chord, 0 m from the chord's line
    if tol >= 0.05:
        assert int(keep[offsets[5] : offsets[6]].sum()) == 2  # a straight line: the ends only
    if tol == 1e6:  # nothing but what must stay
        counts = np.diff(compact(offsets, keep)).tolist()
        assert counts[:4] == [2, 2, 2, 2] and counts[5] == 2 and counts[7:9] == [1, 2]
        assert counts[4] == 2 + 4 + 3  # the ends and both sides of the two gaps in Z (the second: one vertex)
        assert counts[6] == 2 + 2 and counts[9] == 2 + 4 and counts[10] == 2 + 3  # every MAX_RUN-th vertex
        assert counts[11:14] == [4, 4, 4] and counts[16] == 4  # rings: three corners and the repeat
        assert counts[14] == 4 + 4 and counts[15] == 4 + 3  # a ring with a gap in Z; a ring with run marks


def test_empty_heights_are_kept_apart() -> None:
    rng = np.random.default_rng(5)
    line = _walk(rng, 400)
    line[150:220, 2] = math.nan
    keep = keep_mask(line, [0, 400], 32.0)
    assert keep[[0, 149, 150, 219, 220, 399]].all()  # both sides of every change
    out = line[keep]
    nan = np.isnan(out[:, 2])
    assert nan.any() and not nan.all() and np.isfinite(out[~nan]).all()  # NaN stays NaN, never 0
    flat = line.copy()
    flat[:, 2] = 0.0  # a run without Z is simplified in plan: as the same run at one level
    assert keep[150:220].tolist() == keep_mask(flat[150:220], [0, 70], 32.0).tolist()


def test_circle_with_a_known_answer() -> None:
    """A ring of 2¹⁶ vertices, R = 1000 m: halving stops at the first chord whose sagitta R·(1 − cos(π/k)) fits."""
    n, radius = 1 << 16, 1000.0
    ring = _ring(radius, n)
    for tol, arcs in ((4.0, 64), (4.9, 32), (0.3, 256), (0.31, 128), (19.3, 16)):
        keep = keep_mask(ring, [0, n + 1], tol, [1], max_run=1 << 20)
        assert np.flatnonzero(keep).tolist() == list(range(0, n + 1, n // arcs)), tol
        sagitta = radius * (1.0 - math.cos(math.pi / arcs))
        assert sagitta <= tol < radius * (1.0 - math.cos(2 * math.pi / arcs))  # the tolerance is used
        assert _worst(ring, np.array([0, n + 1]), keep) == pytest.approx(sagitta, abs=1e-9)
    keep = keep_mask(ring, [0, n + 1], 4.0, [1])  # with the run marks: 64 arcs and the marks, which fall on them
    assert int(keep.sum()) == 65 and keep[::MAX_RUN].all()


def test_worst_case_is_bounded() -> None:
    """A comb whose teeth get shorter: plain Douglas–Peucker is quadratic here (2·10¹⁰ distances)."""
    n = 200_000
    i = np.arange(n)
    comb = UTM + np.column_stack([i * 10.0, np.where(i % 2, 1.0, -1.0) * 1000.0 * (1 - i / n), np.zeros(n)])
    keep_mask(comb[:100], [0, 100], 4.0)  # compiled
    t0 = time.perf_counter()
    keep = keep_mask(comb, [0, n], 4.0)
    assert time.perf_counter() - t0 < 5.0
    assert _worst(comb, np.array([0, n]), keep) <= 4.0 and keep[: n // 2].all()  # teeth of 500 m and more stay


def test_bad_input_is_refused() -> None:
    xyz = np.zeros((4, 3))
    for bad in (-1.0, math.nan, math.inf):
        with pytest.raises(ValueError, match="tolerance"):
            keep_mask(xyz, [0, 4], bad)
    with pytest.raises(ValueError, match="offsets"):
        keep_mask(xyz, [0, 5], 1.0)
    with pytest.raises(ValueError, match="offsets"):
        keep_mask(xyz, [0, 3, 2, 4], 1.0)
    with pytest.raises(ValueError, match="closed"):
        keep_mask(xyz, [0, 4], 1.0, [1, 0])
    with pytest.raises(ValueError, match="finite"):
        keep_mask(np.array([[0.0, math.nan, 0.0]]), [0, 1], 1.0)
    with pytest.raises(ValueError, match="finite"):
        keep_mask(np.array([[0.0, 0.0, math.inf]]), [0, 1], 1.0)
    assert keep_mask(np.zeros((0, 3)), [0], 1.0).shape == (0,)
    assert keep_mask(xyz, [0, 0, 4, 4], 1.0).tolist() == [True, False, False, True]  # empty lines are passed over


# --- parts ---------------------------------------------------------------------------------------------


def _layer(rng: np.random.Generator) -> LineSet:
    """Hard lines, a 40 km line, a segment of 30 km, 300 contours over 25 km and a few texts."""
    lines, flags = _hard_lines(rng)
    far = UTM + np.column_stack([np.linspace(0, 40_000, 9000), 300 * np.sin(np.linspace(0, 60, 9000)), np.zeros(9000)])
    jump = UTM + np.array([[0, 0, 0], [30_000, 100, 0], [30_010, 110, 0]], dtype=float)
    lines += [far, jump]
    flags += [0, 0]
    for _ in range(300):
        v = _walk(rng, int(rng.integers(50, 1500)))
        lines.append(v + [rng.uniform(-12_000, 12_000), rng.uniform(-12_000, 12_000), 0.0])
        flags.append(0)
    marks = [UTM + [k * 10.0, 5.0, 1.0] for k in range(4)]
    lines += [m[None, :] for m in marks]
    flags += [0] * 4
    n = len(lines)
    text: list[str | None] = [None] * n
    height = [math.nan] * n
    text[-4:] = ["Профиль 12", "Ш-" + "я" * 400, "", "РЛ +400"]
    height[-4:] = [2.5, math.nan, 1.0, 3.0]
    return from_lines("Горизонтали", lines, closed=[bool(f) for f in flags], attrs={"text": text, "height": height})


def _pieces_of(lay: vl.Layout, k: int) -> np.ndarray:
    p = lay.parts[k]
    return np.arange(p.start, p.stop)


@pytest.mark.parametrize(("max_vertices", "max_span"), [(2000, 16_000.0), (256_000, 16_000.0), (700, 3_000.0)])
def test_parts_cover_every_segment_once(max_vertices: int, max_span: float) -> None:
    layer = _layer(np.random.default_rng(9))
    assert layer.offsets is not None and layer.closed is not None
    lay = vl.build(layer.xyz, layer.offsets, layer.closed, max_vertices=max_vertices, max_span=max_span)
    assert lay.line is not None and lay.start is not None and lay.stop is not None and lay.closed is not None
    counts = lay.stop - lay.start
    # pieces: in every line they follow one another and share one vertex; none is too long or too wide
    by_line = np.lexsort((lay.start, lay.line))
    ln, st, sp = lay.line[by_line], lay.start[by_line], lay.stop[by_line]
    first = np.r_[True, ln[1:] != ln[:-1]]
    last = np.r_[ln[1:] != ln[:-1], True]
    assert np.array_equal(np.unique(ln), np.arange(layer.line_count))
    assert np.array_equal(st[first], layer.offsets[:-1]) and np.array_equal(sp[last], layer.offsets[1:])
    assert np.array_equal(st[~first], sp[:-1][~first[1:]] - 1)
    assert counts.max() <= max_vertices and counts.min() >= 1
    for j in np.flatnonzero(counts > 2).tolist():
        v = layer.xyz[lay.start[j] : lay.stop[j]]
        assert np.nanmax(np.fmax.reduce(v) - np.fmin.reduce(v)) <= max_span  # a height that is empty is not 0
    whole = (lay.start == layer.offsets[lay.line]) & (lay.stop == layer.offsets[lay.line + 1])
    assert np.array_equal(lay.closed, (layer.closed[lay.line] == 1) & whole)  # a cut ring is open pieces
    # parts: runs of the pieces, each within the limits unless it is one piece; whole-metre origins
    assert [p.k for p in lay.parts] == list(range(len(lay.parts)))
    assert lay.parts[0].start == 0 and lay.parts[-1].stop == len(counts)
    for a, b in zip(lay.parts[:-1], lay.parts[1:], strict=True):
        assert a.stop == b.start
    for p in lay.parts:
        idx = _pieces_of(lay, p.k)
        pts = np.vstack([layer.xyz[lay.start[j] : lay.stop[j]] for j in idx.tolist()])
        assert lay.part_points[p.k] == len(pts)
        assert len(pts) <= max_vertices or len(idx) == 1
        lo, hi = np.fmin.reduce(pts), np.fmax.reduce(pts)  # NaN only where no vertex of the part has a height
        np.testing.assert_array_equal(np.array(p.bbox), np.r_[lo, hi])
        one_segment = len(idx) == 1 and len(pts) == 2
        assert np.nanmax(hi - lo) <= max_span or one_segment
        centre = np.where(np.isnan(lo), 0.0, (lo + hi) / 2)
        assert all(float(v).is_integer() for v in p.origin) and np.abs(np.array(p.origin) - centre).max() <= 0.5
        if not one_segment and max_span <= 16_000.0:
            off = (pts - np.array(p.origin)).astype(np.float32)
            assert np.nanmax(np.abs(off)) <= 8000.5
            assert np.nanmax(np.abs(off.astype(np.float64) + np.array(p.origin) - pts)) <= F32
    if max_vertices == 256_000:
        assert len(lay.parts) > 1  # the layer is 25 km wide: cut by the span alone
    assert sum(lay.part_points) == int(counts.sum()) == layer.point_count + (len(counts) - layer.line_count)


def test_points_parts_and_one_point_per_cell() -> None:
    rng = np.random.default_rng(21)
    n = 60_000
    xyz = UTM + np.column_stack([rng.uniform(0, 30_000, n), rng.uniform(0, 9_000, n), rng.uniform(-200, 200, n)])
    xyz[: n // 2] = UTM + rng.uniform(0, 1.0, (n // 2, 3)) * [300.0, 200.0, 6.0]  # a dense survey of a bench
    xyz[::7, 2] = math.nan
    lay = vl.build(xyz, None, None, max_vertices=9000)
    assert lay.line is None and sorted(lay.order.tolist()) == list(range(n))
    assert sum(lay.part_points) == n and max(lay.part_points) <= 9000
    seen = np.zeros(n, dtype=int)
    total = {1: 0, 2: 0}
    for p in lay.parts:
        assert (np.array(p.bbox[3:5]) - np.array(p.bbox[:2])).max() <= 16_000.0
        head, full = vl.encode_part(lay, p.k, 0)
        assert set(full) == {"xyz", "line"} and head["points_only"] and not head["simplified"]
        feat = full["line"].astype(np.int64)
        seen[feat] += 1
        back = full["xyz"].reshape(-1, 3).astype(np.float64) + np.array(p.origin)
        assert np.array_equal(np.isnan(back[:, 2]), np.isnan(xyz[feat, 2]))  # an empty Z stays NaN
        assert np.nanmax(np.abs(back - xyz[feat])) <= F32
        for lod, cell in ((1, 4.0), (2, 32.0)):
            head, a = vl.encode_part(lay, p.k, lod)
            kept = a["line"].astype(np.int64)
            total[lod] += len(kept)
            assert head["points"] == len(kept) and head["simplified"] is (len(kept) < len(feat))
            assert np.array_equal(kept, feat[np.isin(feat, kept)])  # the order of the part is kept

            def cells(idx: np.ndarray, cell: float = cell) -> list[tuple[float, ...]]:
                c = np.floor(xyz[idx] / cell)
                return [tuple(r) for r in np.where(np.isnan(c), -1.0, c).tolist()]

            every = cells(feat)
            assert set(cells(kept)) == set(every) and len(kept) == len(set(every))  # one point in every cell
            first = {c: i for i, c in reversed(list(zip(feat.tolist(), every, strict=True)))}
            assert set(kept.tolist()) == set(first.values())  # the first of the cell in the order of the part
    assert seen.tolist() == [1] * n
    assert total[2] < total[1] < 0.75 * n  # the dense half thins out; the sparse one stays


# --- API -----------------------------------------------------------------------------------------------


@pytest.fixture()
def stored(pclient):  # type: ignore[no-untyped-def]
    """A project with the hard layer and a layer of points: (client, project, layer, layer id, points id)."""
    r = pclient.post(f"{API}/projects", json={"name": "Линии"})
    assert r.status_code == 200, r.text
    project = projects_of(pclient.core).require_current()
    layer = _layer(np.random.default_rng(9))
    rng = np.random.default_rng(2)
    pts = from_points(
        "Пикеты",
        UTM + rng.uniform(0, 400, (3000, 3)),
        attrs={"text": [f"ПК{i}" if i % 3 else None for i in range(3000)], "rotation": np.arange(3000.0)},
    )
    store = BlobStore(project.folder)
    with dialect.write_tx(project.engine) as conn:
        file_id = ls_store.create_file(conn, store, name="рельеф.dxf", meta={"format": "dxf"})
        layer_id = ls_store.put_layer(conn, store, layer, file_id=file_id)
        points_id = ls_store.put_layer(conn, store, pts, file_id=file_id)
    return pclient, project, layer, layer_id, points_id


def _get(c, url: str, **params):  # type: ignore[no-untyped-def]
    r = c.get(f"{API}/viz/lines/{url}", params=params)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["cache-control"] == "no-cache"
    return r, *binary.decode(r.content)


def _polyline_dist(p: np.ndarray, line: np.ndarray) -> np.ndarray:
    """Distance of every point of p from the polyline (its nearest segment)."""
    if len(line) == 1:
        return np.asarray(np.linalg.norm(p - line[0], axis=1))
    return np.min([_seg_dist(p, a, b) for a, b in zip(line[:-1], line[1:], strict=True)], axis=0)


def test_layer_parts_and_levels_through_the_api(stored) -> None:  # type: ignore[no-untyped-def]
    c, _project, layer, layer_id, _points = stored
    assert layer.offsets is not None and layer.closed is not None and layer.attrs is not None
    r, head, arrays = _get(c, str(layer_id))
    assert head["kind"] == "line_layer" and head["layer"]["name"] == "Горизонтали" and arrays == {}
    assert head["lines"] == layer.line_count and head["points"] == layer.point_count and not head["points_only"]
    assert head["lods"] == [{"lod": 0, "tol": 0.0}, {"lod": 1, "tol": 4.0}, {"lod": 2, "tol": 32.0}]
    assert head["labels"] is True and head["bbox"][:2] == layer.extent()[:2] and len(head["parts"]) > 1
    pieces: dict[int, list[tuple[int, int]]] = {}
    total = {0: 0, 1: 0, 2: 0}
    labels: list[str] = []
    for part in head["parts"]:
        origin = np.array(part["origin"])
        assert all(float(v).is_integer() for v in part["origin"])
        full = None
        for lod, tol in enumerate((0.0, 4.0, 32.0)):
            rp, h, a = _get(c, f"{layer_id}/parts/{part['k']}", lod=lod)
            assert rp.headers["etag"] == part["etags"][lod]
            assert h["kind"] == "lines" and (h["k"], h["lod"], h["tol"]) == (part["k"], lod, tol)
            assert h["origin"] == part["origin"] and h["bbox"] == part["bbox"] and h["n"] == part["n"]
            assert {k: v.dtype.str for k, v in a.items()} == {
                "offsets": "<u4",
                "xyz": "<f4",
                "line": "<u4",
                "closed": "|u1",
            }
            off = a["offsets"].astype(np.int64)
            xyz = a["xyz"].reshape(-1, 3).astype(np.float64) + origin
            assert len(off) == h["n"] + 1 and off[0] == 0 and off[-1] == len(xyz) == h["points"]
            assert np.all(np.diff(off) >= 1) and h["source_points"] == part["points"]
            total[lod] += h["points"]
            if lod == 0:
                full = (off, xyz, a["line"].astype(np.int64))
                assert h["simplified"] is False and h["points"] == part["points"]
                for j, ln in enumerate(a["line"].tolist()):
                    pieces.setdefault(ln, []).append((part["k"], j))
                lab = h["labels"]
                labels += lab["text"]
                for at, t, hh in zip(lab["at"], lab["text"], lab["height"], strict=True):
                    src = layer.attrs.column("text")[a["line"][at]].as_py()
                    assert src.startswith(t) and len(t) <= vl.MAX_LABEL_CHARS
                    want = layer.attrs.column("height")[a["line"][at]].as_py()
                    assert hh == (None if math.isnan(want) else want)
                continue
            assert full is not None and np.array_equal(a["line"], full[2])  # the same polylines, fewer vertices
            for j in range(h["n"]):
                src = full[1][full[0][j] : full[0][j + 1]]
                got = xyz[off[j] : off[j + 1]]
                assert np.array_equal(got[0], src[0], equal_nan=True) and np.array_equal(
                    got[-1], src[-1], equal_nan=True
                )
                if a["closed"][j]:
                    assert len(got) >= 4 and np.array_equal(got[0], got[-1], equal_nan=True)
                if len(src) <= 3000 and not np.isnan(src).any():
                    assert _polyline_dist(src, got).max() <= tol + 2 * F32 * math.sqrt(3)
    assert total[0] > layer.point_count and total[2] < total[1] < 0.5 * total[0]  # the levels get lighter
    assert sorted(labels) == sorted(["Профиль 12", "Ш-" + "я" * (vl.MAX_LABEL_CHARS - 2), "РЛ +400"])
    # level 0 gives the source back: every line from its pieces, within the float32 step of its part
    assert sorted(pieces) == list(range(layer.line_count))
    cache: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for ln in (0, 3, 4, 13, 14, 17, 18, 40, layer.line_count - 1):
        got = []
        for k, j in pieces[ln]:
            if k not in cache:
                _r, h, a = _get(c, f"{layer_id}/parts/{k}")
                cache[k] = (
                    a["offsets"],
                    a["xyz"].reshape(-1, 3).astype(np.float64) + np.array(h["origin"]),
                    a["closed"],
                )
            off, xyz, closed = cache[k]
            got.append(xyz[off[j] : off[j + 1]])
            assert bool(closed[j]) == (bool(layer.closed[ln]) and len(pieces[ln]) == 1)
        src = layer.line(ln)
        joined = np.vstack([got[0], *[g[1:] for g in got[1:]]])  # pieces share one vertex
        assert joined.shape == src.shape and np.array_equal(np.isnan(joined), np.isnan(src))  # NaN stays NaN
        limit = F32 if len(pieces[ln]) == 1 and ln != 18 else 2e-3  # the 30 km segment: a part wider than 16 km
        assert np.nanmax(np.abs(joined - src)) <= limit
    assert len(pieces[17]) >= 3 and len(pieces[18]) == 2  # 40 km in pieces of 16 km; the long segment on its own


def test_points_layer_through_the_api(stored) -> None:  # type: ignore[no-untyped-def]
    c, _project, _layer_, _layer_id, points_id = stored
    _r, head, _a = _get(c, str(points_id))
    assert head["points_only"] and head["lines"] == 0 and head["points"] == 3000 and len(head["parts"]) == 1
    _r, h, a = _get(c, f"{points_id}/parts/0")
    assert set(a) == {"xyz", "line"} and h["points"] == h["n"] == 3000 and h["points_only"]
    lab = h["labels"]
    assert lab["total"] == 2000 and len(lab["at"]) == 2000 and "height" not in lab
    feat = a["line"][lab["at"]].tolist()
    assert lab["text"] == [f"ПК{i}" for i in feat] and lab["rotation"] == [float(i) for i in feat]
    _r, h2, a2 = _get(c, f"{points_id}/parts/0", lod=2)
    assert h2["points"] == len(a2["line"]) < 3000 and h2["simplified"]
    kept = a2["line"][h2["labels"]["at"]].tolist()
    assert h2["labels"]["text"] == [f"ПК{i}" for i in kept] and h2["labels"]["total"] == len(kept)


def test_etag_follows_the_version_and_the_level(stored, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, project, _layer_, layer_id, points_id = stored
    r, head, _a = _get(c, str(layer_id))
    tag = r.headers["etag"]
    tags = head["parts"][0]["etags"]
    assert len({tag, *tags}) == 4 and tags != head["parts"][1]["etags"]
    other = _get(c, str(points_id))[1]["parts"][0]["etags"]
    assert not set(other) & set(tags)

    def boom(*_a: object, **_k: object) -> bytes:
        raise AssertionError("a 304 must not build anything")

    with monkeypatch.context() as m:
        m.setattr(vl, "manifest", boom)
        m.setattr(vl, "part", boom)
        m.setattr(vl, "layout", boom)
        assert c.get(f"{API}/viz/lines/{layer_id}", headers={"If-None-Match": tag}).status_code == 304
        for lod in (0, 1, 2):
            r304 = c.get(
                f"{API}/viz/lines/{layer_id}/parts/0", params={"lod": lod}, headers={"If-None-Match": tags[lod]}
            )
            assert r304.status_code == 304 and r304.headers["etag"] == tags[lod] and not r304.content
    wrong = c.get(f"{API}/viz/lines/{layer_id}/parts/0", params={"lod": 1}, headers={"If-None-Match": tags[2]})
    assert wrong.status_code == 200 and wrong.headers["etag"] == tags[1]
    with dialect.write_tx(project.engine) as conn:  # what PATCH /lines/layers/{id} does
        conn.execute(sa.update(objects).where(objects.c.id == layer_id).values(version=objects.c.version + 1))
    r2, head2, _a = _get(c, str(layer_id))
    assert r2.headers["etag"] != tag and head2["version"] == head["version"] + 1
    assert not set(head2["parts"][0]["etags"]) & set(tags)
    assert c.get(f"{API}/viz/lines/{layer_id}", headers={"If-None-Match": tag}).status_code == 200


def test_errors_are_russian_with_a_hint(stored) -> None:  # type: ignore[no-untyped-def]
    c, project, _layer_, layer_id, _points = stored
    for url, params, status, code in (
        ("999999", {}, 404, "line_layer_not_found"),
        ("999999/parts/0", {}, 404, "line_layer_not_found"),
        (f"{layer_id}/parts/9999", {}, 404, "part_not_found"),
        (f"{layer_id}/parts/-1", {}, 404, "part_not_found"),
        (f"{layer_id}/parts/0", {"lod": 3}, 422, "bad_lod"),
        (f"{layer_id}/parts/0", {"lod": -1}, 422, "bad_lod"),
    ):
        r = c.get(f"{API}/viz/lines/{url}", params=params)
        body = r.json()
        assert (r.status_code, body["code"]) == (status, code), r.text
        assert body["message"] and body["hint"] and any("а" <= ch <= "я" for ch in body["message"])
    with dialect.write_tx(project.engine) as conn:  # an import still in progress is not shown
        conn.execute(sa.update(objects).where(objects.c.id == layer_id).values(state="building"))
    assert c.get(f"{API}/viz/lines/{layer_id}").json()["code"] == "line_layer_not_found"
