"""The rows of one prefix that are in NO common plan (В5 Fable, group 2, № 1): the second run with «Заменить поля»
keeps the values of the domain of the first (№ 77, docs/progress/e18-api.md §2.2) — the rows that wrote the current
fields (the graph of the lineage) are its neighbours as much as the rows of a plan are."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from tests import estjob_util as u
from tests.test_estimate_job import cols, run, same


def _two_rows(jw: u.JW) -> tuple[int, int]:
    return u.make(jw, name="Руда"), u.make(jw, label="порода", zone="перв", name="Порода")


def test_a_run_of_a_second_row_of_the_prefix_keeps_the_domain_of_the_first(tmp_path: Path) -> None:
    jw = u.world(tmp_path)
    try:
        a, b = _two_rows(jw)  # one prefix, two domains, no plan
        first = run(jw, estimation_ids=[a])
        names = first["fields"]
        before = cols(jw, names)
        dom = cols(jw, ["DOM"])["DOM"]
        assert not np.isnan(before["AU_OK_EST"][dom == 0]).all()
        with pytest.raises(ApiError) as e:  # the question names the rows whose blocks are kept and those replaced
            run(jw, estimation_ids=[b])
        assert (e.value.status, e.value.code, e.value.extra["field"]) == (409, "field_exists", "AU_OK_STATUS")
        assert e.value.extra["kept"] == ["Руда"] and e.value.extra["replaced"] == ["Порода"]
        assert "«Руда»" in e.value.message and "сохран" in e.value.message and "«Порода»" in e.value.message
        out = run(jw, estimation_ids=[b], replace=True)
        after = cols(jw, names)
        for n in names:
            assert same(after[n][dom == 0], before[n][dom == 0]), n  # the domain of the first row: carried
        assert (after["AU_OK_STATUS"][dom == 0] == 0).all()
        assert same(after["AU_OK_EST"][dom == 1], u.direct(jw, b)["EST"][dom == 1])
        assert any("Руда" in line and "сохранены" in line for line in out["log"])
        again = run(jw, estimation_ids=[a], replace=True)  # and the other way round
        back = cols(jw, names)
        for n in names:
            assert same(back[n][dom == 1], after[n][dom == 1]), n
        assert again["fields"] == names
    finally:
        jw.project.engine.dispose()


def test_a_row_that_replaces_its_own_fields_alone_is_asked_as_before(tmp_path: Path) -> None:
    jw = u.world(tmp_path)
    try:
        a, _b = _two_rows(jw)
        run(jw, estimation_ids=[a])
        with pytest.raises(ApiError) as e:
            run(jw, estimation_ids=[a])
        assert e.value.code == "field_exists" and e.value.extra["kept"] == [] and e.value.extra["replaced"] == ["Руда"]
        run(jw, estimation_ids=[a], replace=True)
    finally:
        jw.project.engine.dispose()
