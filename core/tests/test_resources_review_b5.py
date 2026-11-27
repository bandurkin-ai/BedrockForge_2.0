# ruff: noqa: F811 - the fixture `ra` is imported, and the tests take it by its name
"""The review of the milestone В5, group 4 (Э20): the form of a resource report.

A table the report did not count is not a table of zeros. The template «ГКЗ» has the columns «Балансовые» and
«Забалансовые»; a report made without the balance cut-offs (they are optional in the window) has no rows of those
kinds, and its form showed «0» tonnes in every row — for a deposit of thousands of tonnes — with no word about it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bf_core.db import dialect
from bf_core.resources import export_job
from bf_core.resources import forms as fm
from bf_core.resources import job as rj
from bf_core.resources import store as st
from tests.resapi_world import BODY, RS, get, model, ok, ra, refused, run  # noqa: F401 - the fixture
from tests.test_resources_compare import close, compare, find, saved, td37
from tests.test_resources_export import book, csv_lines, export, jorc_report, out, sheet_lines, w  # noqa: F401
from tests.test_resources_forms import AU, GKZ, cell, grp


def texts(form: dict[str, Any], part: str) -> set[str]:
    return {c["text"] for s in form["sections"] for r in s["rows"] for k, c in r["cells"].items()
            if k.startswith(part + ":")}  # fmt: skip


def values(form: dict[str, Any], part: str) -> set[Any]:
    return {c["value"] for s in form["sections"] for r in s["rows"] for k, c in r["cells"].items()
            if k.startswith(part + ":")}  # fmt: skip


def test_a_balance_form_of_a_report_without_the_balance_is_empty_not_zero() -> None:
    rows = [grp(None, 7500.0, 2.0, kind="all", cutoff=None), grp("A", 7500.0, 2.0, kind="all", cutoff=None)]
    form = fm.build(rows, GKZ, [AU], ["class"])
    assert values(form, "balance") == {None} and texts(form, "balance") == {""}  # no «0 тыс. т» of a table not made
    assert values(form, "offbalance") == {None} and texts(form, "offbalance") == {""}
    said = " ".join(form["warnings"])
    assert "балансов" in said.casefold() and "борт" in said.casefold()  # and the form says why and what to do


def test_a_balance_without_the_off_balance_leaves_only_those_columns_empty() -> None:
    rows = [grp("A", 1000.0, 3.0, kind="balance", cutoff=1.0), grp("B", 2000.0, 2.0, kind="balance", cutoff=1.0)]
    form = fm.build(rows, GKZ, [AU], ["class"])
    assert cell(form, "A", "balance:tonnes")["value"] == 1.0  # тыс. т
    assert cell(form, "C1", "balance:tonnes") == {"value": 0.0, "text": "0"}  # counted: no block of C1 — a true zero
    assert values(form, "offbalance") == {None} and texts(form, "offbalance") == {""}
    assert any("забаланс" in w.casefold() for w in form["warnings"])


def test_a_balance_table_that_was_counted_and_has_no_block_is_zero() -> None:
    """The rows of the kind are there (the total of the table), no group has a block: T = 0 is what was counted."""
    rows = [grp(None, 0.0, None, kind="balance", cutoff=5.0), grp(None, 0.0, None, kind="offbalance", cutoff=4.0)]
    form = fm.build(rows, GKZ, [AU], ["class"])
    assert cell(form, "Итого", "balance:tonnes") == {"value": 0.0, "text": "0"}
    assert cell(form, "Итого", "offbalance:tonnes") == {"value": 0.0, "text": "0"}
    assert not any("борт" in w.casefold() for w in form["warnings"])


def test_the_book_and_the_csv_of_such_a_form_keep_the_cells_empty_and_say_why(w: Any, out: Path) -> None:
    """Т8.10-5 for the same case: the numbers of the files are the numbers shown — no «0» in the book or in the CSV."""
    ident = jorc_report(w)  # a report with no balance cut-offs
    export(w, ident, out / "gkz.xlsx", template="gkz")
    lines = sheet_lines(book(out / "gkz.xlsx")["Отчёт"])
    head = next(i for i, r in enumerate(lines) if r[0] == "Строка")
    body = [r for r in lines[head + 1 :] if r[0] in ("A", "B", "C1", "C2", "A + B + C1", "Итого")]
    assert len(body) == 6 and all(v is None for r in body for v in r[1:])
    assert any("не задан балансовый борт" in str(v) for r in lines for v in r if v)
    export(w, ident, out / "gkz.csv", format="csv", what="form", template="gkz")
    rows = csv_lines(out / "gkz.csv")
    assert rows[0][0] == "Строка" and all(v == "" for r in rows[1:] for v in r[1:])


# --- «Сравнить отчёты»: an element in other units ---------------------------------------------------------------------


def test_a_grade_in_other_units_has_no_difference_not_a_number_of_two_units(ra) -> None:
    """Two reports of one model, AU read as g/t in one and as ppb in the other: Δḡ was 2,75 − 2,75 = 0 «г/т» for
    grades a thousand times apart, under the words «разности — в единицах A». ppm against g/t is one unit; the metal
    of one family (grams) is one unit too; g/t against % is neither."""
    mid = td37(ra)
    gt = saved(ra, mid, "AU в г/т", density="RHO")
    ppb = saved(ra, mid, "AU в ppb", density="RHO", elements=[{"field": "AU", "unit": "ppb"}])
    ppm = saved(ra, mid, "AU в ppm", density="RHO", elements=[{"field": "AU", "unit": "ppm"}])
    pct = saved(ra, mid, "AU в %", density="RHO", elements=[{"field": "AU", "unit": "%"}])

    out = compare(ra, gt, ppb)
    d = find(out)["delta"]
    assert d["tonnes"] == 0.0 and d["elements"]["AU"]["grade"] is None  # not «0 г/т»
    assert close(d["elements"]["AU"]["metal"], 1100.0 - 1.1)  # grams against grams
    assert "grade_units_differ" in [w["code"] for w in out["warnings"]]
    assert not any("в единицах A" in w["message"] for w in out["warnings"])

    same = find(compare(ra, gt, ppm))["delta"]["elements"]["AU"]
    assert same == {"grade": 0.0, "metal": 0.0}  # 1 ppm is 1 g/t

    other = find(compare(ra, gt, pct))["delta"]["elements"]["AU"]
    assert other == {"grade": None, "metal": None}  # grams minus tonnes is not a difference


# --- a report saved with a template of the user that is deleted afterwards --------------------------------------------


def test_a_report_whose_template_was_deleted_still_opens_rebuilds_and_exports(ra, tmp_path: Path) -> None:
    """A template is a view of a report (it can be deleted, the report does not go «устарел»). A report saved with a
    template of the user answered 404 `template_not_found` for ever after the template was deleted: it could not be
    opened (the panel reads it without `?template=`), rebuilt or exported."""
    mid = model(ra)
    mine = {"name": "Свой", "labels": ["Измеренные", "Выявленные"], "subtotals": [], "total": "Всего"}
    assert ok(ra.put(f"{RS}/templates/svoy", json={"template": mine})) == {"id": "svoy", "version": 1}
    rid = run(ra, {"model_id": mid, **BODY, "template_id": "svoy"}, save=True, name="Со своим шаблоном")["report_id"]
    assert get(ra, rid)["form"]["template"]["id"] == "svoy"
    ok(ra.delete(f"{RS}/templates/svoy", params={"expected_version": 1}))

    got = get(ra, rid)  # 200: the rows are there, the form is not, and the report says why
    assert got["form"] is None and got["rows"] and got["rows_total"] > 0
    assert [w["code"] for w in got["warnings"]] == ["template_missing"] and "svoy" in got["warnings"][0]["message"]
    assert get(ra, rid, template="jorc")["form"]["template"]["id"] == "jorc"  # another view of the same rows
    refused(ra.get(f"{RS}/reports/{rid}", params={"template": "svoy"}), 404, "template_not_found")  # asked by name

    with dialect.read_tx(ra.project.engine) as conn:
        again = st.rebuild_params(conn, rid)
    out = rj.run_report(ra.project.engine, ra.project.folder, again)  # «Перестроить»
    assert out["version"] == 2 and out["form"] is None and out["rows"] == got["rows"]
    assert "template_missing" in [w["code"] for w in out["warnings"]]
    assert [w["code"] for w in get(ra, rid)["warnings"]] == ["template_missing"]  # told once, not stored twice

    path = tmp_path / "out" / "report.xlsx"
    path.parent.mkdir()
    with dialect.read_tx(ra.project.engine) as conn:
        params = export_job.request(conn, ra.project.folder, rid, {"format": "xlsx", "path": str(path)})
    export_job.run_export(ra.project.engine, ra.project.folder, params)
    assert path.is_file() and path.stat().st_size > 0  # the generic form, as for a report with no template

    # a NEW report with a template that is not there is refused as before
    refused(ra.post(f"{RS}/reports", json={"params": {"model_id": mid, **BODY, "template_id": "svoy"}, "save": False}),
            404, "template_not_found")  # fmt: skip
