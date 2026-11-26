"""/api/v1/pit — the template, the report, the surfaces, the solid, the comparison (подшаг 21.11; ADR 0028 «API»,
решения 25–27; docs/progress/e21-api.md §4, §7, §9–§11). Relative to `/pit` (`router.py`):

  * `GET /scenarios/{id}/template` → {template}: `sidecar:template` — K, the offsets and φ_эфф(az) of every domain.
    404 `pit_scenario_not_found`; 409 `pit_no_result`.
  * `GET /scenarios/{id}/report?calendar=` → the pit-by-pit report (`report_view.report_json`; the calendar is a JSON
    text, the one of the scenario by default; the NPV is recomputed from `data`, nothing is written). 404; 409
    `pit_no_result`; 422 `bad_param {calendar}`. Works in a project «только просмотр».
  * `POST /scenarios/{id}/surfaces` {shells?} → {source, id} of the job `pit.surfaces` (quick; 21.12). Checked before
    it: 404; 409 `pit_no_result`, `pit_running`; 422 `bad_param {shells.i}` (a number beyond the shells of the
    result); 423. The job is given {scenario_id, shells, expected_version}. While the job is not in the build — 501
    `not_implemented`.
  * `POST /scenarios/{id}/solid` {shell, name?} → {source, id} of the job `pit.solid` (quick; 21.12), the same checks
    (422 `bad_param {shell}`); the job is given {scenario_id, shell, name, expected_version}.
  * `POST /compare` {a, b} → the comparison of two results (21.15; synchronous, works «только просмотр»): 404
    `pit_scenario_not_found`; 409 `pit_no_result`; 422 `bad_param {a, b}`; A = B is allowed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.geostat.estimate.routes_common import Payload, Sched, Svc, body_of

from . import spec
from .routes import submit

router = APIRouter()
KIND_SURFACES = "pit.surfaces"
KIND_SOLID = "pit.solid"


def not_implemented(what: str) -> ApiError:
    return ApiError(501, "not_implemented", f"{what} ещё не подключён в этой сборке.", "Обновите программу.")


def registered(kind: str, what: str) -> None:
    from bf_core.jobs.registry import KINDS

    if kind not in KINDS:
        raise not_implemented(what)


# --- the template and the report --------------------------------------------------------------------------------


@router.get("/scenarios/{scenario_id}/template")
def get_template(svc: Svc, scenario_id: int) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        doc = st.read_template(conn, BlobStore(project.folder), scenario_id)  # 404; 409 `pit_scenario_broken`
    if doc is None:
        raise st.no_result()
    return {"template": doc}


@router.get("/scenarios/{scenario_id}/report")
def get_report(svc: Svc, request: Request, scenario_id: int) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import report_view

    query = spec.parse_report_query(request.query_params)
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return report_view.report_json(conn, BlobStore(project.folder), scenario_id, query.calendar)


# --- the surfaces and the solid (21.12) -------------------------------------------------------------------------


def _result_of(conn: Any, scenario_id: int, *, running: bool) -> tuple[int, int, list[int] | None]:
    """The scenario has a result (and is not running when it is written): → (the version, the shells in the result,
    the counts of the grid of the model as the run saw them — for the estimate of the memory of the job)."""
    from . import store as st

    row = st._row(conn, scenario_id)  # noqa: SLF001 - 404 `pit_scenario_not_found`
    if running:
        st._writable(conn, row)  # noqa: SLF001 - 409 `pit_running`
    if st.data_sha(conn, scenario_id) is None:
        raise st.no_result()
    meta = row.meta or {}
    counts = ((meta.get("model") or {}).get("grid") or {}).get("counts")
    return int(row.version), len(meta.get("shells") or []), (list(counts) if counts else None)


def _size(counts: list[int] | None, shells: int) -> dict[str, int]:
    """The sizes of the plan for `memory_mb` of the job (`limits.hints`); none when the result has no grid."""
    from . import limits

    return limits.hints(counts, shells) if counts else {}


def _bad(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


@router.post("/scenarios/{scenario_id}/surfaces")
def surfaces(svc: Svc, s: Sched, scenario_id: int, body: Payload) -> dict[str, Any]:
    raw = body_of(body)
    req = spec.parse_surfaces(raw)
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        version, n, counts = _result_of(conn, scenario_id, running=True)
    if req.shells is not None:  # the index is that of the body (`raw`), not of the sorted list of `req`
        for i, v in enumerate(raw.get("shells") or []):
            if int(v) > n:
                raise _bad(
                    f"shells.{i}", f"Оболочки {int(v)} нет: в результате их {n}.",
                    "Выберите номера от 1 до числа оболочек.",
                )  # fmt: skip
    registered(KIND_SURFACES, "Построение поверхностей оболочек")
    job = {"scenario_id": scenario_id, "shells": None if req.shells is None else list(req.shells),
           "expected_version": version, **_size(counts, n if req.shells is None else len(req.shells))}  # fmt: skip
    key, job_id = submit(s, KIND_SURFACES, job, "Поверхности оболочек карьера")
    return {"source": key, "id": job_id}


@router.post("/scenarios/{scenario_id}/solid")
def solid(svc: Svc, s: Sched, scenario_id: int, body: Payload) -> dict[str, Any]:
    req = spec.parse_solid(body_of(body))
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        version, n, counts = _result_of(conn, scenario_id, running=False)
    if req.shell > n:
        raise _bad("shell", f"Оболочки {req.shell} нет: в результате их {n}.",
                   "Выберите номер от 1 до числа оболочек.")  # fmt: skip
    registered(KIND_SOLID, "Построение каркаса карьера")
    job = {"scenario_id": scenario_id, "shell": req.shell, "name": req.name, "expected_version": version,
           **_size(counts, 1)}  # fmt: skip
    key, job_id = submit(s, KIND_SOLID, job, "Каркас карьера")
    return {"source": key, "id": job_id}


# --- the comparison (21.15) -------------------------------------------------------------------------------------


@router.post("/compare")
def compare(svc: Svc, body: Payload) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import compare as cp

    req = spec.parse_compare(body_of(body))
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return cp.compare_scenarios(conn, BlobStore(project.folder), req.a, req.b)
