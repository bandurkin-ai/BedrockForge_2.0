"""The second review of the list of blocks (подшаг 27.10б): the tolerance of the wizard is refused above a quarter of a
sub-cell (422 `bad_param {param: "grid.tol"}`, not cut), the report and the regularization of a list with overlapping
blocks say the common part is counted twice, a long series of blocks with one centre is not quadratic, the statistics
(swath) do not offer a list, and the error of an inspection keeps its own code and message over `extra`."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from bf_core.api.errors import ApiError
from bf_core.blocks import jobs as bj
from bf_core.blocks import list_kernels as lk
from bf_core.blocks import list_layout as ll
from bf_core.blocks.schema import GridV2
from bf_core.importing import object_blockfile as obf
from tests.test_blocks_list import import_list, partition, report_total, write_csv
from tests.test_import_blocks import project as base_project  # noqa: F401
from tests.test_import_blocks import regular, run, text


@pytest.fixture()
def project(base_project):  # type: ignore[no-untyped-def]  # noqa: F811
    base_project.uid = "review"
    return base_project


def test_a_tolerance_over_a_quarter_of_a_sub_cell_is_refused_not_cut(project) -> None:  # type: ignore[no-untyped-def]
    m = regular()  # a parent of 10 × 20 × 5 m, no sub-blocks: a quarter of a sub-cell is 1.25 m at most
    path = project.tmp / "tol.csv"
    path.write_text(text(m, decimal="."), encoding="utf-8")
    ok = obf.inspect_blockfile(path, {"blocks": {"grid": {"tol": 1.0}}})
    assert ok["error"] is None
    for blocks in ({"grid": {"tol": 3.0}}, {"grid": {"tol": 1.3}}):
        info = obf.inspect_blockfile(path, {"blocks": blocks})
        assert info["error"]["code"] == "bad_param" and info["error"]["param"] == "grid.tol", info["error"]
        with pytest.raises(ApiError) as exc:
            run(project, path, blocks)
        assert exc.value.code == "bad_param" and exc.value.extra["param"] == "grid.tol"
    with pytest.raises(ApiError) as exc:
        obf.BlockOptions.of({"grid": {"tol": 0}}).hint(None)  # the wizard sends a number above zero
    assert exc.value.extra["param"] == "grid.tol"


def test_the_error_of_an_inspection_keeps_its_code_over_the_extra() -> None:
    # `{**exc.extra, "code": …}`: an `extra` with a key of the error itself must not replace the code or the message
    extra = {"code": "other", "message": "чужое", "n": 1}
    e = ApiError(422, "blocks_unaligned", "Сообщение.", "Подсказка.", extra=extra)
    out = {**e.extra, "code": e.code, "message": e.message, "hint": e.hint}
    assert out["code"] == "blocks_unaligned" and out["message"] == "Сообщение." and out["n"] == 1


def overlapping(project) -> tuple[Any, Any]:  # type: ignore[no-untyped-def]
    m = partition(parents=3, seed=4)
    inside = (m.centre[20][0] + 0.1, m.centre[20][1], m.centre[20][2], *m.d[20], 1.0, 2.7, "руда")
    path = write_csv(project.tmp / "over.csv", m, extra=[inside])
    return run(project, path, {"layout": "list"}, name="Наложения"), m


def test_the_report_and_the_regularization_of_overlapping_blocks_say_so(project) -> None:  # type: ignore[no-untyped-def]
    res, _m = overlapping(project)
    assert res.checks["overlaps"]["n"] >= 2
    out = report_total(project, res.model_id)
    assert any("перекрывающихся блоков" in w and "дважды" in w for w in out["warnings"]), out["warnings"]
    done = bj.run_regularize(project.engine, project.folder, {"model_id": res.model_id, "size": [10.0, 10.0, 10.0],
                                                              "name": "Из наложений", "density": 2.7})
    assert any("перекрывающихся блоков" in w and "дважды" in w for w in done["warnings"]), done["warnings"]
    clean = import_list(project, partition(parents=3, seed=5), "Чистая")
    assert not [w for w in report_total(project, clean.model_id)["warnings"] if "дважды" in w]
    done = bj.run_regularize(project.engine, project.folder, {"model_id": clean.model_id, "size": [10.0, 10.0, 10.0],
                                                              "name": "Чистая 10", "density": 2.7})
    assert done["warnings"] == []


def test_a_long_series_of_blocks_with_one_centre_is_not_quadratic() -> None:
    n = 120_000
    grid = GridV2.of({"corner": [0.0, 0.0, 0.0], "counts": [1, 1, 1], "size": [10.0, 10.0, 10.0], "div": [1, 1, 1]})
    lc = np.full((n, 3), 5.0)
    ld = np.column_stack([np.full(n, 1.0), np.full(n, 1.0), 1.0 + (np.arange(n) % 4) * 0.25])  # four sizes only
    ll.arrange(grid, lc[:8], ld[:8])  # compiled before the timing
    t0 = time.perf_counter()
    got = ll.arrange(grid, lc, ld)
    assert time.perf_counter() - t0 < 5.0  # the pairs of 120 000 rows would be 7·10⁹ comparisons
    assert got.dups == n - 4 and len(got.order) == 4
    assert sorted(got.order.tolist()) == [0, 1, 2, 3]  # the first row of every size stays
    assert [e["other_row"] for e in got.examples][:3] == [0, 1, 2] and len(got.examples) == 20
    # the kernel alone: the first of equal sizes is the one the others repeat
    idx = np.zeros(6, dtype=np.uint64)
    c = np.full((6, 3), 1.0)
    d = np.array([[1, 1, 1], [2, 1, 1], [1, 1, 1], [3, 1, 1], [2, 1, 1], [1, 1, 1]], dtype=np.float64)
    dup, first = lk.mark_duplicates(idx, c, d)
    assert dup.tolist() == [False, False, True, False, True, True] and first.tolist() == [-1, -1, 0, -1, 1, 0]


def test_the_statistics_by_the_grid_do_not_offer_a_list(project) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats.routes_swath import sources_of

    res = import_list(project, partition(parents=2), "Список для профилей")
    got = {m["id"]: m for m in sources_of(project)["models"]}[res.model_id]
    assert got["available"] is False and "список блоков" in got["reason"]["message"]
    assert "Регуляризуйте модель" in got["reason"]["hint"]
    grid = run(project, _grid_file(project), name="Сеточная")
    ok = {m["id"]: m for m in sources_of(project)["models"]}[grid.model_id]
    assert "available" not in ok and "reason" not in ok  # a grid model: the answer is as it was


def _grid_file(project) -> Any:  # type: ignore[no-untyped-def]
    path = project.tmp / "grid.csv"
    path.write_text(text(regular(), decimal="."), encoding="utf-8")
    return path

