"""/api/v1/geostat — the checks of an estimate (ТЗ 8.8.5; ADR 0024 §8; подшаг 18.13; docs/progress/e18-api.md §8):

  * `POST /estimate/xval` {estimation_id, by?: "hole" | "sample"} → {source, id} of the job `estimate.xval` (heavy);
    `GET /estimations/{id}/xval?page=1&size=500` → the page of the table, the summary and the histogram (404
    `xval_not_found`).
  * `POST /estimate/kna` {estimation_id | estimation, blocks?, vary?} → {source, id} of the job `estimate.kna` (quick).
  * `POST /estimate/global` {estimation_id} → {source, id} of the job `estimate.global` (quick); 409 `not_estimated`
    when the model has no field of the row.
  * `POST /estimate/qq` {estimation_id, quantiles?, weighted?, channel?, seq?} — synchronously, in a HEAVY slot.
  * `POST /estimate/swath` {estimation_id, direction?} → the settings of the swath window of Э13 for the row.

Starting a job writes (xval) or is a calculation of the project: the read-only project (423 `project_read_only`) may
run `kna`, `global`, `qq`, `swath` and read the table; `xval` writes the table and is refused. The checks of the body
(404, 409 `source_lost`, 422) are made here, before the job is queued. A job not registered in this build — 422
`not_ready`. Events: the job `xval` brings `geostat.changed {kind: "estimation", id}` in its result.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.storage.blobs import BlobStore

from .routes_common import Payload, Sched, Svc, body_of, common_of, registered, sample_rows, sync

router = APIRouter()
KIND_XVAL, KIND_KNA, KIND_GLOBAL = "estimate.xval", "estimate.kna", "estimate.global"


def _row(project: Any, params: dict[str, Any]) -> tuple[int, str]:
    """(id, name) of the row of the body: 422 `bad_param`, 404 `estimation_not_found`, 409 `source_lost`."""
    from . import checks

    return checks._row(project.engine, params)[:2]  # noqa: SLF001


@router.post("/estimate/xval")
def xval(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from . import xval as xv

    raw = body_of(body)
    project = svc.require_current(writable=True)
    ident, by = xv._parse(raw)  # noqa: SLF001
    from . import checks

    ident, name, spec, _org = checks._row(project.engine, {"estimation_id": ident})  # noqa: SLF001
    with dialect.read_tx(project.engine) as conn:
        rows = sample_rows(conn, [spec])
    registered(KIND_XVAL, "Перекрёстная проверка")
    params: dict[str, Any] = {"estimation_id": ident, "by": by}
    if rows:
        params["rows"] = rows  # for `memory_mb`: the rows of the table of the samples
    key, job_id = s.submit(KIND_XVAL, params, title=f"Перекрёстная проверка: {name}")
    return {"source": key, "id": job_id}


@router.get("/estimations/{estimation_id}/xval")
def xval_page(svc: Svc, estimation_id: int, page: Annotated[int, Query()] = 1,
              size: Annotated[int, Query()] = 500) -> dict[str, Any]:  # fmt: skip
    from . import store_base as sb
    from . import xval as xv
    from .routes_common import plain

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        sb.stored(conn, sb.KIND, estimation_id)  # 404 estimation_not_found before xval_not_found
        return plain(xv.read_page(conn, BlobStore(project.folder), estimation_id, page, size))  # type: ignore[no-any-return]


@router.post("/estimate/kna")
def kna(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from . import kna as kn
    from . import preview as pv
    from .spec import parse_at, parse_estimation

    raw = body_of(body)
    project = svc.require_current()
    have = [k for k in ("estimation_id", "estimation") if raw.get(k) is not None]
    if len(have) != 1:
        raise ApiError(422, "bad_param", "Укажите параметры оценки: сохранённые (estimation_id) или тело (estimation).",
                       "Выберите строку оценки.", {"param": "estimation_id"})  # fmt: skip
    kn.parse_vary(raw.get("vary"))
    blocks = raw.get("blocks")
    if blocks is not None:
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= kn.BLOCKS_MAX:
            raise ApiError(422, "bad_param", f"Тестовых блоков должно быть от 1 до {kn.BLOCKS_MAX}.",
                           "Щёлкните блоки в 3D или оставьте выбор автоматическим.", {"param": "blocks"})  # fmt: skip
        for i, a in enumerate(blocks):
            parse_at(a, f"blocks.{i}")
    params: dict[str, Any] = {"blocks": blocks, "vary": raw.get("vary")}
    with dialect.read_tx(project.engine) as conn:
        if have[0] == "estimation_id":
            ident, name = _row(project, {"estimation_id": raw["estimation_id"]})
            spec, _origin = pv.row_of(conn, ident)
            params["estimation_id"] = ident
        else:
            spec = pv.light(conn, parse_estimation(raw["estimation"]))
            params["estimation"] = spec.canonical()
            name = spec.name or spec.samples.field
    if spec.method.kind not in ("ok", "sk"):
        raise ApiError(422, "kna_method", "Анализ параметров кригинга — только для простого и обычного кригинга.",
                       "Выберите метод SK или OK.", {"param": "method.kind"})  # fmt: skip
    registered(KIND_KNA, "Анализ параметров кригинга")
    key, job_id = s.submit(KIND_KNA, params, title=f"Анализ параметров кригинга: {name}")
    return {"source": key, "id": job_id}


@router.post("/estimate/global")
def global_(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from bf_core.blocks.model import Model

    from . import checks
    from . import store_base as sb

    raw = body_of(body)
    project = svc.require_current()
    ident, name = _row(project, raw)
    with dialect.read_tx(project.engine) as conn:
        spec = sb.load_spec(dict(sb.stored(conn, sb.KIND, ident).meta or {}))
        model = Model.open(conn, BlobStore(project.folder), spec.model_id)
    checks.field_names(model, spec)  # 409 not_estimated
    registered(KIND_GLOBAL, "Глобальная проверка")
    key, job_id = s.submit(KIND_GLOBAL, {"estimation_id": ident}, title=f"Глобальная проверка: {name}")
    return {"source": key, "id": job_id}


@router.post("/estimate/qq")
def qq(svc: Svc, body: Payload) -> dict[str, Any]:
    from . import checks

    raw = body_of(body)
    project = svc.require_current()
    common = common_of(raw)
    params = {k: raw.get(k) for k in ("estimation_id", "quantiles", "weighted")}
    return sync(common, lambda: checks.run_qq(project.engine, project.folder, params))


@router.post("/estimate/swath")
def swath(svc: Svc, body: Payload) -> dict[str, Any]:
    from . import checks
    from . import store_base as sb

    raw = body_of(body)
    project = svc.require_current()
    ident, name = _row(project, raw)
    direction = raw.get("direction") or "z"
    if direction not in ("x", "y", "z"):
        raise ApiError(422, "bad_param", f"Неизвестное направление полос «{str(direction)[:20]}».",
                       "Выберите X, Y или Z.", {"param": "direction"})  # fmt: skip
    with dialect.read_tx(project.engine) as conn:
        spec = sb.load_spec(dict(sb.stored(conn, sb.KIND, ident).meta or {}))
    return {"estimation_id": ident, "name": name, **checks.swath_settings(spec, direction)}
