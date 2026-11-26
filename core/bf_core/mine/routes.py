"""/api/v1/pit — оптимизация карьера (ТЗ 8.11; ADR 0028 «API», решения 20, 28, 30; подшаг 21.11;
docs/progress/e21-api.md §2–§6). The paths are relative to `/pit` (`router.py`):

  * `GET /defaults?model_id=` → {params, choices, limits, ranges, model?}: the form of a new scenario; with a model —
    the elements, the density, the categories, Z_ref, K by the step of the grid, the DTMs of the project, the grid.
  * `POST /validate` {params} → {ready, missing, warnings} (synchronous; the one source of «Запустить»).
  * `POST /check` {params} → {source, id} of the job `pit.check` (quick). 404 `block_model_not_found`; 422 `bad_param`,
    `variable_grid`; 423 (a project «только просмотр» has no scheduler: the job cannot run there).
  * `GET /scenarios` → {scenarios [{id, name, version, state, has_result, params_changed, model, shells, value, graph,
    created_at, updated_at, job_id}]}; `POST /scenarios` {name?, params?} → {id, version, name}; `GET /scenarios/{id}`;
    `PUT /scenarios/{id}` {name?, params?, calendar?, expected_version} → {id, version, name, params_changed};
    `DELETE /scenarios/{id}` → {id, deleted: true}. 404 `pit_scenario_not_found`, `block_model_not_found`; 409
    `pit_running`, `version_conflict {version}`, `name_taken`; 422 `bad_param`, `too_many_scenarios`; 423.
  * `POST /scenarios/{id}/run` {solver?} → {source, id} of the job `pit.run` (heavy). Refused before the job: 404; 409
    `pit_running`, `pit_fields_taken`; 422 `pit_not_ready {missing, param}`, `variable_grid`, `bad_param`,
    `filter_field_missing`; 423. What only the data tells comes from the job: `pit_density_missing`, `pit_empty`,
    `pit_too_large`, `pit_internal`, `model_changed`.
  * Events `pit.changed {kind: "pit_scenario", id, deleted?}` after a create, an edit, a rename and a deletion (the job
    publishes its own with its result).

The template, the report, the surfaces, the solid and the comparison — `routes_extra.py`. Plain `def` routes run in the
thread pool; numpy, pyarrow and the kernels are imported inside the functions (nothing heavy loads with this module).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.geostat.estimate.routes_common import Payload, Sched, Svc, body_of, registered

from . import spec

router = APIRouter()
EVENT = "pit.changed"
KIND_RUN = "pit.run"
KIND_CHECK = "pit.check"
TITLE_RUN = "Оптимизация карьера"
TITLE_CHECK = "Проверка шаблона карьера"


def publish(request: Request, scenario_id: int, deleted: bool = False) -> None:
    """`pit.changed {kind: "pit_scenario", id, deleted?}`."""
    data: dict[str, Any] = {"kind": "pit_scenario", "id": scenario_id}
    if deleted:
        data["deleted"] = True
    request.app.state.core.events.publish(EVENT, data)


def submit(s: Any, kind: str, params: dict[str, Any], title: str) -> tuple[str, int]:
    """`Scheduler.submit`; a project opened «только просмотр» is not attached to the scheduler (the job rows are in the
    project): 423 `project_read_only` in Russian, not a 500."""
    try:
        return s.submit(kind, params, title=title)  # type: ignore[no-any-return]
    except LookupError:
        raise spec.refuse(
            "project_read_only",
            "Проект открыт только для чтения: задание в нём не запускается.",
            "Откройте проект для записи: закройте его в другом окне BedrockForge и откройте заново.",
        ) from None


# --- the form ---------------------------------------------------------------------------------------------------------


def model_form(conn: Any, store: Any, model_id: int) -> dict[str, Any]:
    """`defaults.model`: what the window fills its form with for a model (e21-api §2)."""
    from bf_core.blocks.model import Model
    from bf_core.wireframes import store as wf

    from . import plan

    model = Model.open(conn, store, model_id)  # 404 `block_model_not_found`
    k_auto = None
    if model.grid.tensors is None:  # the steps of the parents; K for the default angle (45°) — the window shows it
        from .template import auto_k

        dx, dy, dz = model.grid.regular()
        k_auto = auto_k(dx, dy, dz, 45.0)
    terrains = [{"id": int(w["id"]), "name": str(w["name"])} for w in wf.list_wireframes(conn) if w.get("dtm")]
    return {**spec.model_defaults(model.fields, plan.z_range_of(model)), "k_auto": k_auto, "terrains": terrains,
            "grid": dict(model.meta.get("grid") or {})}  # fmt: skip


@router.get("/defaults")
def defaults(svc: Svc, model_id: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    out: dict[str, Any] = {**spec.defaults_json(), "ranges": spec.ranges_json()}
    if model_id is not None:
        project = svc.require_current()
        with dialect.read_tx(project.engine) as conn:
            out["model"] = model_form(conn, BlobStore(project.folder), model_id)
    return out


@router.post("/validate")
def validate(svc: Svc, body: Payload) -> dict[str, Any]:
    from bf_core.blocks.model import Model
    from bf_core.storage.blobs import BlobStore

    sp = spec.parse_check(body_of(body))  # 422 `bad_param`
    ctx = None
    if sp.model_id is not None:
        project = svc.require_current()
        with dialect.read_tx(project.engine) as conn:
            ctx = {"fields": Model.open(conn, BlobStore(project.folder), sp.model_id).fields}  # 404
    return spec.validate(sp, ctx)


@router.post("/check")
def check(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from bf_core.blocks.model import Model
    from bf_core.storage.blobs import BlobStore

    from . import plan

    raw = body_of(body)
    sp = spec.parse_check(raw)
    if sp.model_id is None:
        raise ApiError(422, "bad_param", "Не выбрана блочная модель.",
                       "Выберите модель, на сетке которой проверять шаблон.", {"param": "model_id"})  # fmt: skip
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        model = Model.open(conn, BlobStore(project.folder), sp.model_id)  # 404 `block_model_not_found`
        plan.check_grid(model)  # 422 `variable_grid`
        name = model.name
    registered(KIND_CHECK, TITLE_CHECK)
    key, job_id = submit(s, KIND_CHECK, {"params": raw["params"]}, f"{TITLE_CHECK} · {name}")
    return {"source": key, "id": job_id}


# --- the scenarios ----------------------------------------------------------------------------------------------------


@router.get("/scenarios")
def list_scenarios(svc: Svc) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"scenarios": st.list_scenarios(conn)}


@router.post("/scenarios")
def create_scenario(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    from bf_core.drillholes.edit import author
    from bf_core.storage.blobs import BlobStore

    from . import plan
    from . import store as st

    req = spec.parse_scenario_create(body_of(body))
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        st.check_room(conn)  # 422 `too_many_scenarios`
        params = plan.normalize(conn, BlobStore(project.folder), req.params)  # 404 `block_model_not_found`
        out = st.create(conn, req.name, params, author=author())
    publish(request, int(out["id"]))
    return out


@router.get("/scenarios/{scenario_id}")
def get_scenario(svc: Svc, scenario_id: int) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        info = st.info(conn, scenario_id, BlobStore(project.folder))  # 404 `pit_scenario_not_found`
    info.pop("size", None)
    return info


@router.put("/scenarios/{scenario_id}")
def update_scenario(svc: Svc, request: Request, scenario_id: int, body: Payload) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import plan
    from . import store as st

    req = spec.parse_scenario_update(body_of(body))
    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        params = None if req.params is None else plan.normalize(conn, BlobStore(project.folder), req.params)
        out = st.update_params(conn, scenario_id, req.expected_version, params=params,
                               calendar=None if req.calendar is None else req.calendar.canonical(),
                               name=req.name)  # fmt: skip
    publish(request, scenario_id)
    return out


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(svc: Svc, request: Request, scenario_id: int) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        st.delete(conn, scenario_id)  # 404, 409 `pit_running`
    publish(request, scenario_id, deleted=True)
    return {"id": scenario_id, "deleted": True}


# --- the run ----------------------------------------------------------------------------------------------------------


def check_run(conn: Any, store: Any, scenario_id: int, params: dict[str, Any]) -> tuple[str, int, dict[str, int]]:
    """What the job would refuse before it reads the data: the model and its fields (404, 422), the grid and its size
    (422 `variable_grid`, `pit_too_large`: the plan, the columns, the z-grids of the shells), the names of the fields
    PIT and ORE (409), the filter, the surface and the fields of the rules, the readiness of the scenario (422
    `pit_not_ready`). → (the name of the model, the version of the scenario, the sizes for the estimate of the memory
    of the job: `limits.hints`)."""
    from bf_core.blocks import calc_job, restrict
    from bf_core.blocks.filters import schema as fs
    from bf_core.blocks.model import Model
    from bf_core.wireframes import store as wf

    from . import fields, limits, plan
    from . import store as st
    from .store_graph import rock_rules

    row = st._row(conn, scenario_id)  # noqa: SLF001 - 404 `pit_scenario_not_found`
    st._writable(conn, row)  # noqa: SLF001 - 409 `pit_running`
    sp0 = spec.parse_params(params)
    if sp0.model_id is None:
        spec.require_ready(sp0)  # 422 `pit_not_ready`: the model is the first thing it names
    assert sp0.model_id is not None
    model = Model.open(conn, store, sp0.model_id)  # 404 `block_model_not_found`
    plan.check_grid(model)  # 422 `variable_grid`
    sp = plan.resolve(model, sp0)  # 404 `block_field_not_found`, 422 `bad_param`
    limits.check_size(model.grid.counts, len(sp.rf))  # 422 `pit_too_large`
    fields.check_names(model.meta, sp.prefix, scenario_id)  # 409 `pit_fields_taken`
    if sp.terrain_id is not None:
        wf.wireframe_info(conn, int(sp.terrain_id))  # 404 `wireframe_not_found`
    trees = [t for t in (calc_job.rules_tree(conn, sp.filter_id, sp.restrict), *rock_rules(sp.canonical())) if t]
    have = {str(f["name"]).casefold() for f in model.fields}
    for tree in trees:
        for name in fs.fields_of(fs.normalize_rules(tree)):
            if name.casefold() not in have:
                raise restrict.field_missing(name, model.name)
    spec.require_ready(sp, plan.context(model))  # 422 `pit_not_ready {missing, param}`
    return model.name, int(row.version), limits.hints(model.grid.counts, len(sp.rf))


@router.post("/scenarios/{scenario_id}/run")
def run_scenario(svc: Svc, s: Sched, scenario_id: int, body: Payload) -> dict[str, Any]:
    from bf_core.storage.blobs import BlobStore

    from . import store as st

    req = spec.parse_run(body_of(body))
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        params = st.current_params(conn, scenario_id)  # 404
        _model_name, version, size = check_run(conn, BlobStore(project.folder), scenario_id, params)
        title = st._row(conn, scenario_id).name  # noqa: SLF001
    registered(KIND_RUN, TITLE_RUN)
    job = {"scenario_id": scenario_id, "params": params, "name": title, "expected_version": version,
           "solver": req.solver, **size}  # fmt: skip
    key, job_id = submit(s, KIND_RUN, job, f"{TITLE_RUN} · {title}")
    return {"source": key, "id": job_id}
