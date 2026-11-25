"""«Проверка модели по скважинам» (подшаг 19.17; ТЗ 7.35 Д-32; ADR 0026 решение 28; e19-api §8.1, §8.2): the table of
the deviations at the data points of a built model and the map of the support of its surfaces.

**ТД32-1** η = 0: every residual ≤ 1e−8·max abs(f) (Т8.9-3), and the table is the stored points; **ТД32-2** a plane
z = 5 and one contact point at z = 8 with s > 0: that point is the first of the table and its distance to the surface
is that of a brute force over the triangles (and ≈ abs(z − z_пов)); **ТД32-3** the distance of the map of the support
equals a full brute force (1e−9 m). Also: the sort and the pages, the CSV, the highlight, a group of several fields
(an indicator shell), the share beyond the threshold and its record in `meta.stats.by_group` (the version stays), the
refusals {code, message, hint}, «устарела» as a warning, the route, the imports, the same bits for any number of
threads, the speed (100 thousand points: bench, `bench/` of the scratchpad)."""

from __future__ import annotations

import csv
import io
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.formats.lineset import from_points
from bf_core.lines import store as ls_store
from bf_core.model import check as ck
from bf_core.model import check_support as cs
from bf_core.model import store as st
from bf_core.model import store_base as sb
from bf_core.model.spec import parse_check, parse_model
from bf_core.wireframes import store as wfs
from tests.test_model_data_load import World
from tests.test_model_job import (  # noqa: F401 - `_roles`: the autouse fixture of the roles of the files
    _roles,
    groups_of,
    make,
    meta_of,
    run,
    shell,
)

pytestmark = pytest.mark.db
N = 147  # the data points of `plane_with_an_outlier`: 49 + 49 + 49


