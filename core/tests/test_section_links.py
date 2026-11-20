"""Contours of a series and their automatic links (ТЗ 8.1.4; ADR 0016 §5, §7, §8; 10.8): `sections/contours.py`,
`sections/links.py`, `sections/link_kernels.py`, `GET …/contours?k=`, `POST …/links`.

Reference answers: the exact orientation sign — against `fractions.Fraction` on near-degenerate points (the float64
determinant has the wrong sign there); overlap — by construction (overlap, touching at an edge or a vertex,
nesting both ways, a cross with no vertex inside the other, a crescent around a square — boxes meet, no overlap);
links — overlap wins over the centres, a shift of the centres ≤ Δ links, > Δ does not, 1 → 2 is a branching, N : M
a conflict. Membership: a contour belongs to the section whose window holds all its vertices; one with a vertex
2 mm off the plane is still on it, with a warning. Times (best of 3): 100 contours × 500 vertices — links ≤ 0,2 с;
the shadow of a layer of 2 000 contours — ≤ 50 ms warm.
"""

from __future__ import annotations

import math
import time
from fractions import Fraction
from typing import Any

import numpy as np
import pytest

from bf_core.db import dialect
from bf_core.formats.lineset import from_lines
from bf_core.lines import store as ls_store
from bf_core.projects.service import projects_of
from bf_core.sections import contours as sc_mod
from bf_core.sections.geometry import series_geometry
from bf_core.sections.links import auto_links, centres, conflicts
from bf_core.storage.blobs import BlobStore
from bf_core.viz import binary
from tests.test_import_job import API

URL = f"{API}/section-series"
P1 = (512_000.0, 6_789_000.0, 0.0)  # UTM
SERIES = {"type": "vertical", "p1": list(P1), "az": 90, "s0": 0, "s1": 100, "step": 25, "towards": 12.5, "away": 12.5}


def kernels() -> Any:
    from bf_core.sections import link_kernels

    return link_kernels


# --- exact signs -------------------------------------------------------------------------------------------------


def exact_sign(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> int:
    ax, ay, bx, by, cx, cy = (Fraction(v) for v in (*a, *b, *c))
    d = (ax - cx) * (by - cy) - (ay - cy) * (bx - cx)
    return (d > 0) - (d < 0)


def test_orientation_is_exact_near_a_line() -> None:
    """Shewchuk's grid near the line y = x: the float64 determinant gets many signs wrong, `orient` none."""
    k = kernels()
    wrong = 0
    eps = 2.0**-53
    for i in range(64):
        for j in range(64):
            a = (0.5 + i * eps, 0.5 + j * eps)
            b, c = (12.0, 12.0), (24.0, 24.0)
            want = exact_sign(a, b, c)
            naive = (a[0] - c[0]) * (b[1] - c[1]) - (a[1] - c[1]) * (b[0] - c[0])
            wrong += (naive > 0) - (naive < 0) != want
            assert k.orient(*a, *b, *c) == want
            assert k.orient_exact(*a, *b, *c) == want
    assert wrong > 0  # the grid does test the exact path
    rng = np.random.default_rng(5)
    for _ in range(2000):  # at UTM magnitudes, with collinear and nearly collinear points
        a, b = rng.normal(0, 1e6, 2), rng.normal(0, 1e6, 2)
        t = rng.random()
        c = a + t * (b - a) + rng.choice([0.0, 1e-9, -1e-9]) * rng.normal(size=2)
        assert k.orient(*a, *b, *c) == exact_sign(tuple(a), tuple(b), tuple(c))


# --- overlap of two polygons ------------------------------------------------------------------------------------


def rect(u0: float, w0: float, u1: float, w1: float) -> np.ndarray:
    return np.array([[u0, w0], [u1, w0], [u1, w1], [u0, w1]], dtype=np.float64)


def overlaps(a: np.ndarray, b: np.ndarray) -> bool:
    uw = np.vstack([a, b])
    offsets = np.array([0, len(a), len(a) + len(b)], dtype=np.int64)
    _cu, _cw, _area, box = centres(uw, offsets)
    return bool(kernels().overlap(uw, 0, len(a), len(a), len(a) + len(b), box[0], box[1]))


CRESCENT = np.array([[0, 0], [30, 0], [30, 30], [0, 30], [0, 25], [25, 25], [25, 5], [0, 5]], dtype=np.float64)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (rect(0, 0, 20, 20), rect(10, 10, 30, 30), True),  # overlap
        (rect(0, 0, 20, 20), rect(20, 0, 40, 20), True),  # touching along an edge
        (rect(0, 0, 20, 20), rect(20, 20, 40, 40), True),  # touching at a corner
        (rect(0, 0, 20, 20), rect(5, 5, 10, 10), True),  # nested
        (rect(5, 5, 10, 10), rect(0, 0, 20, 20), True),  # nested the other way
        (rect(0, 8, 30, 12), rect(13, -10, 17, 30), True),  # a cross: no vertex inside the other
        (CRESCENT, rect(5, 10, 20, 20), False),  # boxes meet, the square sits in the gap
        (rect(0, 0, 20, 20), rect(20.000001, 0, 40, 20), False),  # a gap of a micrometre
        (CRESCENT[::-1].copy(), rect(5, 10, 20, 20) + 1e-9, False),  # either winding
    ],
)
def test_overlap_cases(a: np.ndarray, b: np.ndarray, expected: bool) -> None:
    assert overlaps(a, b) is expected
    assert overlaps(b, a) is expected
    shift = np.array([512_345.678, 6_789_012.345])  # at UTM magnitudes too
    assert overlaps(a + shift, b + shift) is expected


