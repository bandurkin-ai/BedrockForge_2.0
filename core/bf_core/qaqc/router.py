"""/api/v1/qaqc — the settings, the CRM reference and the QAQC data (ADR 0020 §10; подшаг 14.10).

  * `GET /config` → {version, types {code: type}, dl [{element, lab, value, unit}], limits, empty_is_orig};
    `PUT /config {types, dl, limits, empty_is_orig, expected_version}` → the same; 409 `version_conflict {current}`,
    422 `bad_param {param}`, 423. Event `qaqc.changed {what: "config", version}`.
  * `GET /crm` → {version, rows [{crm_id, element, mean, sd, unit, source}]}; `PUT /crm {rows, expected_version}`;
    422 `bad_crm {row, param}`, `too_many_rows`; `POST /crm/read {path, sheet?}` → {rows, problems [{row, message}],
    columns} — nothing written. Event `qaqc.changed {what: "crm", version}`.
  * `GET /data` → {items: [data], limit: 50}; `GET /data/{id}` → {id, name, version, rows, elements [{name, unit}],
    fields [{name, kind}], labs, batches, date_from, date_to, type_codes [{code, type | null, n}], source, mapping,
    stale, source_missing, sha256, counts, notes, …}; `PUT /data/{id} {name, expected_version}`; `DELETE /data/{id}`
    → {id, deleted}. Event `objects.changed {kind: "qaqc_data", id, deleted?}`. 404 `qaqc_data_not_found`.
  * `POST /data/inspect {kind: "file", path, sheet?, encoding?, delimiter?, header_row?} | {kind: "table", set_id,
    table}` → {kind, columns [{name, kind, samples}], rows, rows_exact, mapping, preview (≤ 50 rows), info, summary,
    warnings}.
  * `POST /data {name?, source, mapping, replace_id?}` → 202 {job_id, source, id} of `qaqc.import` (heavy); 422
    `no_sample_id`, `no_type`, `no_elements`, `field_not_found {param}`, `too_many_elements`, `too_many_data`; 409
    `name_taken`; 423. `POST /data/{id}/refresh` → 202 the same job with the source and the mapping of the data
    (a table found again by names after a `.bfggis` import); 404 `source_missing` — the table is gone.
  * `GET /data/{id}/values?field=&q=&limit=` → {values [{value, n}], more, empty}: the values of a field of the
    records for the filter (≤ 1000, by value; `q` — a part of the text, without case). 422 `field_not_found`.

The routes of the parts are included here: `routes_calc` (`/calc/{section}`, `/records`), `routes_reports`
(`/reports…`).
numpy, pyarrow and the readers load on the first request, not with this module (tests/test_process.py)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import routes_calc, routes_reports

router = APIRouter(prefix="/qaqc")
router.include_router(routes_calc.router)
router.include_router(routes_reports.router)
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[Any, Body()]
MAX_VALUES = 1000


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна QAQC.")
    return raw


def _publish(request: Request, event: str, data: dict[str, Any]) -> None:
    request.app.state.core.events.publish(event, data)


# --- settings and the CRM reference ----------------------------------------------------------------------------


@router.get("/config")
def get_config(svc: Svc) -> dict[str, Any]:
    from . import config as cf

    with dialect.read_tx(svc.require_current().engine) as conn:
        return cf.get_config(conn)


@router.put("/config")
def put_config(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    from . import config as cf

    raw = _body(body)
    with dialect.write_tx(svc.require_current(writable=True).engine) as conn:
        settings = {k: v for k, v in raw.items() if k != "expected_version"}
        out = cf.put_config(conn, settings, raw.get("expected_version"))
    _publish(request, "qaqc.changed", {"what": "config", "version": out["version"]})
    return out


@router.get("/crm")
def get_crm(svc: Svc) -> dict[str, Any]:
    from . import config as cf

    with dialect.read_tx(svc.require_current().engine) as conn:
        return cf.get_crm(conn)


@router.put("/crm")
def put_crm(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    from . import config as cf

    raw = _body(body)
    with dialect.write_tx(svc.require_current(writable=True).engine) as conn:
        out = cf.put_crm(conn, raw.get("rows"), raw.get("expected_version"))
    _publish(request, "qaqc.changed", {"what": "crm", "version": out["version"]})
    return out


@router.post("/crm/read")
def read_crm(svc: Svc, body: Payload) -> dict[str, Any]:
    from pathlib import Path

    from . import config as cf

    svc.require_current()
    raw = _body(body)
    path = raw.get("path")
    if not isinstance(path, str) or not Path(path).is_file():
        raise ApiError(404, "file_not_found", "Файл справочника не найден.", "Проверьте путь к файлу.",
                       extra={"param": "path"})  # fmt: skip
    sheet = raw.get("sheet")
    return cf.read_crm_file(path, sheet if isinstance(sheet, str) and sheet else None)


# --- data ---------------------------------------------------------------------------------------------------


@router.get("/data")
def list_data(svc: Svc) -> dict[str, Any]:
    from . import service as sv

    with dialect.read_tx(svc.require_current().engine) as conn:
        return {"items": sv.list_data(conn), "limit": sv.MAX_DATA}


@router.get("/data/{data_id}")
def get_data(svc: Svc, data_id: int) -> dict[str, Any]:
    from . import service as sv

    with dialect.read_tx(svc.require_current().engine) as conn:
        return sv.info(conn, data_id)


@router.put("/data/{data_id}")
def rename_data(svc: Svc, request: Request, data_id: int, body: Payload) -> dict[str, Any]:
    from . import service as sv

    raw = _body(body)
    with dialect.write_tx(svc.require_current(writable=True).engine) as conn:
        out = sv.rename(conn, data_id, raw.get("name"), raw.get("expected_version"))
    _publish(request, "objects.changed", {"kind": sv.KIND, "id": data_id})
    return out


@router.delete("/data/{data_id}")
def delete_data(svc: Svc, request: Request, data_id: int) -> dict[str, Any]:
    from . import records as r
    from . import service as sv

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        gone = sv.delete(conn, data_id)
    for sha in gone["shas"]:
        r.CACHE.forget((project.uid, sha))
    _publish(request, "objects.changed", {"kind": sv.KIND, "id": data_id, "deleted": True})
    return {"id": data_id, "deleted": True}


@router.post("/data/inspect")
def inspect(svc: Svc, body: Payload) -> dict[str, Any]:
    from . import jobs as qj

    project = svc.require_current()
    raw = _body(body)
    kind = raw.get("kind") or ("table" if "set_id" in raw else "file")
    if qj.source_kind({**raw, "kind": kind}) == "table":
        return qj.inspect_table(project, raw)
    return qj.inspect_file(raw)


def _submit(s: Scheduler, params: dict[str, Any]) -> JSONResponse:
    from . import jobs as qj

    key, job_id = s.submit(qj.KIND, params, title=f"Импорт данных QAQC: {params['name']}")
    return JSONResponse({"job_id": job_id, "source": key, "id": job_id}, status_code=202)


@router.post("/data")
def import_data(svc: Svc, s: Sched, body: Payload) -> JSONResponse:
    from . import jobs as qj

    project = svc.require_current(writable=True)
    return _submit(s, qj.check_submit(project, _body(body)))


@router.post("/data/{data_id}/refresh")
def refresh_data(svc: Svc, s: Sched, data_id: int) -> JSONResponse:
    from . import jobs as qj
    from . import service as sv

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        current = sv.info(conn, data_id)
        state = sv.source_state(conn, {"source": current["source"]})
    src = dict(current["source"] or {})
    if src.get("kind") == "table":
        if state["source_missing"]:
            raise ApiError(404, "source_missing", "Таблица-источник данных QAQC не найдена.",
                           "Импортируйте данные из другой таблицы или файла.")  # fmt: skip
        src = {"kind": "table", "set_id": state["set_id"], "table": state["table_id"]}
    body = {"name": current["name"], "source": src, "mapping": current["mapping"], "replace_id": data_id}
    return _submit(s, qj.check_submit(project, body))


@router.get("/data/{data_id}/values")
def field_values(
    svc: Svc,
    data_id: int,
    field: Annotated[str, Query(max_length=200)],
    q: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=MAX_VALUES)] = 200,
) -> dict[str, Any]:
    from . import service as sv

    recs = sv.open_records(svc.require_current(), data_id)
    return values_of(recs, field, q, limit)


def values_of(recs: Any, field: str, q: str = "", limit: int = 200) -> dict[str, Any]:
    """The distinct values of a field with their counts, by value; `q` — a part of the text, without case."""
    import numpy as np

    try:
        kind = recs.schema.kind(field)
    except KeyError:
        raise ApiError(422, "field_not_found", f"Поля «{field}» нет в данных QAQC.", "Выберите поле из списка.",
                       extra={"param": "field"}) from None  # fmt: skip
    needle = q.strip().casefold()
    if kind == "text":
        t = recs.text(field)
        n = np.bincount(t.codes + 1, minlength=len(t.labels) + 1)
        pairs = [(lab, int(k)) for lab, k in zip(t.labels, n[1:].tolist(), strict=True) if k]
        empty = int(n[0])
    elif kind == "id":
        import pyarrow.compute as pc

        arr = recs.ids(field)
        vc = pc.value_counts(arr.drop_null())
        pairs = sorted(zip(vc.field("values").to_pylist(), vc.field("counts").to_pylist(), strict=True))
        empty = int(arr.null_count)
    elif kind in ("number", "date"):
        values = recs.date() if kind == "date" else recs.number(field)
        ok = values[~np.isnan(values)]
        u, n = np.unique(ok, return_counts=True)
        pairs = list(zip(u.tolist(), n.tolist(), strict=True))
        empty = int(len(values) - len(ok))
    else:
        raise ApiError(422, "field_not_found", f"По полю «{field}» не фильтруют.", "Выберите другое поле.",
                       extra={"param": "field"})  # fmt: skip
    if needle:
        pairs = [(v, k) for v, k in pairs if needle in str(v).casefold()]
    return {"values": [{"value": v, "n": k} for v, k in pairs[:limit]], "more": len(pairs) > limit, "empty": empty}
