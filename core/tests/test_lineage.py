"""Graph of dependencies and freshness of computed objects (ADR 0025, ТД35-1…4, -6; 18.Г1): the chain «таблица →
композиты → модель вариограммы → поле блочной модели → отчёт», the states and their reasons, pins, the influence
of a change, `.bfggis` there and back, a project made before the graph, the speed of `status`."""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core import lineage
from bf_core.api.errors import ApiError
from bf_core.composites import results as res
from bf_core.db import dialect, migrate
from bf_core.db.schema import dh_results, dh_tables, objects
from bf_core.db.schema import lineage as lineage_table
from bf_core.drillholes import store as dh
from bf_core.projects.service import projects_of
from bf_core.stats import source as stats_source
from tests.dbutil import make_engine
from tests.test_archive import API, export, wait_job
from tests.test_composites_results import add_result, composites, noop, samples, setup

pytestmark = pytest.mark.db

Node = lineage.Node


def insert_object(conn: sa.Connection, kind: str, name: str, meta: dict[str, Any]) -> int:
    now = dialect.utcnow()
    res_ = conn.execute(
        sa.insert(objects).values(
            kind=kind, name=name, name_key=dialect.name_key(name), sort_key=0.0, state="ready", version=1, meta=meta,
            created_at=now, updated_at=now,
        )
    )  # fmt: skip
    return int(res_.inserted_primary_key[0])  # type: ignore[index]


def table_entry(table_id: int, found: Any) -> dict[str, Any]:
    """`inputs.tables` of a variogram model (`points.Points.inputs`)."""
    return {"id": table_id, "name_key": found.name_key, "token": found.token, "rev": found.rev}


def touch(conn: sa.Connection, object_id: int, **meta: Any) -> None:
    """A change of an object: its meta (and so the fields the providers read) and its version move."""
    row = conn.execute(sa.select(objects.c.meta, objects.c.version).where(objects.c.id == object_id)).one()
    conn.execute(
        sa.update(objects).where(objects.c.id == object_id).values(meta={**row.meta, **meta}, version=row.version + 1)
    )


class Chain(SimpleNamespace):
    """src table → composites (result + table) → variogram model → field of a block model → report; + a control
    branch (another table with its result and a trend) that nothing in the chain reads."""

    engine: sa.Engine
    set_id: int
    src: int
    comp: int
    result: int
    vario: int
    model: int
    report: int
    trend: int
    other: int
    other_result: int

    def field(self) -> Node:
        return Node("bm_field", self.model, "au_ok")

    def nodes(self) -> dict[str, Node]:
        return {
            "src": Node("dh_table", self.src),
            "result": Node("dh_result", self.result),
            "comp": Node("dh_table", self.comp),
            "vario": Node("object:variogram_model", self.vario),
            "field": self.field(),
            "report": Node("object:resources_report", self.report),
        }

    def control(self) -> dict[str, Node]:
        return {
            "other": Node("dh_table", self.other),
            "other_result": Node("dh_result", self.other_result),
            "trend": Node("object:trend", self.trend),
            "set": Node("dh_set", self.set_id),
        }

    def status(self, *nodes: Node) -> dict[Node, lineage.NodeStatus]:
        with dialect.read_tx(self.engine) as conn:
            return lineage.status(conn, list(nodes))

    def edit_source(self) -> None:
        with dialect.write_tx(self.engine) as conn:
            res.bump_table(conn, self.set_id, self.src)


