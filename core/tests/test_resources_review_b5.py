"""The review of the milestone В5, group 4 (Э20): the form of a resource report.

A table the report did not count is not a table of zeros. The template «ГКЗ» has the columns «Балансовые» and
«Забалансовые»; a report made without the balance cut-offs (they are optional in the window) has no rows of those
kinds, and its form showed «0» tonnes in every row — for a deposit of thousands of tonnes — with no word about it."""

from __future__ import annotations

from typing import Any

from bf_core.resources import forms as fm
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
