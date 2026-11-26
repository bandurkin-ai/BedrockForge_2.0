"""The reference of the templates of the resource report (подшаг 20.4; ТЗ 8.10 «Формы»; ADR 0027 decisions 17–19):
JORC and ГКЗ are always there and are not deleted, own templates are made / rewritten / deleted with an optimistic
version (409 `version_conflict`), the body is checked (422 `bad_param {param}`)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import settings
from bf_core.resources import templates as tp


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[sa.Engine]:
    eng = dialect.create_engine(dialect.sqlite_url(str(tmp_path / "p.db")))
    with eng.begin() as conn:
        settings.create(conn)
    yield eng
    eng.dispose()


def mine(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"name": "Мой", "labels": ["Руда 1", "Руда 2", "Руда 3"]}
    body.update(over)
    return body


def err(fn: Any, *args: Any) -> ApiError:
    with pytest.raises(ApiError) as e:
        fn(*args)
    return e.value  # type: ignore[no-any-return]


def stored(engine: sa.Engine) -> Any:
    with engine.connect() as conn:
        return conn.execute(sa.select(settings.c.value, settings.c.version).where(settings.c.scope == tp.SCOPE)).first()


def test_the_box_is_there_in_an_empty_project(engine: sa.Engine) -> None:
    with engine.connect() as conn:
        got = tp.templates(conn)
    assert [t["id"] for t in got] == ["jorc", "gkz"]
    jorc, gkz = got
    assert (jorc["name"], jorc["version"], jorc["balance"], jorc["total"]) == ("JORC", 1, False, "Итого")
    assert jorc["labels"] == ["Измеренные", "Выявленные", "Предполагаемые"]
    assert jorc["subtotals"] == [{"label": "Изм. + Выяв.", "members": ["Измеренные", "Выявленные"]}]
    assert jorc["units"] == {"tonnes": "kt", "volume": "km3", "metal": {"g": "koz", "t": "t"}}
    assert (gkz["name"], gkz["version"], gkz["balance"]) == ("ГКЗ", 1, True)
    assert gkz["labels"] == ["A", "B", "C1", "C2"]
    assert gkz["subtotals"] == [{"label": "A + B + C1", "members": ["A", "B", "C1"]}]
    assert gkz["units"]["metal"] == {"g": "kg", "t": "t"}
    assert stored(engine) is None  # the box is not stored until the first write
    with engine.connect() as conn:
        assert tp.get(conn, "gkz") == gkz


def test_the_box_is_valid_by_its_own_rules() -> None:
    for t in tp.defaults().values():
        clean = tp.check_body(t)
        assert {k: v for k, v in t.items() if k != "version"} == clean


def test_a_new_template_is_made_and_listed_after_the_box(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        made = tp.put(conn, "my", mine())
        tp.put(conn, "alpha", mine(name="Альфа"))
    assert made["id"] == "my" and made["version"] == 1
    assert (made["key"], made["section"], made["total"]) == ("class", None, "Итого")
    assert made["columns"] == ["tonnes", "grade", "metal"]
    assert made["decimals"] == tp.DEFAULT_DECIMALS and made["units"]["metal"] == {"g": "koz", "t": "t"}
    with engine.connect() as conn:
        assert [t["id"] for t in tp.templates(conn)] == ["jorc", "gkz", "alpha", "my"]  # the box, then by name
        assert tp.get(conn, "my") == made
    value, version = stored(engine)
    assert version == 2 and sorted(value["templates"]) == ["alpha", "my"]  # the untouched box is not kept


def test_the_version_is_optimistic(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        tp.put(conn, "my", mine())
    with engine.begin() as conn:
        e = err(tp.put, conn, "my", mine())  # exists: a version is needed
    assert (e.status, e.code, e.extra["version"]) == (409, "version_conflict", 1)
    with engine.begin() as conn:
        again = tp.put(conn, "my", mine(name="Мой 2", expected_version=1))
    assert (again["version"], again["name"]) == (2, "Мой 2")
    with engine.begin() as conn:
        e = err(tp.put, conn, "my", mine(expected_version=1))  # stale
    assert (e.code, e.extra["version"]) == ("version_conflict", 2)
    with engine.begin() as conn:
        e = err(tp.put, conn, "new", mine(expected_version=1))  # a new one has no version
    assert (e.code, e.extra["version"]) == ("version_conflict", 0)


def test_the_box_is_rewritten_at_its_version_and_keeps_its_id(engine: sa.Engine) -> None:
    body = tp.defaults()["jorc"]
    body = {**body, "decimals": {**body["decimals"], "tonnes": 1}, "expected_version": 1}
    with engine.begin() as conn:
        e = err(tp.put, conn, "jorc", {k: v for k, v in body.items() if k != "expected_version"})
        assert e.code == "version_conflict"
        got = tp.put(conn, "jorc", body)
    assert got["version"] == 2 and got["decimals"]["tonnes"] == 1
    with engine.connect() as conn:
        listed = tp.templates(conn)
    assert [t["id"] for t in listed] == ["jorc", "gkz"] and listed[0]["version"] == 2 and listed[1]["version"] == 1
    value, _ = stored(engine)
    assert sorted(value["templates"]) == ["jorc"]  # only the rewritten one is kept


def test_delete(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        tp.put(conn, "my", mine())
        tp.put(conn, "other", mine(name="Другой"))
    with engine.begin() as conn:
        assert err(tp.delete, conn, "my", None).code == "version_conflict"  # the version is needed
    with engine.begin() as conn:
        e = err(tp.delete, conn, "my", 7)
    assert (e.status, e.code, e.extra["version"]) == (409, "version_conflict", 1)
    with engine.begin() as conn:
        assert tp.delete(conn, "my", 1) == {"id": "my", "deleted": True}
    with engine.connect() as conn:
        assert [t["id"] for t in tp.templates(conn)] == ["jorc", "gkz", "other"]
        e = err(tp.get, conn, "my")
    assert (e.status, e.code) == (404, "template_not_found")
    with engine.begin() as conn:
        assert err(tp.delete, conn, "my", 1).code == "template_not_found"
        assert tp.put(conn, "my", mine())["version"] == 1  # the id is free again
    with engine.begin() as conn:
        tp.delete(conn, "my", 1)
        tp.delete(conn, "other", 1)
    value, _ = stored(engine)
    assert value["templates"] == {}


@pytest.mark.parametrize("tid", ["jorc", "gkz"])
def test_the_box_is_not_deleted(engine: sa.Engine, tid: str) -> None:
    with engine.begin() as conn:
        e = err(tp.delete, conn, tid, 1)
    assert (e.status, e.code, e.extra["param"]) == (422, "bad_param", "id")
    assert e.message and e.hint


def test_an_unknown_template_and_a_bad_id(engine: sa.Engine) -> None:
    with engine.connect() as conn:
        e = err(tp.get, conn, "nope")
    assert (e.status, e.code, e.extra["id"]) == (404, "template_not_found", "nope")
    with engine.begin() as conn:
        assert err(tp.put, conn, "плохой код", mine()).extra["param"] == "id"
        assert err(tp.put, conn, "my", "text").extra["param"] == "body"


def test_subtotals_follow_their_last_member_and_members_are_matched_by_label(engine: sa.Engine) -> None:
    body = mine(subtotals=[{"label": "Руды 1+2", "members": ["руда  1", "Руда 2", "РУДА 1"]}])
    with engine.begin() as conn:
        made = tp.put(conn, "my", body)
    assert made["subtotals"] == [{"label": "Руды 1+2", "members": ["Руда 1", "Руда 2"]}]  # spelled as in `labels`


@pytest.mark.parametrize(
    ("over", "param"),
    [
        ({"name": ""}, "name"),
        ({"name": "x" * 101}, "name"),
        ({"labels": []}, "labels"),
        ({"labels": "A"}, "labels"),
        ({"labels": ["A", " a "]}, "labels.1"),
        ({"labels": ["A", ""]}, "labels.1"),
        ({"labels": [f"L{i}" for i in range(65)]}, "labels"),
        ({"labels": ["Прочие"]}, "labels.0"),
        ({"subtotals": "x"}, "subtotals"),
        ({"subtotals": [{"label": "S", "members": ["Руда 1"]}]}, "subtotals.0.members"),
        ({"subtotals": [{"label": "S", "members": ["Руда 1", "нет такой"]}]}, "subtotals.0.members.1"),
        ({"subtotals": [{"label": "Руда 1", "members": ["Руда 1", "Руда 2"]}]}, "subtotals.0.label"),
        ({"subtotals": [{"label": "S", "members": "Руда 1"}]}, "subtotals.0.members"),
        ({"total": "Руда 2"}, "total"),
        ({"total": ""}, "total"),
        ({"key": "Класс"}, "key"),
        ({"section": "class"}, "section"),
        ({"columns": []}, "columns"),
        ({"columns": ["tonnes", "tonnes"]}, "columns"),
        ({"columns": ["tonnes", "cost"]}, "columns"),
        ({"balance": "yes"}, "balance"),
        ({"units": {"tonnes": "lb"}}, "units.tonnes"),
        ({"units": {"volume": "ft3"}}, "units.volume"),
        ({"units": {"metal": "oz"}}, "units.metal.g"),
        ({"units": {"metal": {"g": "koz", "t": "lb"}}}, "units.metal.t"),
        ({"units": {"metal": 5}}, "units.metal"),
        ({"units": "kt"}, "units"),
        ({"decimals": {"tonnes": 7}}, "decimals.tonnes"),
        ({"decimals": {"grade": 1.5}}, "decimals.grade"),
        ({"decimals": {"metal": True}}, "decimals.metal"),
        ({"expected_version": 0}, "expected_version"),
    ],
)
def test_a_bad_body_is_422_with_the_param_in_russian(engine: sa.Engine, over: dict[str, Any], param: str) -> None:
    with engine.begin() as conn:
        e = err(tp.put, conn, "my", mine(**over))
    assert (e.status, e.code, e.extra["param"]) == (422, "bad_param", param)
    assert e.message and e.hint and any("а" <= c <= "я" for c in e.message.lower())  # a Russian text
    assert stored(engine) is None  # nothing was written


def test_total_may_be_off_and_a_text_metal_unit_is_for_both_bases(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        body = mine(total=None, units={"metal": "kg"}, section="domain", columns=["volume", "tonnes"])
        made = tp.put(conn, "my", body)
    assert made["total"] is None and made["units"]["metal"] == {"g": "kg", "t": "kg"}
    assert (made["section"], made["columns"]) == ("domain", ["volume", "tonnes"])


def test_a_stored_template_that_no_longer_passes_the_check_is_left_out(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        tp.put(conn, "my", mine())
        broken = {"v": 1, "templates": {"bad": {"name": "Плохой", "labels": []}, "jorc": {"labels": "x"}, "z": 5}}
        conn.execute(sa.update(settings).where(settings.c.scope == tp.SCOPE).values(value=broken))
    with engine.connect() as conn:
        got = tp.templates(conn)
    assert [t["id"] for t in got] == ["jorc", "gkz"] and got[0]["name"] == "JORC"  # the default stays


def test_the_units_of_the_metal_follow_the_troy_ounce() -> None:
    assert 31250 * tp.METAL_UNITS["koz"][1] == pytest.approx(1.004711, abs=1e-6)  # Т8.10-1: 31.25 kg
    assert tp.METAL_UNITS["kg"][1] == 1e-3 and tp.METAL_UNITS["t"][1] == 1e-6
    assert tp.TONNES_UNITS["kt"][0] == "тыс. т" and tp.VOLUME_UNITS["km3"][0] == "тыс. м³"
