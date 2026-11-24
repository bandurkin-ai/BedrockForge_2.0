"""/api/v1/blocks — filling and coding by wireframes, «Перестроить» (подшаг 17.20; ADR 0023 §6, §14;
docs/progress/e17-api.md §7). The router is included by `blocks/router.py` (prefix `/blocks`).

  * `POST /blocks/fill` {prototype_id, wireframes: [{id, code?, priority?}], mode, levels, m, outside_code,
    code_field, frac_field, name, folder} → {source, id} of the job `blocks.fill` (queue heavy). Checked here, before
    the job: 404 `block_model_not_found`, `wireframe_not_found {param: "wireframes.N.id"}`; 422 `not_prototype`,
    `bad_param {param}`, `fill_mode`, `subblock_div`, `wireframe_open {param, name}` (by the stored stats),
    `variable_grid`; 423 `project_read_only`. What the geometry tells (`wireframe_orientation`, an open body the
    stats did not know) — the failure of the job with that code.
  * `POST /blocks/{id}/code` {expected_version, wireframes, mode, split, m, outside_code, code_field, frac_field, name}
    → {source, id} of the job `blocks.code`: `split: false` — the fields of the same model, `true` — a new model with
    the sub-blocks of the boundary rows. 409 `version_conflict {version}`, `field_exists {field}`; 422 `frac_exists`,
    `block_model_empty`.
  * `POST /blocks/{id}/rebuild` {expected_version} → {source, id}: the job of the maker of the model (`fill`, `code`
    with splitting) on `source.params`, the model is rewritten in place (version + 1); the fields of other makers
    are dropped, the job lists them (`dropped`). 409 `version_conflict {version}`, `source_lost`; 422 `not_rebuildable`.
    Another maker is added to `REBUILD` (`regularize`, 17.26).
The job not registered in this build — 422 `not_ready`. No project — 409 `no_project`. Plain `def` routes run in the
thread pool; numpy and the kernels load with the first job, not with this module.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Body, Depends

from bf_core.api.errors import ApiError
from bf_core.blocks import meta as bmeta
from bf_core.blocks import schema as bs
from bf_core.blocks.fields_store import version_conflict
from bf_core.blocks.fill import commit
from bf_core.blocks.fill.spec import FillSpec, parse_code, parse_fill
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.scheduler import Scheduler
from bf_core.jobs.service import jobs
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter(prefix="/blocks")
Svc = Annotated[ProjectService, Depends(projects)]
Sched = Annotated[Scheduler, Depends(jobs)]
Payload = Annotated[Any, Body()]
KIND_FILL = "blocks.fill"  # = jobs.KIND_FILL (the module of the job registers the handler: not imported here)
KIND_CODE = "blocks.code"  # = jobs.KIND_CODE


def _model(conn: sa.Connection, model_id: int) -> Any:
    from bf_core.blocks.store import not_found

    row = conn.execute(
        sa.select(objects.c.name, objects.c.version, objects.c.meta).where(
            objects.c.kind == bs.KIND, objects.c.id == model_id, objects.c.state == "ready"
        )
    ).first()
    if row is None:
        raise not_found()
    return row


def _submit(s: Scheduler, kind: str, params: dict[str, Any], title: str) -> dict[str, Any]:
    from bf_core.jobs.registry import KINDS

    if kind not in KINDS:
        raise ApiError(422, "not_ready", "Операция ещё не подключена в этой сборке.", "Обновите программу.")
    key, job_id = s.submit(kind, params, title=title)
    return {"source": key, "id": job_id}


def _tris(infos: list[dict[str, Any]]) -> int:
    return sum(int(i.get("triangle_count") or 0) for i in infos)


@router.post("/fill")
def fill(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    import dataclasses

    from bf_core.blocks.prototype import prototype_info

    spec = parse_fill(body)
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        row = _model(conn, spec.source_id)
        info = prototype_info(row.meta)
        if info is None:
            raise commit.not_prototype()
        grid: bs.GridV2 = info["grid"]
        if spec.levels is not None:
            grid = dataclasses.replace(grid, sub=bs.SubblockSpec.of_levels(spec.levels))
        grid.regular("Заполнение")
        infos = commit.wire_infos(conn, spec.wires)
    params = {"spec": spec.to_params(), "rebuild": None, "parents": grid.cells, "triangles": _tris(infos), "rows": 0}
    return _submit(s, KIND_FILL, params, f"Заполнение: «{row.name}»")


@router.post("/{bm_id}/code")
def code(svc: Svc, s: Sched, bm_id: int, body: Payload) -> dict[str, Any]:
    spec = parse_code(bm_id, body)
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        row = _model(conn, bm_id)
        if int(row.version) != spec.expected_version:
            raise version_conflict(int(row.version))
        meta = bmeta.upgrade(row.meta)
        if not meta["rows"]:
            raise ApiError(
                422, "block_model_empty", "В модели нет блоков: кодировать нечего.",
                "Заполните прототип каркасами (команда «Заполнить каркасами»).",
            )  # fmt: skip
        bmeta.grid_of(meta).regular("Кодирование")
        commit.check_names(meta["fields"], spec)
        infos = commit.wire_infos(conn, spec.wires)
    params = {"spec": spec.to_params(), "rebuild": None, "parents": meta["parents"], "triangles": _tris(infos),
              "rows": meta["rows"]}  # fmt: skip
    return _submit(s, KIND_CODE, params, f"Кодирование: «{row.name}»")


# --- «Перестроить» ---------------------------------------------------------------------------------------------------


def _wires_alive(conn: sa.Connection, spec: FillSpec) -> list[dict[str, Any]]:
    try:
        return commit.wire_infos(conn, spec.wires)
    except ApiError as e:
        if e.code != "wireframe_not_found":
            raise
        n = int(str(e.extra.get("param", "wireframes.0.id")).split(".")[1])
        raise commit.source_lost(f"каркас № {spec.wires[n].id} удалён") from None


def _rebuild_fill(s: Scheduler, conn: sa.Connection, bm_id: int, row: Any, meta: dict[str, Any]) -> dict[str, Any]:
    spec = FillSpec.of_params(meta["source"]["params"])
    infos = _wires_alive(conn, spec)
    params = {"spec": spec.to_params(), "rebuild": {"model_id": bm_id, "expected_version": int(row.version)},
              "parents": bmeta.grid_of(meta).cells, "triangles": _tris(infos), "rows": 0}  # fmt: skip
    return _submit(s, KIND_FILL, params, f"Перестроить: «{row.name}»")


def _rebuild_code(s: Scheduler, conn: sa.Connection, bm_id: int, row: Any, meta: dict[str, Any]) -> dict[str, Any]:
    spec = FillSpec.of_params(meta["source"]["params"])
    if not spec.split:
        raise _not_rebuildable()
    try:
        src = _model(conn, spec.source_id)
    except ApiError:
        raise commit.source_lost("модель, которую кодировали, удалена") from None
    infos = _wires_alive(conn, spec)
    src_meta = bmeta.upgrade(src.meta)
    params = {"spec": spec.to_params(), "rebuild": {"model_id": bm_id, "expected_version": int(row.version)},
              "parents": src_meta["parents"], "triangles": _tris(infos), "rows": src_meta["rows"]}  # fmt: skip
    return _submit(s, KIND_CODE, params, f"Перестроить: «{row.name}»")


def _not_rebuildable() -> ApiError:
    return ApiError(
        422, "not_rebuildable", "Эту модель нельзя перестроить.",
        "Перестраиваются модели, созданные заполнением, а также кодированием с дроблением.",
    )  # fmt: skip


#: the makers of a model that «Перестроить» knows: `source.op` → (scheduler, conn, id, row, meta) → {source, id}
REBUILD: dict[str, Callable[..., dict[str, Any]]] = {"fill": _rebuild_fill, "code": _rebuild_code}


@router.post("/{bm_id}/rebuild")
def rebuild(svc: Svc, s: Sched, bm_id: int, body: Payload) -> dict[str, Any]:
    version = body.get("expected_version") if isinstance(body, dict) else None
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        row = _model(conn, bm_id)
        if isinstance(version, bool) or not isinstance(version, int) or int(row.version) != version:
            raise version_conflict(int(row.version))
        meta = bmeta.upgrade(row.meta)
        op = (meta["source"] or {}).get("op")
        make = REBUILD.get(str(op))
        if make is None or not (meta["source"] or {}).get("params"):
            raise _not_rebuildable()
        return make(s, conn, bm_id, row, meta)