@pytest.fixture()
def world(db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    w = World(db_url, tmp_path)
    yield w
    w.engine.dispose()


@pytest.fixture(autouse=True)
def _forget() -> None:
    ck.forget()


def check_spec(**q: Any) -> Any:
    return parse_check(q)


def loaded(w: World, mid: int) -> ck.Loaded:
    return ck.load(w.project, mid)


def refused(fn: Any, status: int, code: str) -> ApiError:
    with pytest.raises(ApiError) as err:
        fn()
    assert err.value.status == status and err.value.code == code, (err.value.status, err.value.code)
    assert err.value.message and err.value.hint  # Russian text and the way out
    return err.value


def surfaces_of(w: World, mid: int) -> list[dict[str, Any]]:
    with dialect.read_tx(w.engine) as conn:
        return [o for o in st.outputs_of(conn, sb.stored(conn, mid).meta) if o["kind"] == "surface"]


def bodies_of(w: World, mid: int) -> list[dict[str, Any]]:
    with dialect.read_tx(w.engine) as conn:
        return [o for o in st.outputs_of(conn, sb.stored(conn, mid).meta) if o["kind"] == "body"]


def geometry(w: World, wid: int) -> tuple[np.ndarray, np.ndarray]:
    with dialect.read_tx(w.engine) as conn:
        return wfs.read_wireframe(conn, w.store, wid)


def stored_points(w: World, mid: int) -> dict[str, Any]:
    with dialect.read_tx(w.engine) as conn:
        sha = st.shas_of(conn, mid)[sb.ROLE_POINTS]
    return st.read_points(w.store, sha)[0]


def layered(w: World, h: float = 8.0) -> int:
    """The fixture scene, three domains, η = 0 (the contacts are exact): a built model."""
    mid = make(w, "Слои", groups_of("ALV", "SS", "SH"), h=h)["id"]
    run(w, mid)
    return mid


# --- ТД32-1 ---------------------------------------------------------------------------------------------------------


def test_td32_1_every_residual_of_an_exact_model_is_rounding_and_the_table_is_the_stored_points(world: World) -> None:
    w = world
    mid = layered(w)
    got = loaded(w, mid)
    out = ck.page(got, check_spec(size=5000))
    cols = stored_points(w, mid)
    assert out["total"] == len(out["rows"]) == len(cols["f"]) > 100  # a row per data point
    assert out["n"] == out["total"] and out["warnings"] == [] and out["model"]["stale"] is False
    rows = out["rows"]
    fmax = float(np.abs(cols["f"]).max())
    assert fmax > 1.0 and max(abs(r["residual"]) for r in rows) <= 1e-8 * fmax  # ТД32-1
    assert out["max_abs_residual"] <= 1e-8 * fmax
    by_i = {r["i"]: r for r in rows}
    assert sorted(by_i) == list(range(len(cols["f"])))  # every point once, the number is its row in the file
    for i in (0, 7, len(cols["f"]) // 2, len(cols["f"]) - 1):
        r = by_i[i]
        assert (r["x"], r["y"], r["z"], r["f_data"]) == (cols["x"][i], cols["y"][i], cols["z"][i], cols["f"][i])
        assert r["f_model"] == pytest.approx(r["f_data"], abs=1e-8 * fmax)
        assert r["residual"] == r["f_model"] - r["f_data"] and r["group"] == int(cols["group"][i])
    labels = {r["type"] for r in rows}
    assert labels <= set(ck.TYPE_LABELS) and {"контакт", "внутри", "снаружи"} <= labels
    for r in rows:
        assert r["source"] and r["source"].startswith("H") and r["from"] is not None and r["to"] is not None
        assert r["vertex"] is None and r["distance"] is not None and r["distance"] >= 0.0
        assert r["group_name"] in ("ALV", "SS", "SH") and r["wing"] is None and r["threshold"] is None
    d = np.array([r["distance"] for r in rows if r["type"] == "контакт"])
    far = np.array([r["distance"] for r in rows if r["type"] != "контакт"])
    # a contact lies on the body of its group (the median is a fraction of h = 8 m; the few that are not are what the
    # table is for: a contact the surface of the grid h did not keep); a point ±D_max/2 away does not
    assert np.median(d) < 1.0 and np.percentile(d, 75) < 8.0 and np.median(far) > 10.0 * np.median(d)


def test_the_distance_is_the_distance_to_the_surface_of_the_group(world: World) -> None:
    """Brute force over the triangles of the body of the group (the exact point–triangle distance)."""
    from bf_core.geo.deviation import point_triangle_d2

    w = world
    mid = layered(w)
    out = ck.page(loaded(w, mid), check_spec(group=3, size=5000))
    rows = out["rows"][:: max(len(out["rows"]) // 40, 1)]
    (body,) = [o for o in bodies_of(w, mid) if o["group"] == 3]
    v, t = geometry(w, body["wireframe_id"])
    for r in rows:
        d2 = min(point_triangle_d2(r["x"], r["y"], r["z"], *v[a], *v[b], *v[c]) for a, b, c in t.tolist())
        assert r["distance"] == pytest.approx(d2**0.5, abs=1e-9, rel=1e-12)


# --- ТД32-2 ---------------------------------------------------------------------------------------------------------


def plane_with_an_outlier(w: World, s: float = 10.0) -> int:
    """A plane z = 5 by hand data (48 contacts, 49 points above and 49 below at ±D_max/2) and a 49th contact at
    (50, 50, z = 8); the smoothing s > 0, so the surface does not pass through it."""
    xs = [5.0, 20.0, 35.0, 50.0, 65.0, 80.0, 95.0]
    grid = [(x, y) for x in xs for y in xs]
    contact = [(x, y, 5.0) for x, y in grid if (x, y) != (50.0, 50.0)] + [(50.0, 50.0, 8.0)]
    with dialect.write_tx(w.engine) as conn:
        file_id = ls_store.create_file(conn, w.store, name="Плоскость", meta={"format": "test"})
        ids = {
            role: ls_store.put_layer(conn, w.store, from_points(name, np.array(pts, dtype=np.float64)),
                                     file_id=file_id)
            for role, name, pts in (("contact", "Контакт", contact),
                                    ("inside", "Выше", [(x, y, 20.0) for x, y in grid]),
                                    ("outside", "Ниже", [(x, y, -10.0) for x, y in grid]))
        }  # fmt: skip
    manual = [{"layer_id": i, "role": r, "group": 1} for r, i in ids.items()]
    spec = w.spec([{"id": 1, "name": "Плоскость", "type": "domain", "codes": []}], manual)
    body = {**spec.canonical(), "boundary": {"box": [0.0, 0.0, -30.0, 100.0, 100.0, 40.0]}, "h": 8.0,
            "smoothing": {"mode": "manual", "s": s}, "name": "Плоскость z = 5"}  # fmt: skip
    with dialect.write_tx(w.engine) as conn:
        mid = st.create(conn, w.store, w.project, parse_model(body))["id"]
    run(w, mid)
    return mid


def test_td32_2_the_point_off_the_plane_is_the_first_of_the_table_at_its_distance_to_the_surface(world: World) -> None:
    from bf_core.geo.deviation import point_triangle_d2

    w = world
    mid = plane_with_an_outlier(w)
    out = ck.page(loaded(w, mid), check_spec(size=20))
    first = out["rows"][0]
    assert (first["x"], first["y"], first["z"], first["type"]) == (50.0, 50.0, 8.0, "ручная")
    assert first["f_data"] == 0.0 and first["source"] == "слой «Контакт»" and first["vertex"] is not None
    assert first["from"] is None and first["to"] is None  # a layer of strings has no depth
    assert abs(first["residual"]) > 3.0 * abs(out["rows"][1]["residual"])  # not a close call
    (body,) = [o for o in bodies_of(w, mid) if o["group"] == 1]
    v, t = geometry(w, body["wireframe_id"])
    d2 = min(point_triangle_d2(50.0, 50.0, 8.0, *v[a], *v[b], *v[c]) for a, b, c in t.tolist())
    assert first["distance"] == pytest.approx(d2**0.5, abs=1e-9, rel=1e-12)
    # the surface is the plane z ≈ 5 under the point: the distance is abs(z − z_пов) (the ray down from the point)
    hits = []
    (sheet,) = surfaces_of(w, mid)
    sv, tris = geometry(w, sheet["wireframe_id"])
    for a, b, c in tris.tolist():
        p0, e1, e2 = sv[a], sv[b] - sv[a], sv[c] - sv[a]
        m = np.array([[e1[0], e2[0], 0.0], [e1[1], e2[1], 0.0], [e1[2], e2[2], -1.0]])
        if abs(np.linalg.det(m)) < 1e-12:
            continue
        s1, s2, z = np.linalg.solve(m, np.array([50.0 - p0[0], 50.0 - p0[1], -p0[2]]))
        if s1 >= -1e-12 and s2 >= -1e-12 and s1 + s2 <= 1 + 1e-12:
            hits.append(z)
    assert len(hits) >= 1 and 4.0 < hits[0] < 8.0
    assert first["distance"] == pytest.approx(abs(8.0 - hits[0]), rel=0.03)  # a nearly flat surface
    assert first["over"] is True and out["over"] >= 1 and out["max_abs_residual"] == abs(first["residual"])


# --- the sort, the pages, the CSV, the highlight -------------------------------------------------------------------


def test_the_sort_by_residual_and_by_source_and_the_pages(world: World) -> None:
    w = world
    mid = plane_with_an_outlier(w)
    got = loaded(w, mid)
    everything = ck.page(got, check_spec(size=5000))["rows"]
    res = [abs(r["residual"]) for r in everything]
    assert res == sorted(res, reverse=True)
    ties = [(abs(a["residual"]), a["i"]) for a, b in zip(everything, everything[1:], strict=False)
            if abs(a["residual"]) == abs(b["residual"])]  # fmt: skip
    assert ties == sorted(ties, key=lambda x: (-x[0], x[1]))  # the equal ones by the number
    paged = []
    for n in (1, 2, 3, 4):
        part = ck.page(got, check_spec(page=n, size=40))
        assert part["page"] == n and part["size"] == 40 and part["total"] == len(everything) == N
        paged += part["rows"]
    assert [r["i"] for r in paged[:N]] == [r["i"] for r in everything]
    assert ck.page(got, check_spec(page=9, size=40))["rows"] == []
    by_source = ck.page(got, check_spec(sort="source", size=5000))["rows"]
    keys = [(r["source"], r["i"]) for r in by_source]
    assert keys == sorted(keys, key=lambda k: (k[0], k[1])) and {r["source"] for r in by_source} == {
        "слой «Контакт»", "слой «Выше»", "слой «Ниже»"}  # fmt: skip
    assert {r["i"] for r in by_source} == {r["i"] for r in everything}


def test_the_csv_is_the_table_of_an_export_and_the_highlight_is_a_threshold_of_metres(world: World) -> None:
    w = world
    mid = plane_with_an_outlier(w)
    got = loaded(w, mid)
    raw = ck.csv_bytes(got, check_spec(format="csv"), 1.0)
    assert raw.startswith("﻿".encode())  # UTF-8 with BOM, as the export of the tables
    table = list(csv.reader(io.StringIO(raw.decode("utf-8-sig")), delimiter=";"))
    assert table[0] == list(ck.COLUMNS) and len(table) == 1 + N
    page = ck.page(got, check_spec(size=5000), 1.0)["rows"]
    assert [int(r[0]) for r in table[1:]] == [r["i"] for r in page]  # the sort of the page
    first = table[1]
    assert first[3] == "ручная" and first[4] == "слой «Контакт»" and first[8:11] == ["50", "50", "8"]
    assert float(first[13].replace(",", ".")) == pytest.approx(page[0]["residual"], rel=1e-12)
    assert first[15] == "да" and first[5] == "" and first[6] == ""  # over; a layer has no depth: an empty cell
    assert "," in first[13] and "." not in first[13]  # the decimal comma
    over = [r for r in page if r["over"]]
    assert len(over) == sum(1 for r in table[1:] if r[15] == "да") == ck.page(got, check_spec(), 1.0)["over"] >= 1
    assert ck.page(got, check_spec(), 1e9)["over"] == 0 and ck.page(got, check_spec(), 0.0)["over"] >= N - 10
    default = ck.page(got, check_spec())
    assert default["highlight"] == pytest.approx(0.1 * 8.0)  # 0,1·h
    only = ck.csv_bytes(got, check_spec(group=1, format="csv"), None)
    assert only.count(b"\n") == 1 + N


def test_a_group_filter_and_the_groups_summary(world: World) -> None:
    w = world
    mid = layered(w)
    got = loaded(w, mid)
    everything = ck.page(got, check_spec(size=1))
    assert [g["group"] for g in everything["groups"]] == [2, 3, 4]
    assert sum(g["points"] for g in everything["groups"]) == everything["total"]
    assert all(g["max_abs_residual"] is not None and g["wireframes"] for g in everything["groups"])
    one = ck.page(got, check_spec(group=3, size=5000))
    assert one["group"] == 3 and one["total"] == next(g["points"] for g in everything["groups"] if g["group"] == 3)
    assert {r["group"] for r in one["rows"]} == {3}
    refused(lambda: ck.page(got, check_spec(group=99)), 404, "group_not_found")
    refused(lambda: check_spec(sort="по-всякому"), 422, "bad_param")
    refused(lambda: check_spec(size=0), 422, "bad_param")


# --- an indicator shell: a field of its own for every threshold ----------------------------------------------------


def test_an_indicator_shell_has_a_row_per_point_and_threshold_and_a_numeric_one_a_row_per_point(world: World) -> None:
    w = world
    ind = shell(5, w, mode="indicator", thresholds=[0.3, 1.0])
    num = shell(6, w, thresholds=[0.3])
    mid = make(w, "Оболочки", [*groups_of("SS"), ind, num], h=8.0)["id"]
    run(w, mid)
    got = loaded(w, mid)
    out = ck.page(got, check_spec(size=1))
    mine = {g: ck.page(got, check_spec(group=g, size=5000))["rows"] for g in (3, 5, 6)}
    assert all(r["threshold"] is None for r in mine[6]) and mine[6]  # numeric: one RBF of the grades
    thresholds = sorted({r["threshold"] for r in mine[5]})
    assert thresholds and set(thresholds) <= {0.3, 1.0}
    per = {t: [r for r in mine[5] if r["threshold"] == t] for t in thresholds}
    assert len({len(v) for v in per.values()}) == 1  # the same points for every field
    for rows in per.values():
        assert {r["f_data"] for r in rows} <= {-0.5, 0.5}  # the indicator of the threshold, not the grade
    assert len(thresholds) == 2 and out["total"] == len(mine[3]) + len(mine[5]) + len(mine[6])
    assert out["n"] == len(mine[3]) + len(per[0.3]) + len(mine[6]) < out["total"]  # `n` counts the points once
    assert all(r["type"] == "значение" for r in mine[5] + mine[6])
    assert max(abs(r["residual"]) for r in mine[3]) < 1e-6  # the domain is exact; the shells are smoothed (s = 0,05)
    assert {g["group"]: g["threshold"] for g in out["groups"] if g["group"] == 5} == {5: thresholds[-1]}


# --- the field summed again, the field from the systems of the patches ---------------------------


def test_the_field_taken_from_the_systems_of_the_patches_is_the_summed_field(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """f_i − Σ W·η·λ / Σ W is the field at a data point (η > 0 here: the residuals are not zero)."""
    w = world
    mid = plane_with_an_outlier(w)
    summed = loaded(w, mid).table
    assert {g["method"] for g in summed.groups} == {"field"} and np.abs(summed.residual()).max() > 1.0
    ck.forget()
    monkeypatch.setattr(ck, "TERMS_MAX", 0)
    quick = loaded(w, mid).table
    assert {g["method"] for g in quick.groups} == {"identity"}
    assert np.abs(quick.f_model - summed.f_model).max() <= 1e-9 * float(np.abs(summed.f_data).max())
    assert np.array_equal(quick.idx, summed.idx) and np.array_equal(quick.distance, summed.distance)


# --- a structural trend: A_j of every patch ---------------------------------------------------


def test_a_model_of_a_structural_trend_is_checked_with_the_matrices_of_its_patches(world: World) -> None:
    from tests.test_model_job_structural import measurements, structural
    from tests.test_model_update import small_patches

    w = world
    trend = structural(w, measurements(w, 35.0, 120.0))
    mid = make(w, "Структурная", groups_of("SS", "SH"), trend_id=trend, **small_patches())["id"]
    run(w, mid)
    out = ck.page(loaded(w, mid), check_spec(size=5000))
    cols = stored_points(w, mid)
    assert out["total"] == len(cols["f"]) and out["warnings"] == []
    assert out["max_abs_residual"] <= 1e-8 * float(np.abs(cols["f"]).max())  # the field of the build, A_j and all
    assert {g["method"] for g in out["groups"]} == {"field"}


# --- the same bits ------------------------------------------------------------------------------------------------


def test_the_table_does_not_depend_on_the_number_of_threads(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    w = world
    mid = layered(w)
    results = []
    for threads in (1, 2, 4, 8):
        ck.forget()
        monkeypatch.setattr(ck, "threads_of", lambda n=threads: n)
        got = loaded(w, mid).table
        results.append((got.f_model.tobytes(), got.distance.tobytes(), got.idx.tobytes()))
    assert all(r == results[0] for r in results[1:])


# --- ТД32-3: the map of the support ---------------------------------------------------------------------------------


def support(w: World, mid: int, **body: Any) -> cs.Support:
    return cs.run(w.project, mid, cs.parse(body))


def test_td32_3_the_distance_of_the_support_is_the_full_brute_force_and_the_share_is_of_the_area(world: World) -> None:
    from scipy.spatial.distance import cdist

    w = world
    mid = layered(w)
    cols = stored_points(w, mid)
    threshold = 25.0
    for surface in surfaces_of(w, mid):
        got = support(w, mid, wireframe_id=surface["wireframe_id"], threshold=threshold)
        v, t = geometry(w, surface["wireframe_id"])
        mine = np.stack([cols["x"], cols["y"], cols["z"]], axis=1)[cols["group"] == surface["group"]]
        brute = cdist(v, mine).min(axis=1)
        assert got.values is not None and got.values.shape == (len(v),)
        assert np.abs(got.values - brute).max() <= 1e-9  # ТД32-3
        assert got.out["values"] == got.values.tolist()
        # the area that belongs to the vertices beyond the threshold: a third of every triangle to each of its vertices
        per_vertex = np.zeros(len(v))
        total = 0.0
        for a, b, c in t.tolist():
            area = 0.5 * float(np.linalg.norm(np.cross(v[b] - v[a], v[c] - v[a])))
            total += area
            for k in (a, b, c):
                per_vertex[k] += area / 3.0
        beyond = float(per_vertex[brute > threshold].sum())
        (entry,) = [s for s in got.out["surfaces"] if s["wireframe_id"] == surface["wireframe_id"]]
        assert entry["area"] == pytest.approx(total, rel=1e-12) and entry["area_beyond"] == pytest.approx(beyond)
        assert entry["share_beyond"] == pytest.approx(beyond / total, rel=1e-12)
        assert entry["max_distance"] == pytest.approx(float(brute.max()), abs=1e-9)
        assert entry["group"] == surface["group"] and entry["vertices"] == len(v)
        assert got.out["threshold"] == threshold and got.out["d_max"] == 30.0


def test_the_support_without_a_surface_lists_all_the_surfaces_and_has_no_values_and_the_default_is_2_dmax(
    world: World,
) -> None:
    w = world
    mid = layered(w)
    got = support(w, mid)
    assert got.out["threshold"] == 60.0 and got.values is None and "values" not in got.out  # D_оп = 2·D_max
    assert [s["group"] for s in got.out["surfaces"]] == [2, 3, 4]
    assert all(s["share_beyond"] is not None and 0.0 <= s["share_beyond"] <= 1.0 for s in got.out["surfaces"])
    wide = support(w, mid, threshold=9e4)
    assert [s["share_beyond"] for s in wide.out["surfaces"]] == [0.0, 0.0, 0.0]
    tiny = support(w, mid, threshold=0.001)
    assert all(s["share_beyond"] > 0.5 for s in tiny.out["surfaces"])  # almost nothing is on a data point


def test_the_share_is_recorded_in_the_statistics_and_the_version_of_the_model_stays(world: World) -> None:
    w = world
    mid = layered(w)
    before = meta_of(w, mid)
    with dialect.read_tx(w.engine) as conn:
        row0 = conn.execute(sa.select(objects.c.version, objects.c.updated_at).where(objects.c.id == mid)).one()
    assert all(g["support_share"] is None for g in before["stats"]["by_group"])
    got = support(w, mid, threshold=30.0)
    assert got.out["recorded"] is True
    after = meta_of(w, mid)
    shares = {g["group"]: g["support_share"] for g in after["stats"]["by_group"]}
    assert shares == {s["group"]: s["share_beyond"] for s in got.out["surfaces"]}
    assert {g["support_threshold"] for g in after["stats"]["by_group"]} == {30.0}
    with dialect.read_tx(w.engine) as conn:
        row1 = conn.execute(sa.select(objects.c.version, objects.c.updated_at).where(objects.c.id == mid)).one()
        info = st.get(conn, mid)
    assert tuple(row1) == tuple(row0) and info["stale"] is False  # the version, the date: the same; not «устарела»
    keep = [k for k in before if k != "stats"]
    assert {k: before[k] for k in keep} == {k: after[k] for k in keep}  # nothing else of the meta moved
    assert {k: v for k, v in before["stats"].items() if k != "by_group"} == {
        k: v for k, v in after["stats"].items() if k != "by_group"}  # fmt: skip
    no_share = [{k: v for k, v in g.items() if k not in ("support_share", "support_threshold")}
                for g in after["stats"]["by_group"]]  # fmt: skip
    assert no_share == [{k: v for k, v in g.items() if k != "support_share"} for g in before["stats"]["by_group"]]
    w.project.read_only = True  # a project «только просмотр»: the map is shown, nothing is written
    try:
        again = support(w, mid, threshold=45.0)
    finally:
        w.project.read_only = False
    assert again.out["recorded"] is False and again.out["surfaces"]
    assert {g["support_threshold"] for g in meta_of(w, mid)["stats"]["by_group"]} == {30.0}


def test_the_values_as_json_or_float32_and_the_refusals_of_the_support(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    mid = layered(w)
    wid = surfaces_of(w, mid)[0]["wireframe_id"]
    got = support(w, mid, wireframe_id=wid, format="f4")
    assert "values" not in got.out and got.values is not None and got.summary["wireframe_id"] == wid
    j = support(w, mid, wireframe_id=wid)
    assert np.array_equal(j.values, got.values) and len(j.out["values"]) == j.summary["vertices"]
    monkeypatch.setattr(cs, "JSON_MAX", 10)
    err = refused(lambda: support(w, mid, wireframe_id=wid), 422, "bad_param")
    assert err.extra["param"] == "format" and "f4" in err.hint
    assert "values" not in support(w, mid, wireframe_id=wid, format="f4").out
    refused(lambda: support(w, mid, wireframe_id=999999), 404, "wireframe_not_found")
    refused(lambda: cs.parse({"format": "xml"}), 422, "bad_param")
    refused(lambda: cs.parse({"threshold": -1}), 422, "bad_param")
    refused(lambda: cs.parse({"wireframe_id": "x"}), 422, "bad_param")
    refused(lambda: cs.parse([]), 422, "bad_request")
    assert cs.parse(None).threshold is None


# --- the states: not built, building, «устарела» ------------------------------------------------------------


def test_a_model_that_is_not_built_is_refused_and_a_stale_one_is_answered_with_a_warning(world: World) -> None:
    w = world
    mid = make(w, "Новая", groups_of("SS"), h=8.0)["id"]
    refused(lambda: loaded(w, mid), 409, "not_built")
    refused(lambda: support(w, mid), 409, "not_built")
    refused(lambda: loaded(w, 99999), 404, "model_not_found")
    run(w, mid)
    assert loaded(w, mid).info["stale"] is False
    with dialect.write_tx(w.engine) as conn:
        version = int(sb.stored(conn, mid).version)
        st.update(conn, w.store, w.project, mid, {"h": 10.0}, version)  # another step: the parameters changed
    got = loaded(w, mid)
    out = ck.page(got, check_spec())
    assert out["model"]["stale"] is True and out["model"]["stale_reason"] == "изменены параметры"
    (warning,) = out["warnings"]
    assert warning["code"] == "model_stale" and "устарела" in warning["message"] and warning["hint"]
    assert out["total"] > 0 and support(w, mid).out["warnings"] == out["warnings"]  # still the table of the last build
    with dialect.write_tx(w.engine) as conn:
        st.set_state(conn, mid, "building")
    refused(lambda: loaded(w, mid), 409, "model_building")
    refused(lambda: support(w, mid), 409, "model_building")


def test_the_cache_follows_the_files_and_the_outputs_of_the_model(world: World) -> None:
    w = world
    mid = layered(w)
    first = loaded(w, mid).table
    assert loaded(w, mid).table is first  # the same arrays while nothing moved
    with dialect.write_tx(w.engine) as conn:
        version = int(sb.stored(conn, mid).version)
    run(w, mid, update=True)  # nothing changed: the files are the same bytes, the outputs are replaced in place
    after = loaded(w, mid).table
    assert np.array_equal(after.f_model, first.f_model) and after.n == first.n
    assert version > 0


# --- the route ----------------------------------------------------------------------------------------------------


def test_the_route_answers_with_the_form_of_the_contract_the_csv_and_the_errors_in_russian(world: World) -> None:
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bf_core.api.errors import api_error_handler
    from bf_core.model import routes_check
    from bf_core.projects.service import projects

    w = world
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(routes_check.router, prefix="/api/v1/implicit")
    app.dependency_overrides[projects] = lambda: SimpleNamespace(require_current=lambda writable=False: w.project)
    mid = plane_with_an_outlier(w)
    c = TestClient(app)
    base = f"/api/v1/implicit/models/{mid}"
    r = c.get(f"{base}/check", params={"page": "1", "size": "3", "sort": "residual"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert set(got) >= {"group", "n", "max_abs_residual", "page", "size", "total", "rows", "warnings", "highlight"}
    assert len(got["rows"]) == 3 and got["total"] == N and got["rows"][0]["z"] == 8.0
    assert set(got["rows"][0]) >= {
        "i",
        "group",
        "type",
        "source",
        "from",
        "to",
        "vertex",
        "x",
        "y",
        "z",
        "f_model",
        "f_data",
        "residual",
        "distance",
        "over",
    }
    csv1 = c.get(f"{base}/check", params={"format": "csv"})
    csv2 = c.get(f"{base}/check.csv")
    assert csv1.status_code == 200 and csv1.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv1.headers["content-disposition"] and csv1.content == csv2.content
    assert csv1.content.decode("utf-8-sig").splitlines()[0].split(";")[:4] == ["№", "Группа", "Порог", "Тип"]
    r = c.get(f"{base}/check", params={"highlight": "x"})
    assert r.status_code == 422 and r.json()["param"] == "highlight" and r.json()["hint"]
    r = c.get(f"{base}/check", params={"group": "7"})
    assert r.status_code == 404 and r.json()["code"] == "group_not_found" and r.json()["hint"]
    r = c.get(f"{base}/check", params={"size": "abc"})
    assert r.status_code == 422 and r.json()["param"] == "size"
    (surface,) = surfaces_of(w, mid)
    r = c.post(f"{base}/support", json={"wireframe_id": surface["wireframe_id"], "format": "f4"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
    values = np.frombuffer(r.content, dtype="<f4")
    import json as jsonlib

    head = jsonlib.loads(r.headers["x-bf-support"])
    assert len(values) == head["surface"]["vertices"] and head["threshold"] == 60.0
    r = c.post(f"{base}/support", json={"threshold": 12.5})
    assert r.status_code == 200 and r.json()["threshold"] == 12.5 and r.json()["surfaces"][0]["group"] == 1
    r = c.post(f"{base}/support", json={"format": "yaml"})
    assert r.status_code == 422 and r.json()["param"] == "format"
    r = c.post("/api/v1/implicit/models/99999/support", json={})
    assert r.status_code == 404 and r.json()["code"] == "model_not_found" and r.json()["hint"]
    r = c.get("/api/v1/implicit/models/99999/check")
    assert r.status_code == 404 and r.json()["code"] == "model_not_found"


def test_the_modules_do_not_load_numba_or_scipy_with_their_import() -> None:
    code = (
        "import sys, bf_core.model.routes_check\n"
        "bad = [m for m in ('numba', 'manifold3d', 'scipy', 'numpy') if m in sys.modules]\n"
        "raise SystemExit(3 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-500:]
    code = (
        "import sys, bf_core.model.check, bf_core.model.check_support\n"
        "bad = [m for m in ('numba', 'manifold3d', 'scipy') if m in sys.modules]\n"
        "raise SystemExit(3 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-500:]
