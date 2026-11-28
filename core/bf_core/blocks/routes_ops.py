"""/api/v1/blocks — the operations on a block model: prototype, regularization, density, categories (подшаг 17.29; ТЗ
8.7.3, 8.7.5; ADR 0023 §6, §9; e17-api.md §6, §9, §15). The router has no prefix of its own: `blocks/router.py`
includes it under `/blocks`.

  * `POST /blocks/prototype/preview` {size, object_ids | extent, pad, rotation, levels, full} → {grid, parents, disk,
    extent, frame}, synchronously, nothing is written (the window shows the frame and nx·ny·nz). 404 `object_not_found
    {param}`; 422 `bad_param {param}`, `no_extent`, `subblock_div`, `block_model_too_large`;
  * `POST /blocks/prototype` — the same body and {name, folder} → {source, id} of the job `blocks.prototype` (quick);
  * `POST /blocks/{id}/regularize` {size, grades, density, categories, name, folder, expected_version?} → {source,
    id} of `blocks.regularize` (heavy). Checked before the job: the model (404), the version when it is given (409
    `version_conflict {version}`), the request (`regularize.make_plan`: 422 `bad_param {param}`, `variable_grid`,
    `block_model_empty`, `block_model_too_large`; 404 `block_field_not_found`);
  * `POST /blocks/{id}/density` {expected_version, method, value | field | table | expr, default, out, src_field,
    filter_id?, restrict?, replace?, unit?, description?} → {source, id} of `blocks.density` (quick);
  * `POST /blocks/{id}/classify` {expected_version, rules | scheme, overrides, majority, estimate, out, filter_id?,
    restrict?, replace?, description?} → {source, id} of `blocks.classify` (quick).
«Перестроить» of a regularized model (`POST /blocks/{id}/rebuild`, `fill/routes.py`): this module puts its maker into
`fill.routes.REBUILD["regularize"]` when it is loaded (the source model is the one of `inputs.model`; the regularized
model is rewritten in place; 409 `source_lost` when the source is gone).
The two of the fields are checked before the job as the job checks them: the shapes (422 `bad_param {param}`,
`scheme_incomplete`), the model (404; 409 `version_conflict {version}`; 422 `block_model_empty`), the names (409
`field_exists {field}` without `replace`), the fields of the rule (422 `density_field_missing`,
`classify_field_missing`, `expr_*` with `pos`), the scheme (404 `scheme_not_found`), the wireframes of the categories
(404 `wireframe_not_found {param: "overrides.N.wireframe_id"}`), the filter (404 `filter_not_found`) and the fields it
reads (422 `filter_field_missing {field, model}`). Everything that writes — 423 `project_read_only` in a project «только
просмотр». The job not registered in this build — 422 `not_ready`. Plain `def` routes run in the thread pool.
"""

from __future__ import annotations

from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[Any, Body()]
# = the KIND of `jobs.py` / `jobs_fields.py` (these modules load the jobs only when they are needed)
KIND_PROTOTYPE, KIND_REGULARIZE = "blocks.prototype", "blocks.regularize"
KIND_DENSITY, KIND_CLASSIFY = "blocks.density", "blocks.classify"
MAX_TITLE = 400


