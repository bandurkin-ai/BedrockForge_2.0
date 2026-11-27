"""The review of the milestone В5, group 5 (Э23): the assistant.

1. A number of the model that no 64-bit column takes (`offset`, `set_id`, `id` beyond 2⁶³) reached the query of a read
   tool: the database refused it, the model got «внутренняя ошибка ядра» and the log of the core a traceback. It is a
   wrong argument: the answer says which one (ТЗ 7.2: a Russian text for the model on a mistake)."""

from __future__ import annotations

import logging

import pytest

from bf_core.assistant import data_tools as dt
from tests import test_assistant_data_project as base
from tests.test_assistant_data_project import SET_ID, call

demo = base.demo  # the open demo project of the neighbour module

HUGE = 10**30


@pytest.mark.parametrize(
    ("name", "args", "param"),
    [
        ("data_holes", {"set_id": SET_ID, "offset": HUGE}, "offset"),
        ("data_holes", {"set_id": HUGE}, "set_id"),
        ("data_findings", {"set_id": SET_ID, "offset": HUGE}, "offset"),
        ("data_history", {"kind": "drillhole_set", "id": -HUGE}, "id"),
        ("data_hole", {"hole_id": "DH-001", "set_id": 2**70}, "set_id"),
    ],
)
def test_a_number_beyond_64_bits_is_a_wrong_argument_not_an_internal_error(
    demo, caplog: pytest.LogCaptureFixture, name: str, args: dict[str, object], param: str
) -> None:  # type: ignore[no-untyped-def]
    with caplog.at_level(logging.ERROR, logger="bf_core.assistant.data"):
        res, _ = call(demo, name, args)
    assert res.is_error and f"«{param}»" in res.text and "слишком велико" in res.text
    assert "внутренняя ошибка" not in res.text and not caplog.records  # no traceback in the log of the core


def test_the_largest_64_bit_number_is_still_an_argument(demo) -> None:  # type: ignore[no-untyped-def]
    res, page = call(demo, "data_holes", {"set_id": SET_ID, "offset": dt.INT_MAX})
    assert not res.is_error and page["rows"] == [] and page["next_offset"] is None