# --- automatic links --------------------------------------------------------------------------------------------


def series_of(polys: list[np.ndarray], ks: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    uw = np.vstack(polys)
    offsets = np.concatenate([[0], np.cumsum([len(p) for p in polys])]).astype(np.int64)
    return uw, offsets, np.asarray(ks, dtype=np.int64)


def test_links_by_overlap_and_by_centres() -> None:
    step = 25.0
    polys = [
        rect(0, 0, 20, 20),
        rect(0, 0, 20, 20) + [30, 0],  # k = 0: a body at u 0…20 and one at 30…50
        rect(10, 0, 40, 20),  # k = 1: overlaps both — 2 → 1
        rect(34.9, 0, 64.9, 20),  # k = 2: overlaps the one of k = 1 (u 34,9…40)
        rect(200, 0, 220, 20),  # k = 3: far away — no link
        rect(200, 30, 220, 50),
        rect(210, 30, 230, 50),  # k = 4: no overlap with k = 3 (w 30…50 vs 0…20)
    ]
    uw, offsets, section = series_of(polys, [0, 0, 1, 2, 3, 4, 4])
    pairs, how = auto_links(uw, offsets, section, step)
    got = {(int(a), int(b)): int(h) for (a, b), h in zip(pairs, how, strict=True)}
    # 3 — 4: no overlap, centres 30 apart (> Δ); 3 — 5: no overlap, centres √(10² + 30²) > Δ
    assert got == {(0, 2): 1, (1, 2): 1, (2, 3): 1}
    # no overlap: centres within Δ and exactly Δ apart link, just over Δ do not
    for shift, linked in ((24.9, True), (25.0, True), (25.0 + 1e-6, False)):
        uw, offsets, section = series_of([rect(0, 0, 10, 10), rect(shift, 0, shift + 10, 10)], [0, 1])
        pairs, how = auto_links(uw, offsets, section, step)
        assert bool(len(pairs) == 1 and how[0] == 2) is linked, shift
        assert len(pairs) == int(linked)


def test_branching_and_conflicts() -> None:
    polys = [rect(0, 0, 40, 20), rect(0, 0, 15, 20), rect(25, 0, 40, 20)]  # 1 → 2
    uw, offsets, section = series_of(polys, [0, 1, 1])
    pairs, _how = auto_links(uw, offsets, section, 5.0)
    assert pairs.tolist() == [[0, 1], [0, 2]] and conflicts(pairs, section) == []
    polys = [rect(0, 0, 20, 20), rect(18, 0, 40, 20), rect(0, 0, 19, 20), rect(19, 0, 40, 20)]  # 2 : 2
    uw, offsets, section = series_of(polys, [0, 0, 1, 1])
    pairs, _how = auto_links(uw, offsets, section, 1.0)
    assert conflicts(pairs, section) == [{"sections": [0, 1], "contours": [0, 1, 2, 3]}]
    # contours on sections 0 and 2 are not neighbours; a contour in no window (−1) has no links
    uw, offsets, section = series_of([rect(0, 0, 20, 20)] * 3, [0, 2, -1])
    assert auto_links(uw, offsets, section, 25.0)[0].shape == (0, 2)


def test_centres_and_areas() -> None:
    tri = np.array([[0, 0], [6, 0], [0, 3]], dtype=np.float64) + [512_000.0, 300.0]
    uw, offsets, _ = series_of([tri, rect(0, 0, 2, 4)[::-1].copy()], [0, 0])
    cu, cw, area, box = centres(uw, offsets)
    assert cu[0] == pytest.approx(512_002.0, abs=1e-9) and cw[0] == pytest.approx(301.0, abs=1e-9)
    assert area.tolist() == [9.0, -8.0] and box[1].tolist() == [0.0, 0.0, 2.0, 4.0]


# --- contours of a series -----------------------------------------------------------------------------------------


def square(k: int, u0: float = 0.0, w0: float = 100.0, side: float = 20.0, t: float = 0.0) -> np.ndarray:
    """A square on the plane of section k of SERIES (y = 6 789 000 + 25k), u along x, w = Z."""
    uw = rect(u0, w0, u0 + side, w0 + side)
    y = P1[1] + 25.0 * k + t
    return np.column_stack([P1[0] + uw[:, 0], np.full(4, y), uw[:, 1]])


def test_membership_and_off_plane() -> None:
    geom = series_geometry(SERIES)
    lines = [square(0), square(1, t=0.002), square(2, t=12.4), square(2, t=13.0), square(4)]
    lines[1][2, 1] = P1[1] + 25.0 - 0.002  # one vertex 2 mm before the plane, the others after
    stored = [np.vstack([p, p[:1]]) for p in lines] + [square(3)[:3]]  # the last: an open line
    offsets = np.concatenate([[0], np.cumsum([len(p) for p in stored])])
    layer = sc_mod.from_stored(7, "Контуры", 3, np.vstack(stored), offsets, [1, 1, 1, 1, 1, 0], np.arange(6) * 2)
    assert layer.count == 5 and layer.open_lines == 1 and layer.lid.tolist() == [0, 2, 4, 6, 8]
    sc = sc_mod.series_contours(geom, [layer])
    assert sc.section.tolist() == [0, 1, 2, 3, 4]  # t = 13 is past w_от = 12.5: section 3
    assert sc.off[0] == 0.0 and sc.off[1] == pytest.approx(0.002, abs=1e-9)
    warnings = sc_mod.off_plane_warnings(sc, ["6789000N", "6789025N", "6789050N", "6789075N", "6789100N"])
    assert len(warnings) == 3 and warnings[0].startswith("Разрез 6789025N, линия 3 слоя «Контуры»: вершины до 0,002 м")
    assert sc_mod.picked(sc, [1, 3]).tolist() == [1, 3]


# --- the endpoints ------------------------------------------------------------------------------------------------


@pytest.fixture()
def series_router(monkeypatch: pytest.MonkeyPatch) -> None:
    import bf_core.api.app as app_module

    if "sections" not in app_module.ROUTER_MODULES:
        monkeypatch.setattr(app_module, "ROUTER_MODULES", (*app_module.ROUTER_MODULES, "sections"))


def put_layer(c: Any, rings: list[np.ndarray], name: str = "Контуры", closed: list[bool] | None = None) -> int:
    project = projects_of(c.core).require_current()
    with dialect.write_tx(project.engine) as conn:
        store = BlobStore(project.folder)
        file_id = ls_store.create_file(conn, store, name=f"{name}.str", meta={"format": "micromine_str"})
        return ls_store.put_layer(
            conn, store, from_lines(name, rings, closed=closed or [True] * len(rings)), file_id=file_id
        )


@pytest.fixture()
def sc_client(series_router, pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects", json={"name": "Контуры серии"}).status_code == 200
    sc_mod.clear_caches()
    return pclient


def make_series(c: Any, layer_id: int | None, **more: Any) -> dict[str, Any]:
    r = c.post(URL, json={"params": {**SERIES, "contour_layer": layer_id, **more}})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def test_shadow_contours_of_neighbours(sc_client) -> None:  # type: ignore[no-untyped-def]
    c = sc_client
    rings = [square(0), square(1), square(1, u0=40), square(2), square(3, t=0.004)]
    layer = put_layer(c, rings + [square(4)[:2]], closed=[True] * 5 + [False])
    s = make_series(c, layer)
    r = c.get(f"{URL}/{s['id']}/contours", params={"k": "0,2"})  # the shadow «оба» of section 1
    assert r.status_code == 200, r.text
    header, arrays = binary.decode(r.content)
    assert header["kind"] == "series_contours" and header["k"] == [0, 2] and header["lines"] == 2
    assert header["layers"] == [{"id": layer, "name": "Контуры", "rev": 1}] and header["version"] == s["version"]
    assert arrays["lid"].tolist() == [0, 3] and arrays["section"].tolist() == [0, 2]
    assert arrays["offsets"].tolist() == [0, 4, 8]
    assert np.array_equal(arrays["xyz"].reshape(-1, 3), np.vstack([rings[0], rings[3]]))  # bit for bit
    tag = r.headers["etag"]
    assert c.get(f"{URL}/{s['id']}/contours", params={"k": "0,2"}, headers={"If-None-Match": tag}).status_code == 304
    one = binary.decode(c.get(f"{URL}/{s['id']}/contours", params={"k": 1, "layers": str(layer)}).content)
    assert one[1]["lid"].tolist() == [1, 2]
    for k in ("5", "-1", "x", "0,1,2,3,4,0,1,2,3"):
        assert c.get(f"{URL}/{s['id']}/contours", params={"k": k}).json()["code"] in ("bad_section", "bad_query")
    none = make_series(c, None)
    e = c.get(f"{URL}/{none['id']}/contours", params={"k": 0}).json()
    assert e["code"] == "no_contour_layer" and e["hint"]
    assert c.get(f"{URL}/999999/contours", params={"k": 0}).json()["code"] == "series_not_found"


def test_links_table(sc_client) -> None:  # type: ignore[no-untyped-def]
    c = sc_client
    rings = [square(0), square(1), square(1, u0=40), square(2, u0=10), square(4, u0=500), square(3, t=0.004)]
    rings[3][1, 2] = np.nan  # no height at a vertex: not linked
    layer = put_layer(c, rings)
    s = make_series(c, layer)
    t = c.post(f"{URL}/{s['id']}/links", json={}).json()
    assert t["count"] == 5 and t["step"] == 25.0 and t["names"][0] == "6789000N"
    by = {(x["layer_id"], x["lid"]): x for x in t["contours"]}
    assert [by[(layer, i)]["k"] for i in range(6)] == [0, 1, 1, 2, 4, 3]
    assert by[(layer, 0)]["uw"] == [10.0, 110.0] and by[(layer, 0)]["area"] == 400.0
    assert by[(layer, 0)]["centre"] == [P1[0] + 10.0, P1[1], 110.0]
    i = {(x["lid"]): x["i"] for x in t["contours"]}
    # 0 — 1: overlap; 0 — 2: centres 40 apart; 5 (k = 3) — 4 (k = 4, u 500): far
    assert t["links"] == [[i[0], i[1], "overlap"]]
    assert t["skipped"] == {"open": 0, "short": 0, "outside": 0, "no_height": 1}
    assert any("6789075N" in w and "линия 6" in w for w in t["warnings"])
    assert any("без высоты" in w and "линия 4" in w for w in t["warnings"])
    assert [i[0], i[1]] in t["bodies"] and t["conflicts"] == []


def test_speed_links_100_by_500() -> None:
    """ADR 0016 «Замеры»: 100 contours × 500 vertices, links ≤ 0,2 с (full). 20 sections × 5 contours: a flat
    ellipse on even sections, a tall one on odd ones, crossing — no vertex of one is inside the other, so every
    linked pair goes through the sweep of the edges; the bodies are 80 m apart, their boxes do not meet."""
    rng = np.random.default_rng(11)
    a = np.linspace(0.0, 2.0 * np.pi, 500, endpoint=False)
    polys, ks = [], []
    for k in range(20):
        ru, rw, start = (30.0, 5.0, 0.0) if k % 2 == 0 else (5.0, 30.0, 0.5 * np.pi)
        for j in range(5):
            r = 1.0 + rng.normal(0.0, 0.003, 500)
            u = 80.0 * j + 0.5 * (k % 3) + ru * r * np.cos(a + start)
            w = 0.5 * (k % 2) + rw * r * np.sin(a + start)
            polys.append(np.column_stack([u, w]) + [512_000.0, 300.0])
            ks.append(k)
    uw, offsets, section = series_of(polys, ks)
    auto_links(uw[:1000], offsets[:3], section[:2], 5.0)  # the kernels: compiled or from the cache
    best = math.inf
    for _ in range(3):
        started = time.perf_counter()
        pairs, _how = auto_links(uw, offsets, section, 5.0)
        best = min(best, time.perf_counter() - started)
    print(f"\nlinks of 100 × 500: {best * 1e3:.1f} ms, {len(pairs)} links")
    assert len(pairs) == 95 and best <= 0.2


def test_speed_shadow_2000_contours(sc_client) -> None:  # type: ignore[no-untyped-def]
    """ADR 0016 «Замеры»: the shadow of k ± 1 from a layer of 2 000 contours ≤ 50 ms warm."""
    c = sc_client
    a = np.linspace(0.0, 2.0 * np.pi, 100, endpoint=False)
    rings = []
    for n in range(2000):
        k, j = n % 5, n // 5
        uw = np.column_stack([5.0 * j + 2.0 * np.cos(a), 100.0 + 2.0 * np.sin(a)])
        rings.append(np.column_stack([P1[0] + uw[:, 0], np.full(100, P1[1] + 25.0 * k), uw[:, 1]]))
    layer = put_layer(c, rings)
    s = make_series(c, layer)
    url = f"{URL}/{s['id']}/contours"
    assert c.get(url, params={"k": "1,3"}).status_code == 200  # warm: the layer and its sections cached
    best = math.inf
    for _ in range(3):
        started = time.perf_counter()
        r = c.get(url, params={"k": "1,3"})
        best = min(best, time.perf_counter() - started)
    header, _arrays = binary.decode(r.content)
    print(f"\nshadow of 2 000 contours (k ± 1: {header['lines']}): {best * 1e3:.1f} ms")
    assert header["lines"] == 800 and best <= 0.05
