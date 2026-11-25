"""`model.data.load` on a project (подшаг 19.7; ADR 0026 решения 4–6; ТЗ 8.9.2): the fixture scene (`tests/fixtures/
implicit`) imported as a set with its tables and layers of strings; the codes of a group → contacts and s(t) equal to
those of the arrays, hand data of layers by role, the grades of a shell from a table and from a result of sample
processing (linear, indicator, nscore; a missing grade is no point), `no_contacts`, `too_many_points`, the cancel, the
sides of a vein."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect, migrate
from bf_core.db.schema import dh_results
from bf_core.drillholes import store as dh
from bf_core.formats.lineset import from_lines, from_points
from bf_core.jobs.context import JobCancelled
from bf_core.lines import store as ls_store
from bf_core.model import data as md
from bf_core.model import data_holes as dhm
from bf_core.model.spec import parse_model
from bf_core.model.spec_parts import POINT_TYPES
from bf_core.storage.blobs import BlobStore
from tests.dbutil import make_engine
from tests.test_model_data import read_csv, scene_arrays

pytestmark = pytest.mark.db


def noop(_p: float | None, _m: str | None) -> None:
    pass


class World:
    """The fixture scene in a project: the set, the tables of codes and of assays, the layers of strings."""

    def __init__(self, db_url: str, tmp_path: Path, *, litho: list[Any] | None = None) -> None:
        self.engine = make_engine(db_url, tmp_path)
        migrate.upgrade(self.engine, "project")
        self.project = SimpleNamespace(engine=self.engine, uid="data-test", folder=tmp_path)
        self.store = BlobStore(tmp_path)
        collars = read_csv("collars.csv")
        with dialect.write_tx(self.engine) as conn:
            self.set_id = dh.create_set(conn, "Скважины")
            dh.replace_collars(
                conn,
                self.set_id,
                [dh.CollarRow(r["HOLE"], float(r["X"]), float(r["Y"]), float(r["Z"]), float(r["TD"]),
                              float(r["AZIMUTH"]), float(r["DIP"])) for r in collars],
            )  # fmt: skip
        rows = read_csv("lithology.csv") if litho is None else litho
        self.litho = self._table("Литология", "interval", rows, "CODE", "text")
        assays = read_csv("assays.csv")
        self.assays = self._table("Пробы", "assay", assays, "AU", "number")
        self.layers = self._strings()

    def _table(self, name: str, kind: str, rows: list[Any], column: str, ctype: str) -> int:
        values = (
            [r[column] for r in rows] if ctype == "text" else [float(r[column]) if r[column] else None for r in rows]
        )
        data = dh.TableData(name, kind, [r["HOLE"] for r in rows], [float(r["FROM"]) for r in rows],
                            [float(r["TO"]) for r in rows], [dh.ColumnSpec(column, ctype)], [values])  # fmt: skip
        return int(dh.write_table(self.engine, self.set_id, data, job_id=None, tick=noop)["table_id"])

    def _strings(self) -> dict[str, int]:
        by: dict[str, list[tuple[int, int, list[float]]]] = {}
        for r in read_csv("strings.csv"):
            by.setdefault(r["LAYER"], []).append(
                (int(r["LINE"]), int(r["CLOSED"]), [float(r["X"]), float(r["Y"]), float(r["Z"])])
            )
        out: dict[str, int] = {}
        with dialect.write_tx(self.engine) as conn:
            file_id = ls_store.create_file(conn, self.store, name="Ручные данные", meta={"format": "test"})
            for name, items in by.items():
                lines: dict[int, list[list[float]]] = {}
                closed: dict[int, int] = {}
                for line, flag, v in items:
                    lines.setdefault(line, []).append(v)
                    closed[line] = flag
                keys = sorted(lines)
                if all(len(lines[k]) == 1 for k in keys):
                    ls = from_points(name, np.array([lines[k][0] for k in keys]))
                else:
                    ls = from_lines(name, [np.array(lines[k]) for k in keys], [bool(closed[k]) for k in keys])
                out[name] = ls_store.put_layer(conn, self.store, ls, file_id=file_id)
        return out

    def spec(self, groups: list[dict[str, Any]], manual: list[dict[str, Any]] | None = None, **data: Any) -> Any:
        sel = {"set_id": self.set_id, "table": str(self.litho), "field": "CODE", "d_max": 30, "step": 5, **data}
        body = {"boundary": {"box": [0, 0, -300, 400, 400, 300]}, "groups": groups, "manual": manual or [], "data": sel}
        return parse_model(body)

    def result(self) -> int:
        """A `dh_results` row «Композиты» pointing at the table of assays."""
        with dialect.write_tx(self.engine) as conn:
            row = dict(set_id=self.set_id, table_id=self.assays, op="length", name="Композиты", params={}, inputs={},
                       sha256="0" * 64, algo="t", report={}, created_at=dt.datetime.now(dt.UTC))  # fmt: skip
            return int(conn.execute(sa.insert(dh_results).values(**row).returning(dh_results.c.id)).scalar_one())


@pytest.fixture()
def world(db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    w = World(db_url, tmp_path)
    yield w
    w.engine.dispose()


def domain(gid: int, name: str, *codes: str) -> dict[str, Any]:
    return {"id": gid, "name": name, "type": "domain", "codes": list(codes)}


def shell(gid: int, table: Any, **params: Any) -> dict[str, Any]:
    given = {"source": "table", "table": str(table), "field": "AU", "thresholds": [0.5], **params}
    return {"id": gid, "name": f"Au {gid}", "type": "shell", "codes": [], "params": given}


# --- codes --------------------------------------------------------------------------------------------------------


def test_load_reads_the_codes_and_matches_the_arrays(world: World) -> None:
    spec = world.spec([domain(1, "Интрузия", "INT"), domain(2, "Песчаник", "SS", "SH")])
    pts = md.load(world.project, spec)
    sc = scene_arrays()
    for gid, codes in ((1, ("INT",)), (2, ("SS", "SH"))):
        state = np.where(np.isin(sc["code"], codes), 1, 0).astype(np.int8)
        direct = md.assemble([dhm.domain_points(sc["lines"], state, sc["traj"], np.arange(sc["lines"].n_holes),
                                                d_max=30.0, step=5.0, group=gid, names=sc["names"])])  # fmt: skip
        mine = pts.of_group(gid)
        assert mine.n == direct.n > 0
        assert mine.xyz.tobytes() == direct.xyz.tobytes() and mine.f.tobytes() == direct.f.tobytes()
        assert [mine.sources[s] for s in mine.src] == [direct.sources[s] for s in direct.src]
    assert pts.group.tolist() == sorted(pts.group.tolist())  # groups ascending
    assert pts.counts["points"] == pts.n and pts.counts["groups"]["1"]["contact"] > 0
    assert pts.of_group(1).sources and all(s.startswith("H") for s in pts.of_group(1).sources)
    assert md.Points.from_parquet(pts.to_parquet()).to_parquet() == pts.to_parquet()


def test_load_a_chosen_group_only(world: World) -> None:
    spec = world.spec([domain(1, "Интрузия", "INT"), domain(2, "Жила", "VN")])
    only = md.load(world.project, spec, groups=[2])
    assert set(only.group.tolist()) == {2}
    assert md.load(world.project, spec, groups=[spec.groups[0]]).group.max() == 1


def test_a_group_whose_codes_are_never_met_has_no_contacts(world: World) -> None:
    spec = world.spec([domain(1, "Нет такой", "ZZZ")])
    with pytest.raises(ApiError) as err:
        md.load(world.project, spec)
    assert err.value.code == "no_contacts" and err.value.status == 422 and err.value.extra == {"group": 1}


def test_missing_code_is_a_gap_in_the_project(db_url: str, tmp_path: Path) -> None:
    rows = [
        {"HOLE": "H001", "FROM": "0", "TO": "20", "CODE": "INT"},
        {"HOLE": "H001", "FROM": "20", "TO": "30", "CODE": ""},
        {"HOLE": "H001", "FROM": "30", "TO": "60", "CODE": "SS"},
    ]
    w = World(db_url, tmp_path, litho=rows)
    try:
        spec = w.spec([{"id": 1, "name": "Вручную", "type": "domain", "codes": ["INT"]}],
                      [{"layer_id": next(iter(w.layers.values())), "role": "inside", "group": 1}])  # fmt: skip
        pts = md.load(w.project, spec)
        codes_only = pts.take(np.flatnonzero(pts.kind != POINT_TYPES.index("manual")))
        assert not (codes_only.kind == POINT_TYPES.index("contact")).any()  # the gap is not «outside D» — no contact
        # D and the rock below the gap are free at both ends: −D_max in D, +D_max outside, nothing in the gap
        assert sorted(set(codes_only.f.tolist())) == [-30.0, 30.0]
    finally:
        w.engine.dispose()


# --- hand data ----------------------------------------------------------------------------------------------------


def test_load_hand_layers_by_role(world: World) -> None:
    ring, far = world.layers["Внутри INT"], world.layers["Снаружи INT"]
    contact = world.layers["Контакт SS-SH"]
    manual = [{"layer_id": contact, "role": "contact", "group": 1}, {"layer_id": ring, "role": "inside", "group": 1},
              {"layer_id": far, "role": "outside", "group": 1}]  # fmt: skip
    spec = world.spec([{"id": 1, "name": "Ручная", "type": "domain", "codes": []}], manual)
    pts = md.load(world.project, spec)
    assert set(pts.kind.tolist()) == {POINT_TYPES.index("manual")}
    assert set(pts.f.tolist()) == {0.0, -15.0, 15.0}
    assert pts.f.tolist().count(15.0) == 4  # the four points outside
    assert sorted(pts.sources) == ["слой «Внутри INT»", "слой «Контакт SS-SH»", "слой «Снаружи INT»"]
    assert (pts.vertex >= 0).all() and np.isnan(pts.span).all()
    only_contact = world.spec([{"id": 1, "name": "Ручная", "type": "domain", "codes": []}],
                              [{"layer_id": contact, "role": "contact", "group": 1}])  # fmt: skip
    with pytest.raises(ApiError) as err:
        md.load(world.project, only_contact)  # contacts only: no change of sign
    assert err.value.code == "no_contacts"


def test_a_layer_that_is_not_there(world: World) -> None:
    spec = world.spec([{"id": 1, "name": "Ручная", "type": "domain", "codes": []}],
                      [{"layer_id": 987654, "role": "inside", "group": 1}])  # fmt: skip
    with pytest.raises(ApiError) as err:
        md.load(world.project, spec)
    assert err.value.status == 404 and err.value.code == "layer_not_found"


# --- grades -------------------------------------------------------------------------------------------------------


def assay_arrays() -> tuple[np.ndarray, np.ndarray]:
    rows = read_csv("assays.csv")
    mid = [(r["HOLE"], 0.5 * (float(r["FROM"]) + float(r["TO"]))) for r in rows]
    return np.array([float(r["AU"]) if r["AU"] else np.nan for r in rows]), np.array(mid, dtype=object)


def test_shell_numeric_missing_grade_is_no_point(world: World) -> None:
    spec = world.spec([shell(1, world.assays, transform="linear", mode="numeric", thresholds=[0.5, 2.0])])
    pts = md.load(world.project, spec)
    g, _ = assay_arrays()
    assert np.isnan(g).any() and pts.n == int((~np.isnan(g)).sum())  # a blank is no point, never 0
    assert (pts.kind == POINT_TYPES.index("value")).all() and (pts.f > 0).all()
    assert sorted(pts.f.tolist()) == sorted(g[~np.isnan(g)].tolist())
    assert pts.levels == {1: [0.5, 2.0]}
    assert any("без содержания" in t for t in pts.notes) and not np.isnan(pts.xyz).any()
    # the points sit at the middles of the samples: the first sample of H001 is 0–5 m on the hole
    first = world.spec([shell(1, world.assays)])
    p0 = md.load(world.project, first).take([0])
    assert p0.span.shape == (1, 2) and p0.span[0, 1] - p0.span[0, 0] == 5.0


def test_shell_log_indicator_and_nscore(world: World) -> None:
    g = assay_arrays()[0]
    g = g[~np.isnan(g)]
    log = md.load(world.project, world.spec([shell(1, world.assays, transform="log")]))
    assert log.n == g.size and sorted(log.f.tolist()) == pytest.approx(sorted(np.log(g).tolist()))
    assert log.levels == {1: [pytest.approx(np.log(0.5))]}
    ind = md.load(world.project, world.spec([shell(1, world.assays, mode="indicator", thresholds=[0.5, 2.0], p=0.4)]))
    assert sorted(ind.f.tolist()) == sorted(g.tolist()) and ind.levels == {1: [0.5, 2.0]}  # the grade itself
    assert md.indicator(ind.f, 2.0).tolist().count(0.5) == int((g >= 2.0).sum())
    ns = md.load(world.project, world.spec([shell(1, world.assays, transform="nscore")]))
    assert ns.n == g.size and abs(float(ns.f.mean())) < 0.05 and 0.9 < float(ns.f.std()) < 1.1
    assert ns.levels[1][0] < ns.f.max()


def test_shell_from_a_result_of_sample_processing(world: World) -> None:
    rid = world.result()
    comp = {"id": 1, "name": "Композиты", "type": "shell", "codes": [],
            "params": {"source": "composites", "result_id": rid, "field": "AU", "thresholds": [1.0]}}  # fmt: skip
    a = md.load(world.project, world.spec([comp]))
    b = md.load(world.project, world.spec([shell(1, world.assays, thresholds=[1.0])]))
    assert a.xyz.tobytes() == b.xyz.tobytes() and a.f.tobytes() == b.f.tobytes()
    gone = dict(comp, params={**comp["params"], "result_id": rid + 99})
    with pytest.raises(ApiError) as err:
        md.load(world.project, world.spec([gone]))
    assert err.value.status == 404 and err.value.code == "result_not_found"


def test_a_text_field_is_not_a_grade_and_unknown_things_are_refused(world: World) -> None:
    with pytest.raises(ApiError) as err:
        md.load(world.project, world.spec([shell(1, world.litho, field="CODE")]))
    assert err.value.code == "bad_param" and err.value.extra == {"param": "groups.0.params.field"}
    with pytest.raises(ApiError) as err:
        md.load(world.project, world.spec([shell(1, world.assays, field="NOPE")]))
    assert err.value.code == "field_not_found"
    with pytest.raises(ApiError) as err:
        md.load(world.project, world.spec([shell(1, 99999)]))
    assert err.value.status == 404 and err.value.code == "table_not_found"
    with pytest.raises(ApiError) as err:
        md.load(world.project, world.spec([domain(1, "Интрузия", "INT")], field="NOPE"))
    assert err.value.code == "field_not_found"


# --- limits, cancel, veins ----------------------------------------------------------------------------------------


def test_too_many_points_and_cancel(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = world.spec([domain(1, "Песчаник", "SS", "SH")])
    monkeypatch.setattr(md, "POINTS_MAX", 100)
    monkeypatch.setattr(dhm, "POINTS_MAX", 100)
    with pytest.raises(ApiError) as err:
        md.load(world.project, spec)
    assert err.value.code == "too_many_points" and err.value.extra["points"] > 100 and "Δ_т" in err.value.hint
    monkeypatch.undo()
    calls = []

    def tick(_p: float | None, _m: str | None) -> None:
        calls.append(1)
        if len(calls) >= 3:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        md.load(world.project, spec, cancel=tick)
    assert len(calls) == 3


def test_vein_sides_are_the_top_and_the_bottom_of_the_intervals(world: World) -> None:
    sc = scene_arrays()
    state = (sc["code"] == "VN").astype(np.int8)
    hang, foot = dhm.vein_sides(sc["lines"], state, sc["traj"], np.arange(sc["lines"].n_holes))
    assert len(hang) == len(foot) == int(state.sum()) > 0  # every VN row is its own stretch
    vein = {"id": 1, "name": "Жила", "type": "vein", "codes": ["VN"], "params": {}}
    spec = world.spec([vein])
    h2, f2, notes = md.load_vein_sides(world.project, spec, spec.groups[0])
    assert h2.tobytes() == hang.tobytes() and f2.tobytes() == foot.tobytes() and isinstance(notes, list)
    # the vein dips steeply: along the holes the top is the hanging wall, higher than the bottom of the same interval
    assert float(np.mean(hang[:, 2] - foot[:, 2])) > 0.0
    assert hang.dtype == np.float64 and np.isfinite(hang).all()
    p = md.load(world.project, spec)  # a vein has no points of its own: the sides come from `load_vein_sides`
    assert p.n == 0
