"""/api/v1/geostat/variography/models — stored variogram models (ADR 0022 §12, §14; e16-api.md «Модели вариограмм»).

  * `GET /models[?set_id]` → {models: [{id, name, set_id, table, field, transform, type, color, sill, structures,
    stale, stale_reason, source_lost, created_at, updated_at}]}.
  * `POST /models` {name?, model, exp_key, color?, fit?, back?} → {id, name}: the model with a copy of the points of
    the calculation `exp_key` (`store.stage`). 409 `name_taken` {param}; 404 `result_not_found`; 423.
  * `GET /models/{id}` → {id, name, color, meta, points, stale, stale_reason, source_lost, version, …}; 404.
  * `PUT /models/{id}` {expected_version, name?, model?, exp_key?, color?, fit?, back?} → {id, name, version};
    `expected_version` (required, the `version` of `GET`) not the stored one — 409 `version_conflict` {version};
    404; 409 `name_taken`; 422 `bad_param {param: "expected_version"}`; 423.
  * `DELETE /models/{id}` → {deleted: id}; 404; 423.
  * `GET /models/{id}/export?format=json|gslib` → {format, filename, text}: reading only.
  * `POST /models/{id}/export` {format?, path, overwrite?} → {format, path, bytes}: the file written. `path` —
    absolute (Cyrillic allowed), its extension made that of the format (`.json` / `.par`); an existing file without
    `overwrite: true` — 409 `files_exist` {files: [the file name]}; inside the folder of the project — 422
    `bad_path` {param: "path"}; 422 `bad_param` {param: "path" | "format" | "overwrite"}.
  * `POST /models/import` {format?, text? | path?} → {format, model}: the working model of the window, not
    stored; a broken file — 422 `bad_file` {line?} with a hint.

  * `POST /models/{id}/rebuild` → {source, id} of the job `variography.rebuild` (`jobs_map.py`, 16.15): the points
    on the current data with the parameters of the calc; the model is not changed, `fit.residual` and `back`
    follow the new points. 409 `source_lost`; 404; 423; 422 `not_ready` (the job not in this build).

Every write publishes `geostat.changed {kind: "variogram_model", id, deleted?}` (the rebuild — through the result of
its job, `events`). `exp_key` of a record of the map (`variography.map`) — 422 `bad_param {param: "exp_key"}`.
Plain `def` routes run in the thread pool.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService
from bf_core.storage.blobs import BlobStore

from . import store as ms  # `exchange` (numpy) is imported in the handlers: nothing heavy before BF-READY

router = APIRouter(prefix="/variography")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
KIND_REBUILD = "variography.rebuild"  # = jobs_map.KIND_REBUILD
Payload = Annotated[Any, Body()]
EVENT = "geostat.changed"
IMPORT_MAX = 1 << 20  # bytes of a model file read by `import` (a GSLIB block or a JSON of a model is < 64 КБ)


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна вариографии.")
    return raw


def _publish(request: Request, model_id: int, deleted: bool = False) -> None:
    data: dict[str, Any] = {"kind": ms.KIND, "id": model_id}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish(EVENT, data)


def _model(raw: Any, param: str = "model") -> tuple[str, dict[str, Any]]:
    """(type, the canonical model of the API) of a request; `bad_param {param: "model.structures.1.a2"}`."""
    from . import exchange

    try:
        kind, model = exchange.api_model(raw, param)
        return kind, exchange.model_form(kind, model)
    except ValueError as e:
        raise ms.as_api(e) from e


def _stage(store: BlobStore, folder: Path, key: Any) -> ms.Staged:
    """The files of the calc `key` for a model; a record of the map is not a calc of the axes."""
    staged = ms.stage(store, folder, key)
    if not isinstance(staged.record["result"].get("directions"), list):
        staged.discard()
        raise ms.bad("Ключ — расчёт веера или карты, а не вариограмм по осям.",
                     "Сохраните модель из расчёта вариограмм по осям.", "exp_key")  # fmt: skip
    return staged


def _author() -> str | None:
    from bf_core.drillholes.edit import author

    return author()


@router.get("/models")
def list_models(svc: Svc, set_id: Annotated[int | None, Query()] = None) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"models": ms.list_models(conn, set_id)}


@router.post("/models")
def create_model(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current(writable=True)
    model = _model(raw.get("model"))
    ms.check_key(raw.get("exp_key"))
    for key in ("color", "fit", "back"):  # refused before the files are staged
        getattr(ms, f"clean_{key}")(raw.get(key))
    store = BlobStore(project.folder)
    staged = _stage(store, project.folder, raw.get("exp_key"))
    try:
        with dialect.write_tx(project.engine) as conn:
            out = ms.create(conn, store, staged, model, name=raw.get("name"), color=raw.get("color"),
                            fit=raw.get("fit"), back=raw.get("back"), author=_author())  # fmt: skip
    finally:
        staged.discard()  # placed files are gone from `.tmp` already
    _publish(request, out["id"])
    return out


@router.post("/models/import")
def import_model(svc: Svc, body: Payload) -> dict[str, Any]:
    """The model of a file (JSON or GSLIB) for the window; nothing is stored."""
    from . import exchange

    raw = _body(body)
    svc.require_current()
    fmt = raw.get("format") or None
    text, path = raw.get("text"), raw.get("path")
    if text is not None and path not in (None, ""):
        raise ms.bad("Задайте что-то одно: текст или путь к файлу.", "Выберите файл модели вариограммы.", "path")
    if text is None and path in (None, ""):
        raise ms.bad("Не задан файл модели.", "Выберите файл модели вариограммы.", "path")
    data: Any = text
    if text is None:
        data = _read(path)
    elif not isinstance(text, str):
        raise ms.bad("Текст модели задан неверно.", "Выберите файл модели вариограммы.", "text")
    try:
        if fmt is None:
            fmt = exchange.detect_format(data)
        fields = exchange.read_model(data, fmt)
        kind, model = exchange.api_model(fields)
    except ValueError as e:
        raise ms.as_api(e) from e
    return {"format": str(fmt).strip().lower(), "model": exchange.model_form(kind, model)}


def _target(path: Any) -> Path:
    if not isinstance(path, str) or not path.strip() or not Path(path).is_absolute():
        raise ms.bad("Путь к файлу должен быть полным.", "Выберите файл в окне сохранения.", "path")
    return Path(path)


def _read(path: Any) -> bytes:
    target = _target(path)
    try:
        if target.stat().st_size > IMPORT_MAX:
            raise ms.bad("Файл слишком большой для модели вариограммы.", "Выберите файл модели (.json, .par).",
                         "path")  # fmt: skip
        return target.read_bytes()
    except OSError as e:
        raise ms.bad("Файл модели не найден или не читается.", "Выберите файл заново.", "path") from e


def _write(target: Path, data: bytes) -> None:
    if not target.parent.is_dir():
        raise ms.bad("Папка для файла не найдена.", "Выберите папку заново.", "path")
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with open(tmp, "wb") as fh:  # bytes: «\n» stays «\n» on Windows (the text is read back bit for bit)
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except OSError as e:
        raise ms.bad("Файл не записан: нет доступа к папке или файл открыт в другой программе.",
                     "Выберите другое место или закройте файл.", "path") from e  # fmt: skip
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/models/{model_id}")
def get_model(svc: Svc, model_id: int) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return ms.get(conn, BlobStore(project.folder), model_id)


@router.put("/models/{model_id}")
def put_model(svc: Svc, request: Request, model_id: int, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        now = ms.versioned(conn, model_id)[2]  # 404 first
    version = ms.clean_version(raw.get("expected_version"))
    if now != version:  # 409 before the files are staged
        raise ms.conflict(now)
    model = None if raw.get("model") is None else _model(raw["model"])
    for key in ("color", "fit", "back"):
        getattr(ms, f"clean_{key}")(raw.get(key))
    store = BlobStore(project.folder)
    staged = None if raw.get("exp_key") is None else _stage(store, project.folder, raw["exp_key"])
    try:
        with dialect.write_tx(project.engine) as conn:
            out = ms.update(conn, store, model_id, expected_version=version, name=raw.get("name"), model=model,
                            staged=staged, color=raw.get("color"), fit=raw.get("fit"), back=raw.get("back"),
                            has=frozenset(k for k in ("fit", "back") if k in raw))  # fmt: skip
    finally:
        if staged is not None:
            staged.discard()
    _publish(request, model_id)
    return out


@router.delete("/models/{model_id}")
def delete_model(svc: Svc, request: Request, model_id: int) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        ms.delete(conn, model_id)
    _publish(request, model_id, deleted=True)
    return {"deleted": model_id}


def _export_text(svc: Svc, model_id: int, format: Any) -> tuple[str, str, str, Path]:  # noqa: A002
    """(format, file name, text, folder of the project) of the export of a model."""
    from . import exchange

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        name, meta = ms.meta_of(conn, model_id)
    try:
        fmt = str(format or "json").strip().lower()
        text = exchange.write_model(meta, fmt)
        filename = exchange.file_name(name, fmt)
    except ValueError as e:
        raise ms.as_api(e) from e
    return fmt, filename, text, Path(project.folder)


def export_target(path: Any, fmt: str, folder: Path, overwrite: bool) -> Path:
    """The file of a `POST …/export`: absolute, the extension of the format, outside the project, not replaced
    silently (409 `files_exist` without `overwrite`)."""
    from . import exchange

    target = _target(path)
    ext = exchange.EXTENSIONS[fmt]
    if target.suffix.lower() != ext:
        target = target.with_name(target.name + ext)
    if target.resolve().is_relative_to(folder.resolve()):
        raise ms.bad("Нельзя сохранить файл внутрь папки проекта.", "Выберите другую папку, например «Документы».",
                     "path", code="bad_path")  # fmt: skip
    if not target.parent.is_dir():
        raise ms.bad("Папка для файла не найдена.", "Выберите папку заново.", "path")
    if not overwrite and (target.exists() or target.is_symlink()):
        raise ApiError(409, "files_exist", f"Файл «{target.name}» уже есть.",
                       "Подтвердите замену или выберите другое имя файла.", {"files": [target.name]})  # fmt: skip
    return target


@router.get("/models/{model_id}/export")
def export_model(svc: Svc, model_id: int, format: str = "json") -> dict[str, Any]:  # noqa: A002 — the name in the API
    fmt, filename, text, _folder = _export_text(svc, model_id, format)
    return {"format": fmt, "filename": filename, "text": text}


@router.post("/models/{model_id}/export")
def export_model_file(svc: Svc, model_id: int, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    overwrite = raw.get("overwrite", False)
    if not isinstance(overwrite, bool):
        raise ms.bad("Флаг замены файла задан неверно.", "Повторите сохранение.", "overwrite")
    fmt, _filename, text, folder = _export_text(svc, model_id, raw.get("format"))
    target = export_target(raw.get("path"), fmt, folder, overwrite)
    data = text.encode("utf-8")
    _write(target, data)
    return {"format": fmt, "path": str(target), "bytes": len(data)}


@router.post("/models/{model_id}/rebuild")
def rebuild_model(svc: Svc, s: Sched, model_id: int) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS as REGISTERED
    from bf_core.stats import source

    from . import jobs_map
    from . import spec as sp

    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        name, meta = ms.meta_of(conn, model_id)
        body = jobs_map.rebuild_body(conn, meta)
        spec = sp.parse_experimental(conn, body)
        rows = next((t.rows for t in source.tables(conn, spec.data.set_id) if t.id == spec.data.table_id), 0)
    if KIND_REBUILD not in REGISTERED:
        raise ApiError(422, "not_ready", "«Перестроить» ещё не подключено в этой сборке.", "Обновите программу.")
    params = {"model_id": model_id, "rows": int(rows), "dirs": 3 if spec.axes is not None else len(spec.directions),
              "nlag": max(spec.lags.nlag, spec.dh.nlag)}  # fmt: skip
    key, job_id = s.submit(KIND_REBUILD, params, title=f"Перестроить модель: {name}")
    return {"source": key, "id": job_id}
