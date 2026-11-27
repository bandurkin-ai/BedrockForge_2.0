"""Read tools of the assistant `data_tree`, `data_summary`, `data_history` and the common call of `data_tools.py`
(подшаг 23.7; e23-api.md §5.2; ТЗ 7.2, 7.32; ADR 0029 решения 18-19) on the demo project."""

from __future__ import annotations

import json
import math
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from bf_core.api.app import CoreState, create_app
from bf_core.assistant import data_project as dp
from bf_core.assistant import data_tools as dt
from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_rows, dh_tables, edit_log, objects
from bf_core.projects.service import projects_of
from tests.conftest import TOKEN

CONTRACT = Path(__file__).parent / "data" / "assistant" / "contract_answers.json"
SET_ID = 1  # «Скважины 2025» of the demo project


def extend_demo(project: Any) -> None:
    """What the demo lacks for the tools: an interval table with rows and a saved chart."""
    now = dialect.utcnow()
    with dialect.write_tx(project.engine) as conn:
        holes = [int(h) for h in conn.execute(sa.select(dh_holes.c.id).order_by(dh_holes.c.id).limit(5)).scalars()]
        res = conn.execute(
            sa.insert(dh_tables).values(
                set_id=SET_ID,
                name="Литология",
                name_key=dialect.name_key("Литология"),
                kind="interval",
                row_count=len(holes) * 2,
                rev=1,
                state="ready",
                created_at=now,
                updated_at=now,
            )  # fmt: skip
        )
        table_id = int(res.inserted_primary_key[0])  # type: ignore[index]
        conn.execute(
            sa.insert(dh_columns).values(
                table_id=table_id,
                name="Литология",
                kind="text",
                unit=None,
                slot=0,
                role="value",
                deleted=False,
                position=0,
            )  # fmt: skip
        )
        for h in holes:
            for i, (a, b) in enumerate([(0.0, 10.0), (10.0, 25.0)]):
                conn.execute(
                    sa.insert(dh_rows).values(
                        table_id=table_id, hole_id=h, from_m=a, to_m=b, txt={"0": "гранит" if i else "сланец"}
                    )
                )
        conn.execute(
            sa.insert(objects).values(
                kind="chart",
                name="Графики скважин",
                name_key=dialect.name_key("Графики скважин"),
                sort_key=0.0,
                state="ready",
                version=1,
                meta={},
                created_at=now,
                updated_at=now,
            )  # fmt: skip
        )


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    """The open demo project (shared by the tests of the module; they only read it)."""
    base = tmp_path_factory.mktemp("assistant_data")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BF_LOCAL_DIR", str(base / "local"))
        st = CoreState(token=TOKEN, projects_dir=str(base / "Проекты"), data_dir=str(base / "roaming"))
        with TestClient(create_app(st), base_url="http://127.0.0.1:5555", headers={"X-BF-Token": TOKEN}) as c:
            assert c.post("/api/v1/projects/demo").status_code == 200
            project = projects_of(st).require_current()
            extend_demo(project)
            yield project


@pytest.fixture()
def fresh(pclient):  # type: ignore[no-untyped-def]
    """A demo project of its own for a test that writes to it."""
    assert pclient.post("/api/v1/projects/demo").status_code == 200
    project = projects_of(pclient.core).require_current()
    extend_demo(project)
    return project


def call(project: Any, name: str, args: dict[str, Any] | None = None, set_id: int | None = None) -> Any:
    """`(result, parsed)`: the result of the common call and its JSON (None for a refusal)."""
    res = dt.run(name, args or {}, project=project, set_id=set_id)
    return res, (None if res.is_error else json.loads(res.text))


def clean_text(text: str) -> bool:
    """No control, format or line-break characters in the text (an escape `\\u00xx` would show one)."""
    return "\\u" not in text and not any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in text)


# --- the tree ----------------------------------


