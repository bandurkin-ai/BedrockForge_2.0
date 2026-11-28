"""What a model that is a LIST of blocks does not do (ADR 0030 decision 8; подшаг 27.4д): the estimation, the resources,
the pit, the comparison of estimates and the statistics by the grid refuse it with 422 `block_list_unsupported` and the
hint «Регуляризуйте модель» — one test for each module, through its own entry (the route, the job or the function the
route calls) — and the memory of the import job counts the boxes of a list."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.importing import object_blockfile as obf
from tests.test_blocks_list import import_list, partition
from tests.test_import_blocks import project as base_project  # noqa: F401


@pytest.fixture()
def project(base_project):  # type: ignore[no-untyped-def]  # noqa: F811
    base_project.uid = "list-project"
    return base_project


@pytest.fixture()
def listed(project) -> tuple[Any, int]:  # type: ignore[no-untyped-def]  # noqa: F811
    return project, import_list(project, partition(parents=3)).model_id


def refused(exc: pytest.ExceptionInfo[ApiError]) -> None:
    e = exc.value
    assert (e.status, e.code) == (422, "block_list_unsupported"), (e.status, e.code)
    assert "Регуляризуйте модель" in e.hint and "списка блоков" in e.message


def test_the_estimation_refuses_a_list(listed) -> None:  # type: ignore[no-untyped-def]
    from bf_core.geostat.estimate.defaults import model_part

    p, mid = listed
    with dialect.read_tx(p.engine) as conn, pytest.raises(ApiError) as exc:
        model_part(conn, p.store, mid)
    refused(exc)


def test_the_resources_refuse_a_list(listed) -> None:  # type: ignore[no-untyped-def]
    from bf_core.resources.routes import check_request

    p, mid = listed
    req = SimpleNamespace(save=False, params=SimpleNamespace(model_id=mid))
    with dialect.read_tx(p.engine) as conn, pytest.raises(ApiError) as exc:
        check_request(conn, p.store, req)
    refused(exc)


def test_the_pit_refuses_a_list(listed) -> None:  # type: ignore[no-untyped-def]
    from bf_core.mine.routes import model_form

    p, mid = listed
    with dialect.read_tx(p.engine) as conn, pytest.raises(ApiError) as exc:
        model_form(conn, p.store, mid)
    refused(exc)


def test_the_comparison_of_estimates_refuses_a_list(listed) -> None:  # type: ignore[no-untyped-def]
    from bf_core.blocks.compare_job import run_compare

    p, mid = listed
    with pytest.raises(ApiError) as exc:
        run_compare(p.engine, p.folder, {"model_id": mid, "field_a": "AU", "field_b": "SG"})
    refused(exc)


def test_the_statistics_by_the_grid_refuse_a_list(listed) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats import blocks_cache as bc

    p, mid = listed
    with dialect.read_tx(p.engine) as conn, pytest.raises(ApiError) as exc:
        bc.model_of(conn, mid)
    refused(exc)


def test_the_memory_of_the_import_job_counts_the_boxes_of_a_list(project) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from bf_core.importing import objects

    path = project.tmp / "big.csv"
    path.write_text("X,Y,Z,DX,DY,DZ,AU\n" + "1,2,3,1,1,1,0.5\n" * 40_000, encoding="utf-8")
    size = path.stat().st_size
    plain, boxes = obf.memory(path, size), obf.memory(path, size, listed=True)
    assert boxes >= plain >= 1024
    huge = 20_000_000 * 40  # a text file of 20 million blocks: the reserve grows with the rows
    assert obf.memory(path, huge, listed=True) - 1024 >= 2400  # ≈ 130 bytes a row: 2.4 GB
    assert obf.memory(path, huge, listed=True) > obf.memory(path, huge)
    # the job asks for it when the wizard chose the list
    job = lambda blocks: objects._memory({"files": [str(path)], "options": {str(path): {"blocks": blocks}}})  # noqa: E731
    assert job({"layout": "list"}) >= job({})