def build(db_url: str, tmp_path: Path, *, field_reads_data: bool = True) -> Chain:
    engine, set_id, src = setup(db_url, tmp_path)
    comp = dh.write_table(engine, set_id, composites(), job_id=None, tick=noop)["table_id"]
    more = dataclasses.replace(samples("Другая"), kind="interval")  # a second assay table would replace the first
    other = dh.write_table(engine, set_id, more, job_id=None, tick=noop)["table_id"]
    result = add_result(engine, set_id, comp, "length", [(src, ["AU"])])
    other_result = add_result(engine, set_id, None, "conditions", [(other, ["AU"])])
    with dialect.write_tx(engine) as conn:
        found = {t.id: t for t in stats_source.tables(conn, set_id)}[comp]
        vario = insert_object(
            conn, "variogram_model", "Au_D1",
            {
                "source": {"set_id": set_id, "table": str(comp), "field": "AU"},
                "inputs": {"tables": [table_entry(comp, found)]},
                "sha256": "v1", "algo": "vario-1",
            },
        )  # fmt: skip
        lineage.sync(conn, [Node("object:variogram_model", vario)])
        model = insert_object(
            conn, "block_model", "Модель",
            {"fields": [{"name": "AU_OK", "file": "fld:aa", "source": {"op": "estimate", "at": "t1"}}], "inputs": None},
        )  # fmt: skip
        inputs = [Node("object:variogram_model", vario)] + ([Node("dh_table", comp)] if field_reads_data else [])
        lineage.record(conn, Node("bm_field", model, "au_ok"), inputs)
        report = insert_object(conn, "resources_report", "Отчёт по ресурсам", {"algo": "res-1"})
        lineage.record(conn, Node("object:resources_report", report), [Node("bm_field", model, "au_ok")])
        trend = insert_object(conn, "trend", "Тренд", {})
    return Chain(engine=engine, set_id=set_id, src=src, comp=comp, result=result, vario=vario, model=model,
                 report=report, trend=trend, other=other, other_result=other_result)  # fmt: skip


def states(got: dict[Node, lineage.NodeStatus]) -> dict[str, str]:
    return {f"{n.kind}:{n.id}": s.state for n, s in got.items()}