def test_tree_has_sets_tables_charts_and_interval_tables(demo) -> None:  # type: ignore[no-untyped-def]
    res, tree = call(demo, "data_tree")
    assert not res.is_error and len(res.text) <= 16_000
    nodes = {n["path"]: n for n in tree["nodes"]}
    assert tree["total"] == len(tree["nodes"]) and tree["next_offset"] is None
    s = nodes["Скважины 2025"]
    assert (s["kind"], s["id"], s["count"]) == ("drillhole_set", SET_ID, 49)
    assay = nodes["Скважины 2025/Пробы"]
    assert (assay["kind"], assay["count"]) == ("assay_table", 4036)
    lith = nodes["Скважины 2025/Литология"]
    assert (lith["kind"], lith["count"]) == ("interval_table", 10)
    assert nodes["Графики скважин"]["kind"] == "chart"
    kinds = {n["kind"] for n in tree["nodes"]}
    assert {"wireframe", "line_file", "line_layer", "raster", "block_model", "section_series"} <= kinds
    assert nodes["Контуры по разрезам/Контуры рудного тела 3"]["kind"] == "line_layer"  # layers under their file
    names = [n["path"] for n in tree["nodes"]]
    assert names.index("Скважины 2025") < names.index("Скважины 2025/Пробы") < names.index("Скважины 2025/Литология")
    assert "assistant_dialog" not in kinds and res.summary