def _body(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие из окна операции.")
    return raw


def _submit(s: Scheduler, kind: str, params: dict[str, Any], title: str) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS

    if kind not in KINDS:
        raise ApiError(422, "not_ready", "Операция ещё не подключена в этой сборке.", "Обновите программу.")
    key, job_id = s.submit(kind, params, title=title)
    return {"source": key, "id": job_id}


def _label(raw: dict[str, Any], key: str) -> str | None:
    """`name` / `folder` of the request: a text or nothing (422 `bad_param {param}`)."""
    value = raw.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ApiError(422, "bad_param", "Ожидается текст.", "Введите значение текстом.", {"param": key})
    text = " ".join(value.split())
    if len(text) > MAX_TITLE:
        raise ApiError(422, "bad_param", f"Не больше {MAX_TITLE} знаков.", "Сократите текст.", {"param": key})
    return text or None


def _nothing(chunk: Any) -> Any:
    """The share of a wireframe of a category when only the rules are checked: not computed."""
    return chunk


def _open(conn: sa.Connection, folder: Any, model_id: int) -> Any:
    from bf_core.storage.blobs import BlobStore

    from .model import ANY, Model

    return Model.open(conn, BlobStore(folder), model_id, ANY)


# --- the prototype ----------------------------------------------------------------------------------------------------


def _plan(conn: sa.Connection, raw: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """(the plan, the params of the job) of a request of the prototype: the box of the objects is resolved here."""
    from . import jobs as bj
    from . import prototype as pr

    object_ids = raw.get("object_ids")
    if object_ids is not None and not isinstance(object_ids, list):
        raise pr.bad_param("object_ids", "Список объектов для охвата задан неверно.", "Выберите объекты для охвата.")
    box = pr.resolve_extent(conn, object_ids, bj.numbers(raw.get("extent")))
    params = {"size": raw.get("size"), "extent": box, "pad": raw.get("pad", 0.0), "rotation": raw.get("rotation", 0.0),
              "levels": raw.get("levels"), "full": raw.get("full", False)}  # fmt: skip
    plan = bj.plan_of(params)
    return plan, {**params, "extent": box}


@router.post("/prototype/preview")
def prototype_preview(svc: Svc, body: Payload) -> dict[str, Any]:
    raw = _body(body)
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        plan, _ = _plan(conn, raw)
    return dict(plan.preview())


@router.post("/prototype")
def prototype(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    raw = _body(body)
    with dialect.read_tx(project.engine) as conn:
        plan, params = _plan(conn, raw)
    name = _label(raw, "name")
    params = {**params, "name": name, "folder": _label(raw, "folder"), "parents": plan.parents}
    return _submit(s, KIND_PROTOTYPE, params, f"Прототип: {name or 'блочная модель'}")


# --- the regularization -----------------------------------------------------------------------------------------------


@router.post("/{bm_id}/regularize")
def regularize(svc: Svc, s: Sched, bm_id: int, body: Payload) -> dict[str, Any]:
    from . import jobs as bj
    from . import regularize as rg
    from .fields_store import version_conflict

    project = svc.require_current(writable=True)
    raw = _body(body)
    version = raw.get("expected_version")
    size, density = bj.numbers(raw.get("size")), bj.numbers(raw.get("density"))
    with dialect.read_tx(project.engine) as conn:
        model = _open(conn, project.folder, bm_id)
        if version is not None and (isinstance(version, bool) or version != model.version):
            raise version_conflict(model.version)
        rg.make_plan(model, size, raw.get("grades"), density, raw.get("categories"))
    params = {"model_id": bm_id, "size": size, "grades": raw.get("grades"), "density": density,
              "categories": raw.get("categories"), "name": _label(raw, "name"), "folder": _label(raw, "folder"),
              "rebuild": None, "rows": model.rows}  # fmt: skip
    return _submit(s, KIND_REGULARIZE, params, f"Регуляризация: «{model.name}»")


def _rebuild_regularize(
    s: Scheduler, conn: sa.Connection, bm_id: int, row: Any, meta: dict[str, Any]
) -> dict[str, Any]:
    """`REBUILD["regularize"]`: the job again on `source.params` and the source model, the model in place."""
    from . import meta as bmeta
    from .fill import commit
    from .fill import routes as fill_routes

    sources = (meta.get("inputs") or {}).get("model") or []
    if not sources:
        raise fill_routes._not_rebuildable()  # noqa: SLF001
    try:
        src = fill_routes._model(conn, int(sources[0]["id"]))  # noqa: SLF001
    except ApiError:
        raise commit.source_lost("исходная модель удалена") from None
    p = meta["source"]["params"]
    params = {"model_id": int(sources[0]["id"]), "size": p["size"], "grades": p.get("grades"),
              "density": p.get("density"), "categories": p.get("categories"), "name": None, "folder": None,
              "rebuild": {"model_id": bm_id, "expected_version": int(row.version)},
              "rows": bmeta.upgrade(src.meta)["rows"]}  # fmt: skip
    return _submit(s, KIND_REGULARIZE, params, f"Перестроить: «{row.name}»")


def _register() -> None:
    from .fill import routes as fill_routes

    fill_routes.REBUILD["regularize"] = _rebuild_regularize


_register()


# --- the fields: density and categories -------------------------------------------------------------------------------


def _checked(conn: sa.Connection, project: Any, bm_id: int, com: Any, names: list[str]) -> tuple[Any, Any]:
    """The model of a request of a field and its restrictor: 404, 409, 422 (the module text)."""
    from . import calc_job
    from .fields_store import version_conflict
    from .jobs_fields import check_names, check_restrictor

    model = _open(conn, project.folder, bm_id)
    if model.version != com.expected_version:
        raise version_conflict(model.version)
    if not model.rows:
        raise ApiError(422, "block_model_empty", "В модели нет блоков.", "Заполните прототип каркасами.")
    check_names(model, names, com.replace)
    tree = calc_job.rules_tree(conn, com.filter_id, com.restrict)
    check_restrictor(model, tree)
    return model, tree


@router.post("/{bm_id}/density")
def density(svc: Svc, s: Sched, bm_id: int, body: Payload) -> dict[str, Any]:
    from . import density as dens
    from .jobs_fields import parse_common

    project = svc.require_current(writable=True)
    raw = _body(body)
    com = parse_common(raw)
    spec = dens.parse(raw)
    with dialect.read_tx(project.engine) as conn:
        model, _ = _checked(conn, project, bm_id, com, [spec.out, spec.src_field])
        dens.prepare(model, spec)
    params = {**spec.params(), "model_id": bm_id, "expected_version": com.expected_version,
              "filter_id": com.filter_id, "restrict": com.restrict, "replace": com.replace, "unit": com.unit,
              "description": com.description, "rows": model.rows}  # fmt: skip
    return _submit(s, KIND_DENSITY, params, f"Плотность: {spec.out} · {model.name}")


@router.post("/{bm_id}/classify")
def classify(svc: Svc, s: Sched, bm_id: int, body: Payload) -> dict[str, Any]:
    from bf_core.wireframes import store as wf

    from . import categories as cat
    from . import schemes
    from .jobs_fields import parse_common

    project = svc.require_current(writable=True)
    raw = _body(body)
    com = parse_common(raw)
    with dialect.read_tx(project.engine) as conn:
        spec = cat.parse(raw, lambda sid: schemes.get(conn, sid))
        model, _ = _checked(conn, project, bm_id, com, [spec.out])
        for n, over in enumerate(spec.overrides):
            try:
                wf.wireframe_info(conn, over[0])
            except ApiError as e:
                e.extra = {**e.extra, "param": f"overrides.{n}.wireframe_id"}
                raise
        cat.prepare(model, spec, [_nothing] * len(spec.overrides))
    params = {**spec.params(), "model_id": bm_id, "expected_version": com.expected_version,
              "filter_id": com.filter_id, "restrict": com.restrict, "replace": com.replace,
              "description": com.description, "rows": model.rows}  # fmt: skip
    return _submit(s, KIND_CLASSIFY, params, f"Категории: {spec.out} · {model.name}")
