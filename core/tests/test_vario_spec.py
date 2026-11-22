"""Parameters of the variography (подшаг 16.11; ТЗ 8.6.4, ADR 0022 §2–7, §14): the bodies of `experimental`,
`preview`, `map` and the model checked against the project and the ranges — out of a range → 422 `bad_param`
{param}; the defaults that follow L filled in by `resolved`; the canonical body; no numpy at import."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.drillholes import store as dh
from bf_core.geostat.variogram import spec
from tests.dbutil import make_engine

HOLES = (("A-1", 0.0, 0.0), ("B-2", 100.0, 0.0), ("C-3", 0.0, 100.0))
SAMPLES = [  # hole, from, to, AU, ROCK, DECL_W
    ("A-1", 0.0, 2.0, 1.0, "гранит", 1.0),
    ("A-1", 2.0, 4.0, 2.0, "гранит", 1.0),
    ("A-1", 4.0, 6.0, None, "диорит", 2.0),  # no value: counted, never 0
    ("A-1", 6.0, 8.0, 4.0, "диорит", None),  # no weight: in the pairs, not in the table of scores
    ("B-2", 0.0, 2.0, 3.0, "гранит", 0.5),
    ("B-2", 2.0, 4.0, 5.0, "диорит", 0.5),
    ("B-2", 4.0, 6.0, 0.5, "гранит", 0.5),
    ("C-3", 0.0, 2.0, 2.5, "диорит", 1.0),
    ("C-3", 2.0, 4.0, 1.5, "гранит", 1.0),
    ("D-9", 0.0, 2.0, 7.0, "гранит", 1.0),  # a hole without a collar: no position
]
LITHO = [  # hole, from, to, ZONE, CODE
    ("A-1", 0.0, 4.0, "окисл", 1.0),
    ("A-1", 4.0, 8.0, "перв", 2.5),
    ("B-2", 0.0, 6.0, "окисл", 1.0),
    ("C-3", 0.0, 3.0, "перв", 2.5),  # the middle of C-3 3–4 is outside
]


def noop(_p: float | None, _m: str | None) -> None:
    pass


def make_project(tmp_path: Path, samples: list[Any] = SAMPLES) -> tuple[Any, int, int, int]:
    engine = make_engine("sqlite", tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
        dh.replace_collars(conn, set_id, [dh.CollarRow(h, x=x, y=y, z=100.0, td=20.0, azimuth=0.0, dip=-90.0)
                                          for h, x, y in HOLES])  # fmt: skip
    a = dh.write_table(engine, set_id, dh.TableData(
        name="Пробы", kind="assay", holes=[r[0] for r in samples], from_m=[r[1] for r in samples],
        to_m=[r[2] for r in samples],
        columns=[dh.ColumnSpec("AU", "number", "г/т", "component"), dh.ColumnSpec("ROCK", "text"),
                 dh.ColumnSpec("DECL_W", "number")],
        values=[[r[3] for r in samples], [r[4] for r in samples], [r[5] for r in samples]]),
        job_id=None, tick=noop)  # fmt: skip
    b = dh.write_table(engine, set_id, dh.TableData(
        name="Литология", kind="interval", holes=[r[0] for r in LITHO], from_m=[r[1] for r in LITHO],
        to_m=[r[2] for r in LITHO], columns=[dh.ColumnSpec("ZONE", "text"), dh.ColumnSpec("CODE", "number")],
        values=[[r[3] for r in LITHO], [r[4] for r in LITHO]]), job_id=None, tick=noop)  # fmt: skip
    project = SimpleNamespace(engine=engine, uid=f"test:{tmp_path}", folder=tmp_path)
    return project, set_id, int(a["table_id"]), int(b["table_id"])


@pytest.fixture()
def proj(tmp_path: Path):  # type: ignore[no-untyped-def]
    project, set_id, a, b = make_project(tmp_path)
    yield project, set_id, a, b
    project.engine.dispose()


def parse(project: Any, fn: Any, body: dict[str, Any]) -> Any:
    with dialect.read_tx(project.engine) as conn:
        return fn(conn, body)


def refused(project: Any, fn: Any, body: dict[str, Any]) -> ApiError:
    with pytest.raises(ApiError) as e:
        parse(project, fn, body)
    return e.value


def test_defaults_resolved_from_the_median_length(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, a, _ = proj
    s = parse(project, spec.parse_experimental, {"set_id": set_id, "table": "пробы", "field": "au"})
    assert s.data.table_id == a and s.data.field == "AU" and s.data.unit == "г/т" and s.data.transform == "none"
    assert s.data.weights == "DECL_W"  # null — DECL_W when the table has it
    assert s.axes == (0.0, 0.0, 0.0) and s.directions == [] and s.lags.lag is None and s.downhole
    assert spec.sig2(5 * 1.37) == 6.9 and spec.sig2(5 * 2.0) == 10.0
    r = s.resolved(spec.defaults_for(2.0))
    assert (r.lags.lag, r.lags.nlag, r.lags.tol) == (10.0, 15, 5.0)
    c = r.common
    assert (c.az_tol, c.dip_tol, c.band_h, c.band_v) == (22.5, 22.5, 30.0, 20.0)
    assert (r.min_pairs, r.dh.lag, r.dh.nlag, r.dh.tol, r.seed) == (30, 2.0, 15, 1.0, 12345)
    body = r.hash_params()
    assert body["op"] == "experimental" and body["table"] == str(a) and body["weights"] == "DECL_W"
    assert json.loads(json.dumps(body, allow_nan=False)) == body  # JSON-ready: no NaN, no ∞
    d = spec.defaults_json(spec.defaults_for(2.0))
    assert d["map"] == {"nu": 20, "nv": 20, "cell": 10.0, "thickness": 20.0, "min_pairs": 5}
    assert d["fan"] == {"n": 12, "tol": 7.5, "q": 1.0} and d["band_h"] == 30.0
    assert spec.ranges_json()["band_h"]["inf"] is True and spec.ranges_json()["nlag"]["integer"] is True
    with pytest.raises(ApiError) as e:
        spec.defaults_for(math.nan)
    assert e.value.code == "no_lengths"


def test_given_values_infinity_angles_and_directions(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = proj
    base = {"set_id": set_id, "table": "Пробы", "field": "AU"}
    s = parse(
        project,
        spec.parse_experimental,
        {
            **base,
            "lag": "12,5",
            "nlag": 20.0,
            "lag_tol": 12.5,
            "band_h": "inf",
            "band_v": "∞",
            "weights": False,
            "transform": "nscore",
            "axes": {"ang1": 360, "ang2": -10, "ang3": -180},
            "downhole": False,
            "seed": 7,
        },
    )
    r = s.resolved(spec.defaults_for(2.0))
    assert r.axes == (0.0, -10.0, 180.0) and r.data.weights is None and r.data.transform == "nscore"
    assert (r.lags.lag, r.lags.nlag, r.lags.tol) == (12.5, 20, 12.5) and not r.downhole and r.seed == 7
    assert math.isinf(r.common.band_h) and r.body["band_h"] == "inf" and r.body["band_v"] == "inf"
    s = parse(
        project,
        spec.parse_experimental,
        {**base, "az_tol": 45, "directions": [{"az": 360, "plunge": 30, "band_h": 5}, {"az": 90, "dip_tol": 90}]},
    )
    r = s.resolved(spec.defaults_for(2.0))
    assert r.axes is None and [(d.az, d.plunge) for d in r.directions] == [(0.0, 30.0), (90.0, 0.0)]
    assert (r.directions[0].az_tol, r.directions[0].band_h, r.directions[1].dip_tol) == (45.0, 5.0, 90.0)
    assert r.directions[1].band_h == 30.0  # the common one, 3L
    assert r.body["directions"][0] == {"az": 0.0, "plunge": 30.0, "az_tol": 45.0, "dip_tol": 22.5, "band_h": 5.0,
                                       "band_v": 20.0}  # fmt: skip


@pytest.mark.parametrize(("extra", "param"), [
    ({"nlag": 0}, "nlag"), ({"nlag": 101}, "nlag"), ({"nlag": 2.5}, "nlag"), ({"lag": 0}, "lag"),
    ({"lag": -1}, "lag"), ({"lag": "abc"}, "lag"), ({"lag": True}, "lag"), ({"az_tol": 0}, "az_tol"),
    ({"az_tol": 90.5}, "az_tol"), ({"dip_tol": -5}, "dip_tol"), ({"band_h": 0}, "band_h"),
    ({"band_v": "inf-"}, "band_v"), ({"min_pairs": 0}, "min_pairs"), ({"min_pairs": 1001}, "min_pairs"),
    ({"dh_nlag": 200}, "dh_nlag"), ({"dh_lag": 0}, "dh_lag"), ({"seed": -1}, "seed"),
    ({"axes": {"ang1": 361}}, "axes.ang1"), ({"axes": {"ang2": 91}}, "axes.ang2"),
    ({"axes": {"ang3": 180.5}}, "axes.ang3"), ({"axes": [0, 0, 0]}, "axes"),
    ({"directions": []}, "directions"), ({"directions": [{"az": 0}] * 73}, "directions"),
    ({"directions": [{"az": 0}, {"az": -1}]}, "directions.1.az"),
    ({"directions": [{"plunge": 95}]}, "directions.0.plunge"),
    ({"directions": [{"az_tol": 0}]}, "directions.0.az_tol"),
    ({"directions": [{"az": 0}], "axes": {"ang1": 0}}, "directions"),
    ({"transform": "log"}, "transform"), ({"downhole": "да"}, "downhole"),
    ({"field": "ROCK"}, "field"), ({"field": ""}, "field"), ({"weights": "ROCK"}, "weights"),
    ({"domain": {"field": "ZONE", "table": "Литология"}}, "domain.values"),
    ({"domain": {"field": " ", "values": ["a"]}}, "domain.field"),
    ({"domain": {"wireframe_ids": [1]}}, "domain"),
    ({"domain": {"field": "CODE", "table": "Литология", "values": ["x"]}}, "domain.values.0"),
])  # fmt: skip
def test_out_of_range_is_422_bad_param(proj, extra: dict[str, Any], param: str) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = proj
    e = refused(project, spec.parse_experimental, {"set_id": set_id, "table": "Пробы", "field": "AU", **extra})
    assert (e.status, e.code, e.extra.get("param")) == (422, "bad_param", param), e.message
    assert e.message and e.hint  # Russian text with advice


def test_relative_ranges_after_the_lag(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = proj
    base = {"set_id": set_id, "table": "Пробы", "field": "AU"}
    s = parse(project, spec.parse_experimental, {**base, "lag_tol": 6})  # the default L is 5 × 1 = 5 m
    with pytest.raises(ApiError) as e:
        s.resolved(spec.defaults_for(1.0))
    assert (e.value.code, e.value.extra["param"]) == ("bad_param", "lag_tol")
    assert s.resolved(spec.defaults_for(2.0)).lags.tol == 6.0  # L = 10 m
    s = parse(project, spec.parse_experimental, {**base, "dh_lag": 2, "dh_lag_tol": 3})
    with pytest.raises(ApiError) as e:
        s.resolved(spec.defaults_for(2.0))
    assert e.value.extra["param"] == "dh_lag_tol"


def test_not_found_and_field_errors(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = proj
    e = refused(project, spec.parse_experimental, {"table": "Пробы", "field": "AU"})
    assert (e.status, e.code, e.extra["param"]) == (404, "set_not_found", "set_id")
    e = refused(project, spec.parse_experimental, {"set_id": set_id + 99, "table": "Пробы", "field": "AU"})
    assert (e.status, e.code) == (404, "set_not_found")
    e = refused(project, spec.parse_experimental, {"set_id": set_id, "table": "Керн", "field": "AU"})
    assert (e.status, e.code, e.extra["param"]) == (404, "table_not_found", "table")
    e = refused(project, spec.parse_experimental, {"set_id": set_id, "table": "Пробы", "field": "PB"})
    assert (e.status, e.code, e.extra["param"]) == (422, "field_not_found", "field")
    e = refused(
        project,
        spec.parse_experimental,
        {
            "set_id": set_id,
            "table": "Пробы",
            "field": "AU",
            "domain": {"field": "ZONE", "table": "Нет", "values": ["a"]},
        },
    )
    assert (e.status, e.code, e.extra["param"]) == (404, "table_not_found", "domain.table")


def test_domain_text_and_number(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, b = proj
    s = parse(
        project,
        spec.parse_experimental,
        {
            "set_id": set_id,
            "table": "Пробы",
            "field": "AU",
            "domain": {"field": "zone", "table": str(b), "values": [" окисл ", "окисл", "перв"]},
        },
    )
    assert s.data.domain == spec.DomainSel(b, "Литология", "ZONE", "text", ("окисл", "перв"))
    s = parse(
        project,
        spec.parse_experimental,
        {
            "set_id": set_id,
            "table": "Пробы",
            "field": "AU",
            "domain": {"field": "CODE", "table": "Литология", "values": ["2,5", 1]},
        },
    )
    assert s.data.domain is not None and s.data.domain.values == ("2.5", "1") and s.data.domain.kind == "number"
    assert s.data.tables()[1] == (b, ["CODE"])
    s = parse(project, spec.parse_experimental, {"set_id": set_id, "table": "Пробы", "field": "AU", "weights": "decl_w",
                                                 "domain": {"field": "ROCK", "values": ["гранит"]}})  # fmt: skip
    assert s.data.tables() == [(s.data.table_id, ["AU", "DECL_W", "ROCK"])]
    assert s.resolved(spec.defaults_for(2.0)).body["domain"] == {
        "table": str(s.data.table_id),
        "field": "ROCK",
        "values": ["гранит"],
    }


def test_map_spec(proj) -> None:  # type: ignore[no-untyped-def]
    project, set_id, _, _ = proj
    base = {"set_id": set_id, "table": "Пробы", "field": "AU"}
    s = parse(project, spec.parse_map, base).resolved(spec.defaults_for(2.0))
    assert s.plane == (0.0, 0.0, 0.0) and s.what == ("fan", "map") and (s.fan_n, s.fan_tol, s.q) == (12, 7.5, 1.0)
    assert (s.nu, s.nv, s.cell, s.thickness, s.map_min_pairs, s.dip_tol, s.band_v) == (20, 20, 10.0, 20.0, 5,
                                                                                         22.5, 20.0)  # fmt: skip
    assert s.hash_params()["op"] == "map" and s.body["map"]["thickness"] == 20.0
    s = parse(project, spec.parse_map, {**base, "what": "map", "fan": {"n": 24}, "map": {"thickness": "inf",
                                                                                         "nu": 100}})  # fmt: skip
    s = s.resolved(spec.defaults_for(2.0))
    assert s.what == ("map",) and s.fan_tol == 3.75 and s.nu == 100 and s.body["map"]["thickness"] == "inf"
    for extra, param in (({"fan": {"n": 3}}, "fan.n"), ({"fan": {"n": 73}}, "fan.n"), ({"fan": {"q": 1.3}}, "fan.q"),
                         ({"fan": {"tol": 0}}, "fan.tol"), ({"map": {"nu": 101}}, "map.nu"),
                         ({"map": {"cell": 0}}, "map.cell"), ({"map": {"min_pairs": 0}}, "map.min_pairs"),
                         ({"what": ["rose"]}, "what"), ({"plane": {"ang2": -100}}, "plane.ang2"),
                         ({"map": 5}, "map")):  # fmt: skip
        e = refused(project, spec.parse_map, {**base, **extra})
        assert (e.status, e.code, e.extra.get("param")) == (422, "bad_param", param)


def test_model() -> None:
    m = spec.parse_model(
        {
            "c0": "0,1",
            "structures": [
                {"type": "spherical", "c": 0.5, "ang1": 30, "ang2": -10, "ang3": 5, "a1": 100, "a2": 50, "a3": 10},
                {"type": "gaussian", "c": 0.4, "ang1": 99, "a1": 200, "a2": 150, "a3": 20},
            ],
        }
    )
    assert m["type"] == "semivariogram" and m["c0"] == 0.1 and m["common_axes"] is True
    assert [(s["ang1"], s["ang2"], s["ang3"]) for s in m["structures"]] == [(30.0, -10.0, 5.0)] * 2  # common axes
    m = spec.parse_model(
        {
            "type": "nscore",
            "common_axes": False,
            "structures": [{"c": 1, "a1": 1, "a2": 1, "a3": 1}, {"c": 0, "ang1": 360, "a1": 2, "a2": 2, "a3": 2}],
        }
    )
    assert m["c0"] == 0.0 and m["structures"][0]["type"] == "spherical" and m["structures"][1]["ang1"] == 0.0
    for raw, param in ((None, "model"), ({"c0": -1}, "model.c0"), ({"type": "x"}, "model.type"),
                       ({"structures": [{}] * 4}, "model.structures"),
                       ({"structures": [{"c": 1, "a1": 1, "a2": 0, "a3": 1}]}, "model.structures.0.a2"),
                       ({"structures": [{"c": 1, "a1": 1, "a2": 1}]}, "model.structures.0.a3"),
                       ({"structures": [{"c": -1, "a1": 1, "a2": 1, "a3": 1}]}, "model.structures.0.c"),
                       ({"structures": [{"type": "cubic", "c": 1, "a1": 1, "a2": 1, "a3": 1}]},
                        "model.structures.0.type")):  # fmt: skip
        with pytest.raises(ApiError) as e:
            spec.parse_model(raw)
        assert (e.value.code, e.value.extra.get("param")) == ("bad_param", param)


def test_no_numpy_at_import() -> None:
    code = "import sys, bf_core.geostat.variogram.spec; sys.exit('numpy' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0