def test_tree_pages(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    _, full = call(demo, "data_tree")
    monkeypatch.setattr(dp, "TREE_PAGE", 7)
    seen: list[str] = []
    offset: int | None = 0
    pages = 0
    while offset is not None:
        _, page = call(demo, "data_tree", {"offset": offset})
        assert page["total"] == full["total"] and len(page["nodes"]) <= 7
        seen += [n["path"] for n in page["nodes"]]
        offset, pages = page["next_offset"], pages + 1
    assert seen == [n["path"] for n in full["nodes"]] and pages == math.ceil(full["total"] / 7)


# --- the summary ----------------------------------


def test_summary_of_the_project(demo) -> None:  # type: ignore[no-untyped-def]
    res, s = call(demo, "data_summary")  # no set: the project, even when the screen has an active set
    assert not res.is_error
    assert s["name"] == "Демо: участок Северный" and s["description"]["stage"] == "Разведка"
    assert s["sets"] == [{"id": SET_ID, "name": "Скважины 2025", "holes": 49}]
    c = s["counts"]
    assert (c["sets"], c["holes"], c["assays"], c["wireframes"], c["block_models"]) == (1, 49, 4036, 5, 1)
    assert "chats" not in c
    ext = s["extent"]
    assert len(ext) == 6 and ext[0] <= 4900 and ext[3] >= 5500 and ext[2] is not None


def test_summary_of_a_set(demo) -> None:  # type: ignore[no-untyped-def]
    res, s = call(demo, "data_summary", {"set_id": SET_ID})
    assert not res.is_error and s["id"] == SET_ID and s["holes"] == 49
    assert s["metres"] and s["metres"] > 1000
    by_name = {t["name"]: t for t in s["tables"]}
    assert [t["name"] for t in s["tables"]][:2] == ["Устья", "Инклинометрия"]
    assert by_name["Устья"]["rows"] == 49 and by_name["Инклинометрия"]["rows"] > 48
    assert by_name["Пробы"] == {"name": "Пробы", "kind": "assay", "rows": 4036, "fields": ["AU", "CU", "Номер пробы"]}
    assert by_name["Литология"]["kind"] == "interval" and by_name["Литология"]["rows"] == 10
    assert s["components"] == [{"name": "AU", "unit": "г/т"}, {"name": "CU", "unit": "%"}]
    assert s["audit"] == {"e": 6, "w": 5, "i": 0, "stale": False}  # the demo opens with its audit done
    assert len(s["extent"]) == 6 and all(v is not None for v in s["extent"])


def test_summary_counts_holes_without_depth(fresh) -> None:  # type: ignore[no-untyped-def]
    """The metres are the sum of the known depths: the holes without one are told apart, never counted as 0 m."""
    _, before = call(fresh, "data_summary", {"set_id": SET_ID})
    assert before["holes_without_depth"] == 0
    with dialect.write_tx(fresh.engine) as conn:
        one = sa.update(dh_holes).where(dh_holes.c.set_id == SET_ID, dh_holes.c.name == "DH-012")
        conn.execute(one.values(td=None))
    _, after = call(fresh, "data_summary", {"set_id": SET_ID})
    assert after["holes_without_depth"] == 1 and after["metres"] < before["metres"]


def test_summary_without_the_audit_is_null_not_zero(fresh) -> None:  # type: ignore[no-untyped-def]
    from bf_core.db.schema import audit_runs

    with dialect.write_tx(fresh.engine) as conn:
        conn.execute(sa.delete(audit_runs))
    _, s = call(fresh, "data_summary", {"set_id": SET_ID})
    assert s["audit"] is None


# --- no project, no set, bad arguments ----------------------------------


def test_no_project_and_no_set_texts(demo) -> None:  # type: ignore[no-untyped-def]
    res = dt.run("data_tree", {}, project=None)
    assert res.is_error and res.text == "Проект не открыт"
    res = dt.run("data_holes", {}, project=demo)  # no set_id and no active set in the context
    assert res.is_error and res.text == "Нет активного набора скважин: укажите set_id"
    assert call(demo, "data_hole", {"hole_id": "DH-001"})[0].text == dt.NO_SET
    res, _ = call(demo, "data_summary", {"set_id": 999})
    assert res.is_error and "999" in res.text and "не найден" in res.text
    _, holes = call(demo, "data_holes", {"limit": 1}, set_id=SET_ID)  # the active set of the context
    assert holes["set_id"] == SET_ID and holes["total"] == 49


def test_arguments_are_checked_before_the_call(demo) -> None:  # type: ignore[no-untyped-def]
    for name, args, part in [
        ("data_tree", {"offset": "5"}, "типа integer"),
        ("data_tree", {"bogus": 1}, "Неизвестный параметр"),
        ("data_hole", {}, "Не указан параметр «hole_id»"),
        ("data_holes", {"with_findings": "yes"}, "типа boolean"),
        ("data_holes", {"limit": True}, "типа integer"),
        ("data_nothing", {}, "Неизвестный инструмент"),
    ]:
        res = dt.run(name, args, project=demo, set_id=SET_ID)
        assert res.is_error and part in res.text, (name, args, res.text)
    assert dt.run("data_tree", None, project=demo).is_error is False  # no arguments at all
    assert dt.run("data_tree", [1], project=demo).is_error is True


# --- history ----------------------------------


def test_history_of_a_set_and_a_wireframe(fresh) -> None:  # type: ignore[no-untyped-def]
    from bf_core import lineage

    wf = 9  # «Рудное тело 1»
    with dialect.write_tx(fresh.engine) as conn:
        lineage.record(conn, lineage.Node("wireframe", wf), [lineage.Node("dh_set", SET_ID)])
        conn.execute(
            sa.insert(edit_log).values(
                scope_kind="object",
                scope_id=SET_ID,
                group_id=1,
                action="update",
                row_ref={"table": "1", "row": 5, "hole": "DH-001"},
                changes={"AU": [1.0, 2.0]},
                undone=False,
                author="user",
                created_at=dialect.utcnow(),
            )  # fmt: skip
        )
    res, h = call(fresh, "data_history", {"kind": "набор", "id": SET_ID})
    assert not res.is_error and h["object"]["name"] == "Скважины 2025" and h["object"]["kind"] == "drillhole_set"
    assert h["object"]["state"] in ("current", "stale", "pinned") and h["origin"] == []
    assert h["used_by"]["total"] >= 1 and h["used_by"]["nodes"][0]["name"] == "Рудное тело 1"
    assert len(h["edits"]) == 1 and h["edits"][0]["action"] == "update"
    assert h["edits"][0]["summary"] == "таблица Пробы, скважина DH-001, AU"
    res, w = call(fresh, "data_history", {"kind": "каркас", "id": wf})
    assert not res.is_error and w["object"]["name"] == "Рудное тело 1"
    assert [(o["kind"], o["name"]) for o in w["origin"]] == [("dh_set", "Скважины 2025")]
    assert w["edits"] == [] and w["versions"] == [] and w["used_by"]["total"] == 0
    _, same = call(fresh, "data_history", {"kind": "wireframe", "id": wf})
    assert same["origin"] == w["origin"]  # the machine kind is the same as the word


def test_history_of_a_table_and_a_layer_and_missing(demo) -> None:  # type: ignore[no-untyped-def]
    with dialect.read_tx(demo.engine) as conn:
        table_id = int(conn.execute(sa.select(dh_tables.c.id).where(dh_tables.c.name == "Пробы")).scalar_one())
    res, t = call(demo, "data_history", {"kind": "dh_table", "id": table_id})
    assert not res.is_error and t["object"]["name"] == "Пробы" and t["edits"] == []
    res, layer = call(demo, "data_history", {"kind": "линия", "id": 3})  # «Рудное тело»
    assert not res.is_error and layer["object"]["kind"] == "line_layer"
    assert layer["versions"] and layer["versions"][0]["current"] is True
    res, _ = call(demo, "data_history", {"kind": "каркас", "id": 3})  # id 3 is a layer, not a wireframe
    assert res.is_error and "не найден" in res.text
    res, _ = call(demo, "data_history", {"kind": "что-то", "id": 1})
    assert res.is_error and "data_tree" in res.text


# --- the common call ----------------------------------


def _tool(monkeypatch: pytest.MonkeyPatch, name: str, fn: Any) -> None:
    """A tool of a test in the registry for the length of the test."""
    schema = {"type": "object", "properties": {"offset": {"type": "integer"}}, "required": [],
              "additionalProperties": False}  # fmt: skip
    monkeypatch.setitem(dt.DATA_TOOLS, name, dt.DataTool(name, "Тест", "тест", schema, fn))


def test_answer_is_cut_to_16000_with_next_offset(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    rows = [{"hole_id": f"DH-{i:05d}", "note": "x" * 150, "value": i / 7} for i in range(2000)]

    def big(call: dt.DataCall, args: dict[str, Any]) -> dict[str, Any]:
        off = dt.offset_of(args)
        return {"total": len(rows), "rows": rows[off:], "next_offset": None}

    _tool(monkeypatch, "data_big", big)
    seen = 0
    offset = 0
    for _ in range(50):
        res = dt.run("data_big", {"offset": offset}, project=demo)
        assert not res.is_error and len(res.text) <= 16_000
        page = json.loads(res.text)
        assert [r["hole_id"] for r in page["rows"][:1]] == [f"DH-{offset:05d}"]
        seen += len(page["rows"])
        if page["next_offset"] is None:
            assert "truncated" not in page
            break
        assert page["truncated"] is True and page["next_offset"] == offset + len(page["rows"])
        offset = page["next_offset"]
    assert seen == 2000 and json.loads(res.text)["rows"][-1]["value"] == round(1999 / 7, 3)  # 6 significant digits


def test_answer_without_a_list_that_is_too_big_is_refused(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    _tool(monkeypatch, "data_blob", lambda call, args: {"a": {f"k{i}": "y" * 150 for i in range(300)}})
    res = dt.run("data_blob", {}, project=demo)
    assert res.is_error and res.text == dt.TOO_BIG


def test_slow_tool_stops_after_the_time_limit(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    def slow(call: dt.DataCall, args: dict[str, Any]) -> dict[str, Any]:
        for _ in range(100):
            time.sleep(0.05)
            call.check()
        return {"done": True}

    _tool(monkeypatch, "data_slow", slow)
    t0 = time.monotonic()
    res = dt.run("data_slow", {}, project=demo, timeout=0.2)
    assert res.is_error and res.text == "Слишком долго: сузьте запрос" and time.monotonic() - t0 < 1.0


def test_same_read_is_not_started_twice_while_the_first_runs(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    started: list[int] = []

    def slow(call: dt.DataCall, args: dict[str, Any]) -> dict[str, Any]:
        started.append(1)
        for _ in range(200):
            time.sleep(0.02)
            call.check()  # the deadline of the first call is over: this stops the thread
        return {"done": True}

    _tool(monkeypatch, "data_dup", slow)
    assert dt.run("data_dup", {}, project=demo, timeout=0.1).text == dt.TOO_LONG
    again = dt.run("data_dup", {}, project=demo, timeout=0.1)  # the thread of the first call may still be there
    assert again.is_error and again.text == dt.STILL_RUNNING
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(t.name == "data-tool-data_dup" for t in threading.enumerate()):
        time.sleep(0.02)  # `check()` ended it: nothing is left running on the project
    assert not any(t.name == "data-tool-data_dup" for t in threading.enumerate())
    assert len(started) == 1  # the second call did not start a thread of its own


def test_a_running_read_blocks_only_the_same_arguments(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    gate = threading.Event()

    def hold(call: dt.DataCall, args: dict[str, Any]) -> dict[str, Any]:
        gate.wait(5)
        return {"n": args.get("offset", 0)}

    _tool(monkeypatch, "data_hold", hold)
    assert dt.run("data_hold", {}, project=demo, timeout=0.05).text == dt.TOO_LONG
    assert dt.run("data_hold", {}, project=demo, timeout=0.05).text == dt.STILL_RUNNING
    gate.set()
    for _ in range(100):  # the first thread is done: the same read starts again
        res = dt.run("data_hold", {}, project=demo, timeout=1)
        if not res.is_error:
            break
        time.sleep(0.02)
    assert not res.is_error and json.loads(res.text) == {"n": 0}


def test_data_stats_has_a_longer_limit_than_the_others() -> None:
    dt.load_all()
    assert dt.DATA_TOOLS["data_stats"].timeout == 30.0
    assert {n: t.timeout for n, t in dt.DATA_TOOLS.items() if n != "data_stats" and n.startswith("data_")} == {
        n: 5.0 for n in dt.DATA_TOOLS if n != "data_stats" and n.startswith("data_")
    }


def test_failure_is_a_result_not_an_exception(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: E501
    def boom(call: dt.DataCall, args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("password=hunter2 in /home/user/secret")

    def refuse(call: dt.DataCall, args: dict[str, Any]) -> dict[str, Any]:
        raise dt.DataError("Нет такой таблицы «\x00Пробы»")

    _tool(monkeypatch, "data_boom", boom)
    _tool(monkeypatch, "data_refuse", refuse)
    res = dt.run("data_boom", {}, project=demo)
    assert res.is_error and "hunter2" not in res.text and "внутренняя ошибка" in res.text  # nothing internal leaks
    res = dt.run("data_refuse", {}, project=demo)
    assert res.is_error and res.text == "Нет такой таблицы «Пробы»"


def test_numbers_strings_and_gaps() -> None:
    assert dt.num(float("nan")) is None and dt.num(float("inf")) is None and dt.num(-math.inf) is None
    assert dt.num(1234.5678) == 1234.57 and dt.num(0.000123456789) == 0.000123457
    assert dt.num(7650123.4) == 7650123 and dt.num(0.0) == 0.0 and dt.num(12) == 12  # the integer part is kept
    assert dt.num(True) is True
    out = dt.sanitize({"a\x00": [float("nan"), 1.23456789, (1, 2), None, "line1\nline2\t‮X​"], "b": {1.5}})
    assert out == {"a": [None, 1.23457, [1, 2], None, "line1 line2 X"], "b": [1.5]}
    assert dt.clean("a" * 500) == "a" * 199 + "…" and len(dt.clean("a" * 500)) == 200
    assert dt.clean("  много   пробелов \r\n тут ") == "много пробелов тут"
    assert dt.clean("\x07\x1b[31mred\x7f﻿\U000e0041") == "[31mred"
    np = pytest.importorskip("numpy")
    assert dt.sanitize({"v": np.float64("nan"), "w": np.float64(2.5), "n": np.int64(3)}) == {
        "v": None,
        "w": 2.5,
        "n": 3,
    }


def test_registry_matches_the_contract_samples(demo) -> None:  # type: ignore[no-untyped-def]
    sample = {t["name"]: t for t in json.loads(CONTRACT.read_text(encoding="utf-8"))["data_tools"]}
    infos = {i["name"]: i for i in dt.infos()}
    mine = {"data_tree", "data_summary", "data_holes", "data_hole", "data_history"}
    assert mine <= set(infos) and set(infos) <= set(sample)
    for name in mine:
        assert infos[name] == sample[name], name
    order = [i["name"] for i in dt.infos()]
    assert order == [n for n in dt.ORDER if n in order]
    defs = {d["name"]: d for d in dt.definitions()}
    assert all(d["description"] and d["input_schema"]["additionalProperties"] is False for d in defs.values())
    assert [d["name"] for d in dt.definitions()] == order


def test_register_refuses_a_bad_tool() -> None:
    ok = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    with pytest.raises(ValueError):
        dt.register("tree", ok, lambda c, a: {}, title="x")
    with pytest.raises(ValueError):
        dt.register("data_x", {"type": "object", "properties": {}}, lambda c, a: {}, title="x")
    assert "tree" not in dt.DATA_TOOLS and "data_x" not in dt.DATA_TOOLS


ANSWERS = [
    ("data_tree", {}),
    ("data_summary", {}),
    ("data_summary", {"set_id": SET_ID}),
    ("data_holes", {"set_id": SET_ID, "limit": 200}),
    ("data_hole", {"hole_id": "DH-012", "set_id": SET_ID}),
    ("data_history", {"kind": "набор", "id": SET_ID}),
]


@pytest.mark.parametrize(("name", "args"), ANSWERS)
def test_the_answers_of_the_demo_fit_16000(demo, name: str, args: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    res = dt.run(name, args, project=demo)
    assert not res.is_error and len(res.text) <= 16_000 and clean_text(res.text)
