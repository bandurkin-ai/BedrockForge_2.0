"""`GET /intercepts/codes` (подшаг 15.20, ревью интерфейса): every distinct code of a domain field — not the 10 most
frequent of `stats.top` — written as the orientation writes a domain code, at most 1 000 with `total`/`truncated`;
404 in Russian."""

from __future__ import annotations

from typing import Any

import pytest

from bf_core.drillholes import store as dh
from bf_core.intercepts import routes_codes as rc
from bf_core.projects.service import projects_of
from tests.test_intercepts_results import API, IC, cc, noop  # noqa: F401 — fixture


def _set(c, holes: list[str], cols: list[tuple[str, str]], values: list[list[Any]]) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    p = projects_of(c.core).require_current()
    set_id = c.post(API + "/drillholes/sets", json={"name": "Скважины"}).json()["id"]
    n = len(holes)
    data = dh.TableData(name="Литология", kind="interval", holes=holes, from_m=[float(i) for i in range(n)],
                        to_m=[float(i + 1) for i in range(n)],
                        columns=[dh.ColumnSpec(nm, k, None, "value") for nm, k in cols], values=values)  # fmt: skip
    tid = dh.write_table(p.engine, set_id, data, job_id=None, tick=noop)["table_id"]
    fields = c.get(f"{API}/drillholes/sets/{set_id}/tables/{tid}", params={"limit": 1}).json()["columns"]
    return {"set_id": set_id, "table": str(tid), "keys": {f["name"]: f["key"] for f in fields}}


def _codes(c, f: dict[str, Any], column: str, **kw: Any):  # type: ignore[no-untyped-def]
    return c.get(IC + "/codes", params={"set_id": f["set_id"], "table": f["table"], "column": column, **kw})


def test_all_codes_not_only_the_top_ten(cc) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    names = [f"Д{i:02d}" for i in range(15)]
    texts = [*names, " Д03 ", "", None, "Д00"]
    nums = [1.0, -0.0, 0.0, 2.5, None, 1e20, 0.1 + 0.2, 3.0, 1.0, 1.0, 2.0, 2.0, 2.0, 7.0, 7.0, 7.0, 8.0, 9.0, 9.0]
    f = _set(cc, ["A"] * len(texts), [("DOM", "text"), ("N", "number")], [texts, nums])
    r = _codes(cc, f, f["keys"]["DOM"])
    assert r.status_code == 200, r.text
    assert r.json() == {"codes": names, "total": 15, "truncated": False}  # > 10 codes, trimmed, empty dropped
    by_name = _codes(cc, f, "dom")  # the name of the field, case aside
    assert by_name.status_code == 200 and by_name.json()["total"] == 15
    got = _codes(cc, f, f["keys"]["N"]).json()
    want = sorted({0.0, 1.0, 2.0, 2.5, 3.0, 7.0, 8.0, 9.0, 1e20, 0.1 + 0.2})  # by value; −0 is 0
    assert got["codes"] == [format(v, ".15g") for v in want]
    assert "-0" not in got["codes"] and got["total"] == 10


def test_truncated(cc, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    monkeypatch.setattr(rc, "CODES_MAX", 3)
    f = _set(cc, ["A"] * 5, [("DOM", "text")], [["e", "d", "c", "b", "a"]])
    assert _codes(cc, f, "DOM").json() == {"codes": ["a", "b", "c"], "total": 5, "truncated": True}


CASES = [("set", "set_not_found"), ("table", "table_not_found"), ("table²", "table_not_found"),
         ("column", "column_not_found")]  # fmt: skip


@pytest.mark.parametrize(("where", "code"), CASES)
def test_not_found(cc, where: str, code: str) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    f = _set(cc, ["A"], [("DOM", "text")], [["a"]])
    if where == "set":
        f = {**f, "set_id": 999_999}
    elif where == "table":
        f = {**f, "table": "999999"}
    elif where == "table²":
        f = {**f, "table": "²"}
    r = _codes(cc, f, "NOPE" if where == "column" else "DOM")
    assert r.status_code == 404, r.text
    out = r.json()
    assert out["code"] == code and out["message"] and out["hint"]
    assert all(ord(ch) < 128 for ch in out["code"]) and any("а" <= ch <= "я" for ch in out["message"].lower())


def test_distinct_codes_unit() -> None:
    assert rc.distinct_codes([-0.0, float("nan"), 0.0, 1.5], "number") == ["0", "1.5"]
    assert rc.distinct_codes([" b", "a ", None, "  ", "b"], "text") == ["a", "b"]