def test_fresh_chain_is_current(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    got = c.status(*c.nodes().values(), *c.control().values())
    assert {s.state for s in got.values()} == {lineage.CURRENT}, states(got)


def test_td35_1_one_edit_makes_the_whole_chain_stale_and_names_the_table(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    c.edit_source()
    nodes = c.nodes()
    got = c.status(*nodes.values(), *c.control().values())
    for name in ("result", "comp", "vario", "field", "report"):
        s = got[nodes[name]]
        assert s.state == lineage.STALE, (name, s)
        assert "«Пробы»" in (s.reason or ""), (name, s.reason)
    assert got[nodes["src"]].state == lineage.CURRENT  # the table itself is the source: it is not «устарел»
    assert all(got[n].state == lineage.CURRENT for n in c.control().values())
    # the chain is told from the report down to the table
    reason = got[nodes["report"]].reason or ""
    assert reason.index("поле блочной модели") < reason.index("модель вариограммы «Au_D1»") < reason.index("«Пробы»")
    assert reason.endswith("изменена таблица «Пробы»")


def test_td35_1_direct_checks_stay_as_they_were(db_url: str, tmp_path: Path) -> None:
    """The adapters call the direct checks of the modules: the list of results says what it said (ТД35-7)."""
    c = build(db_url, tmp_path)
    c.edit_source()
    with dialect.read_tx(c.engine) as conn:
        listed = {r["id"]: r for r in res.list_results(conn, c.set_id)}
    assert listed[c.result]["stale"] and listed[c.result]["reason"] == "изменена таблица «Пробы»"
    assert not listed[c.other_result]["stale"]
    assert c.status(Node("dh_result", c.result))[Node("dh_result", c.result)].reason == "изменена таблица «Пробы»"


def test_td35_2_same_state_again_is_current(db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wireframe edited and edited back (the same `cache_key`) leaves everything built on it «актуален»."""
    engine, set_id, src = setup(db_url, tmp_path)
    keys = {"key": "k1"}
    monkeypatch.setattr(
        "bf_core.viz.meshes.mesh_state",
        lambda _conn, _wf: SimpleNamespace(name="Тело", cache_key=lambda: keys["key"]),
    )
    with dialect.write_tx(engine) as conn:
        wf = insert_object(conn, "wireframe", "Тело", {})
        stub = insert_object(conn, "model_stub", "Расчёт по каркасу", {})
        lineage.record(conn, Node("object:model_stub", stub), [Node("wireframe", wf)])
    rid = add_result(engine, set_id, None, "conditions", [(src, ["AU"])])
    with dialect.write_tx(engine) as conn:  # the same result reads the wireframe by name and geometry key
        row = conn.execute(sa.select(dh_results.c.inputs).where(dh_results.c.id == rid)).scalar_one()
        conn.execute(
            sa.update(dh_results)
            .where(dh_results.c.id == rid)
            .values(inputs={**row, "meshes": [{"name": "Тело", "key": "k1"}]})
        )
        lineage.sync(conn, [Node("dh_result", rid)])
    watched = [Node("object:model_stub", stub), Node("dh_result", rid)]

    def state() -> list[str]:
        with dialect.read_tx(engine) as conn:
            return [s.state for s in lineage.status(conn, watched).values()]

    assert state() == [lineage.CURRENT] * 2
    keys["key"] = "k2"  # the geometry changed
    assert state() == [lineage.STALE] * 2
    keys["key"] = "k1"  # edited back: the same key
    assert state() == [lineage.CURRENT] * 2


def test_td35_3_deleted_table_says_source_lost_with_its_name(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    with dialect.write_tx(c.engine) as conn:
        stub = insert_object(conn, "model_stub", "Чтение таблицы", {})
        lineage.record(conn, Node("object:model_stub", stub), [Node("dh_table", c.src)])
        conn.execute(sa.delete(dh_tables).where(dh_tables.c.id == c.src))
    nodes = c.nodes()
    got = c.status(*nodes.values(), Node("object:model_stub", stub))
    assert got[nodes["src"]].state == lineage.DELETED
    for name in ("result", "comp", "vario", "field", "report"):
        assert got[nodes[name]].state == lineage.LOST, (name, got[nodes[name]])
        assert "«Пробы»" in (got[nodes[name]].reason or "")
    assert got[Node("object:model_stub", stub)].reason == "источник удалён: таблица «Пробы»"  # the name from the row


def test_td35_4_pin_holds_the_model_and_its_dependents(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path, field_reads_data=False)  # the field reads the model only
    c.edit_source()
    nodes = c.nodes()
    with dialect.write_tx(c.engine) as conn:
        pinned = lineage.pin(conn, nodes["vario"], True)
    assert pinned.state == lineage.PINNED and pinned.changed and "«Пробы»" in (pinned.reason or "")
    got = c.status(nodes["vario"], nodes["field"], nodes["report"], nodes["comp"])
    assert got[nodes["vario"]].state == lineage.PINNED
    assert got[nodes["field"]].state == lineage.CURRENT and got[nodes["report"]].state == lineage.CURRENT
    assert got[nodes["comp"]].state == lineage.STALE  # what is not pinned is not held
    with dialect.write_tx(c.engine) as conn:
        lineage.pin(conn, nodes["vario"], False)
    got = c.status(nodes["vario"], nodes["field"], nodes["report"])
    assert {s.state for s in got.values()} == {lineage.STALE}


def test_pin_of_a_missing_node_is_refused(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    with pytest.raises(ApiError) as e, dialect.write_tx(c.engine) as conn:
        lineage.pin(conn, Node("object:variogram_model", 999_999), True)
    assert e.value.code == "lineage_node_not_found"


def test_changed_fingerprint_of_a_recorded_input_is_stale(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path, field_reads_data=False)
    with dialect.write_tx(c.engine) as conn:
        touch(conn, c.trend)
        stub = insert_object(conn, "model_stub", "Оценка с трендом", {})
        # recorded before the change: the fingerprint of the trend is version 2 now, the row keeps what it read
        lineage.record(conn, Node("object:model_stub", stub), [Node("object:trend", c.trend)])
    with dialect.write_tx(c.engine) as conn:
        touch(conn, c.trend, angle=30)
    s = c.status(Node("object:model_stub", stub))[Node("object:model_stub", stub)]
    assert s.state == lineage.STALE and s.reason == "изменено: тренд «Тренд»"


def test_impact_lists_everything_built_from_the_table(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    with dialect.read_tx(c.engine) as conn:
        got = lineage.impact(conn, Node("dh_table", c.src))
        none = lineage.impact(conn, Node("dh_table", c.other))
        leaf = lineage.impact(conn, c.nodes()["report"])
    assert got["total"] == 5 and not got["truncated"]
    assert got["by_kind"] == {
        "dh_result": 1,
        "dh_table": 1,
        "object:variogram_model": 1,
        "bm_field": 1,
        "object:resources_report": 1,
    }
    assert [n["depth"] for n in got["nodes"]] == [1, 2, 3, 3, 4]  # the field reads the table and the model
    assert sorted(n["name"] for n in got["nodes"][2:4]) == ["Au_D1", "Модель.AU_OK"]
    assert none["total"] == 1 and leaf["total"] == 0  # the control branch: its own result only


def test_impact_skips_rows_of_nodes_that_are_gone(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    with dialect.write_tx(c.engine) as conn:
        conn.execute(sa.delete(objects).where(objects.c.id == c.report))  # deleted without `forget`: a row stays
    with dialect.read_tx(c.engine) as conn:
        assert lineage.impact(conn, Node("dh_table", c.src))["total"] == 4
    with dialect.write_tx(c.engine) as conn:
        assert lineage.sweep(conn) == 1


def test_deleting_a_result_and_a_model_takes_their_rows(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    with dialect.write_tx(c.engine) as conn:
        res.delete_result(conn, c.result)
        from bf_core.geostat.variogram import store as vs

        vs.delete(conn, c.vario)
    with dialect.read_tx(c.engine) as conn:
        left = {(r.dst_kind, r.dst_id) for r in conn.execute(sa.select(lineage_table))}
        assert ("dh_result", c.result) not in left and ("dh_table", c.comp) not in left
        assert ("object:variogram_model", c.vario) not in left
    got = c.status(c.nodes()["field"])[c.field()]
    assert got.state == lineage.LOST and "«Au_D1»" in (got.reason or "")  # built from it: «источник удалён»


def test_cycle_is_an_error(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    with dialect.write_tx(c.engine) as conn:
        a = Node("object:model_stub", insert_object(conn, "model_stub", "A", {}))
        b = Node("object:model_stub", insert_object(conn, "model_stub", "B", {}))
        lineage.record(conn, a, [b])
        lineage.record(conn, b, [a])
    with pytest.raises(ApiError) as e, dialect.read_tx(c.engine) as conn:
        lineage.status(conn, [a])
    assert e.value.code == "lineage_cycle"


def test_record_replaces_inputs_and_refuses_a_missing_one(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    dst = Node("bm_field", c.model, "au_ok")
    with dialect.write_tx(c.engine) as conn:
        refs = lineage.record(conn, dst, [Node("object:trend", c.trend)])
        assert [r.name for r in refs] == ["Тренд"] and refs[0].fp == "1"
    with dialect.read_tx(c.engine) as conn:
        rows = conn.execute(sa.select(lineage_table.c.src_kind).where(lineage_table.c.dst_kind == "bm_field")).all()
    assert [r.src_kind for r in rows] == ["object:trend"]
    with pytest.raises(ApiError) as e, dialect.write_tx(c.engine) as conn:
        lineage.record(conn, dst, [Node("object:trend", 424_242)])
    assert e.value.code == "lineage_node_not_found"
    with pytest.raises(ApiError) as e2:
        lineage.providers.provider_for("table")
    assert e2.value.code == "lineage_bad_kind"


def test_origin_lists_inputs_with_their_fingerprints(db_url: str, tmp_path: Path) -> None:
    c = build(db_url, tmp_path)
    c.edit_source()
    with dialect.read_tx(c.engine) as conn:
        rows = lineage.origin(conn, c.field())
    assert [(r["kind"], r["name"], r["state"]) for r in rows] == [
        ("object:variogram_model", "Au_D1", "stale"), ("dh_table", "Композиты 2 м", "stale")
    ]  # fmt: skip
    assert all(r["fp"] is not None and r["fp_now"] is not None for r in rows)


# --- a project made before the graph, and the migration ------------------------------------------------------------


def test_project_without_lineage_opens_and_is_judged_by_direct_checks(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project", "0007")
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
    src = dh.write_table(engine, set_id, samples(), job_id=None, tick=noop)["table_id"]
    comp = dh.write_table(engine, set_id, composites(), job_id=None, tick=noop)["table_id"]
    # a result written by the code of Э12–Э17: no rows in `lineage` (the table is not there at all)
    with dialect.write_tx(engine) as conn:
        conn.execute(
            sa.insert(dh_results).values(
                set_id=set_id, table_id=comp, op="length", name="Композиты по длине", params={}, sha256="0" * 64,
                algo="length-1", report={}, created_at=dialect.utcnow(),
                inputs=res.make_inputs(conn, set_id, [(src, ["AU"])]),
            )
        )  # fmt: skip
    migrate.upgrade(engine, "project")
    assert migrate.state(engine, "project") == "current"
    nodes = [Node("dh_table", comp), Node("dh_table", src)]
    with dialect.read_tx(engine) as conn:
        assert {s.state for s in lineage.status(conn, nodes).values()} == {lineage.CURRENT}
    with dialect.write_tx(engine) as conn:
        res.bump_table(conn, set_id, src)
    with dialect.read_tx(engine) as conn:
        got = lineage.status(conn, nodes)
    assert got[nodes[0]].state == lineage.STALE and got[nodes[1]].state == lineage.CURRENT
    with dialect.write_tx(engine) as conn:  # the reverse index of an old project: built when first needed
        assert lineage.reindex(conn) >= 2
    with dialect.read_tx(engine) as conn:
        assert lineage.impact(conn, Node("dh_table", src))["total"] == 2  # the result and its table


def test_migration_0008_cycle(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        lineage.record(conn, Node("object:x", 1), [])
        lineage.store.set_pin(conn, Node("object:x", 1), True)
    migrate.downgrade(engine, "project", "0007")
    with engine.connect() as conn:
        assert "lineage" not in sa.inspect(conn).get_table_names()
    migrate.upgrade(engine, "project")
    assert migrate.state(engine, "project") == "current"


# --- .bfggis there and back (ТД35-6) ---------------------------------------------------------------------------------


@pytest.fixture()
def ac(pclient):  # type: ignore[no-untyped-def]
    pclient.post(API + "/projects", json={"name": "Исходный", "description": {}})
    return pclient


def test_td35_6_bfggis_roundtrip_keeps_states_chains_and_pins(ac, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    p = projects_of(ac.core).require_current()
    engine = p.engine
    with dialect.write_tx(engine) as conn:
        set_id = dh.create_set(conn, "Скважины")
    src = dh.write_table(engine, set_id, samples(), job_id=None, tick=noop)["table_id"]
    comp = dh.write_table(engine, set_id, composites(), job_id=None, tick=noop)["table_id"]
    # padding: ids of the archive differ from the new ones
    with dialect.write_tx(engine) as conn:
        for i in range(3):
            insert_object(conn, "folder", f"Папка {i}", {})
    add_result(engine, set_id, comp, "length", [(src, ["AU"])])
    with dialect.write_tx(engine) as conn:
        found = {t.id: t for t in stats_source.tables(conn, set_id)}[comp]
        vario = insert_object(
            conn, "variogram_model", "Au_D1",
            {"source": {"set_id": set_id, "table": str(comp)}, "inputs": {"tables": [table_entry(comp, found)]},
             "sha256": "v1"},
        )  # fmt: skip
        lineage.sync(conn, [Node("object:variogram_model", vario)])
        field = {"name": "AU_OK", "file": "fld:aa", "source": {"op": "estimate", "at": "t"}}
        model = insert_object(conn, "block_model", "Модель", {"fields": [field]})
        lineage.record(conn, Node("bm_field", model, "au_ok"), [Node("object:variogram_model", vario)])
        report = insert_object(conn, "resources_report", "Отчёт", {})
        lineage.record(conn, Node("object:resources_report", report), [Node("bm_field", model, "au_ok")])
        pinned = insert_object(conn, "variogram_model", "Закреплённая", {"sha256": "p"})
        lineage.pin(conn, Node("object:variogram_model", pinned), True)
        res.bump_table(conn, set_id, src)  # everything built on the table is «устарел» now

    def snapshot(eng: sa.Engine) -> dict[str, tuple[str, str | None]]:
        with dialect.read_tx(eng) as conn:
            by_name = {r.name: int(r.id) for r in conn.execute(sa.select(objects.c.id, objects.c.name))}
            tid = {r.name: int(r.id) for r in conn.execute(sa.select(dh_tables.c.id, dh_tables.c.name))}
            rid = int(conn.execute(sa.select(dh_results.c.id)).scalar_one())
            ask = {
                "таблица": Node("dh_table", tid["Пробы"]), "композиты": Node("dh_table", tid["Композиты 2 м"]),
                "результат": Node("dh_result", rid), "модель": Node("object:variogram_model", by_name["Au_D1"]),
                "поле": Node("bm_field", by_name["Модель"], "au_ok"),
                "отчёт": Node("object:resources_report", by_name["Отчёт"]),
                "закреплена": Node("object:variogram_model", by_name["Закреплённая"]),
            }  # fmt: skip
            got = lineage.status(conn, list(ask.values()))
            return {k: (got[n].state, got[n].reason) for k, n in ask.items()}

    before = snapshot(engine)
    assert before["отчёт"][0] == lineage.STALE and before["закреплена"][0] == lineage.PINNED
    job = export(ac, tmp_path / "chain.bfggis")
    r = ac.post(f"{API}/projects/import", json={"path": str(job["result"]["path"]), "mode": "copy"}).json()
    done = wait_job(ac, r["source"], r["id"])
    assert done["status"] == "done", done
    ac.post(f"{API}/projects/{done['result']['uid']}/open")
    after = snapshot(projects_of(ac.core).require_current().engine)
    assert after == before  # the same states, the same chains of reasons, the pin
    with dialect.read_tx(projects_of(ac.core).require_current().engine) as conn:
        ids = {int(i) for i in conn.execute(sa.select(objects.c.id)).scalars()}
        rows = list(conn.execute(sa.select(lineage_table)))
    assert rows and all(r.dst_id in ids | {int(x) for x in range(1, 50)} for r in rows)


# --- the speed of the states ----------------------------------------------------------------------------------------


def test_status_of_2000_nodes_depth_6_is_fast(db_url: str, tmp_path: Path) -> None:
    """ADR 0025: 2000 nodes of depth 6 ≤ 50 ms (the scenario of bench — 18.Г2). Here with a margin for a loaded CI
    machine; the measured time is printed."""
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    levels, width = 6, 334
    with dialect.write_tx(engine) as conn:
        ids: list[list[int]] = []
        for lv in range(levels):
            ids.append([insert_object(conn, "model_stub", f"N{lv}-{i}", {}) for i in range(width)])
        edges = 0
        for lv in range(1, levels):
            for i, oid in enumerate(ids[lv]):
                srcs = [ids[lv - 1][i], ids[lv - 1][(i + 1) % width]]
                lineage.record(conn, Node("object:model_stub", oid), [Node("object:model_stub", s) for s in srcs])
                edges += len(srcs)
    nodes = [Node("object:model_stub", i) for level in ids for i in level]
    assert len(nodes) >= 2000
    with dialect.read_tx(engine) as conn:
        lineage.status(conn, nodes[:10])  # warm: the first call imports and compiles statements
        t0 = time.perf_counter()
        got = lineage.status(conn, nodes)
        ms = (time.perf_counter() - t0) * 1000
    print(f"lineage.status: {len(nodes)} nodes, depth {levels}, {edges} edges: {ms:.1f} ms")
    assert {s.state for s in got.values()} == {lineage.CURRENT}
    assert ms < 400
