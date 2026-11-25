"""/api/v1/geostat — the simulations: «Моделирование» rows, the run, the post-processing (ТЗ 8.8.1; ADR 0024 §9, §11;
подшаг 18.32; the forms — docs/progress/e18-api.md §9).

  * `GET /simulations[?model_id]`, `GET /simulations/{id}` (the form of `contract_answers.simulation`),
    `POST /simulations` (the body of §9), `PUT /simulations/{id}` {expected_version, …}, `DELETE /simulations/{id}` —
    the rows (409 `name_taken`, `version_conflict {version}`); the blob `real` goes with the row.
  * `GET /simulations/{id}/summary` — the summary of the realizations: per realization n, mean, σ, min, max (kept by
    the job in `last_run.summary`), the counts of the run (degenerate systems, nodes with no datum, nodes at a sample),
    the conditioning samples of a node; 409 `no_realizations` before the first run.
  * `POST /simulate/run` {simulation | simulation_id, expected_version?} → {source, id} of the job `simulate.run`;
    refused before it starts: 404, 409 `version_conflict`, `source_lost`, `name_taken`, 422, 423.
  * `POST /simulations/{id}/post` {expected_version (of the model), prefix?, stats?, thresholds?, replace?} →
    {source, id} of the job `simulate.post`; 404, 409 `version_conflict`, `no_realizations`, `field_exists {field}`,
    422, 423. `POST /simulate/post` is the same with `simulation_id` in the body.

The checks go in this order: the shape of the body (422 `bad_request`), the project (409 `no_project`, 423
`project_read_only` for what writes or starts a job), the parameters (422 / 404), the versions and names (409). Writes
publish `geostat.changed {kind: "simulation", id, deleted?}`; the jobs — in their answers (`events`). Plain `def`
routes run in the thread pool; numpy and the kernels load on the first request, not with this module.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from bf_core.db import dialect
from bf_core.geostat.estimate.routes_common import (
    Payload,
    Sched,
    Svc,
    author,
    body_of,
    plain,
    publish,
    registered,
    sample_rows,
)
from bf_core.storage.blobs import BlobStore

router = APIRouter()
KIND_RUN, KIND_POST = "simulate.run", "simulate.post"
KIND = "simulation"


# --- the rows ---------------------------------------------------------------------------------------------------------


@router.get("/simulations")
def list_rows(svc: Svc, model_id: Annotated[int | None, Query()] = None) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"simulations": st.list_rows(conn, model_id)}


@router.get("/simulations/{simulation_id}")
def get_row(svc: Svc, simulation_id: int) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return st.get(conn, simulation_id)


@router.post("/simulations")
def create_row(svc: Svc, request: Request, body: Payload) -> dict[str, Any]:
    from . import store as st
    from . import store_check as sc
    from .spec import parse_simulation

    raw = body_of(body)
    project = svc.require_current(writable=True)
    spec = parse_simulation(raw)
    with dialect.read_tx(project.engine) as conn:
        prepared = sc.prepare(conn, project, spec)  # the samples are read outside the write transaction
    with dialect.write_tx(project.engine) as conn:
        out = st.create(conn, project, spec, author=author(), prepared=prepared)
    publish(request, KIND, out["id"])
    return out


@router.put("/simulations/{simulation_id}")
def put_row(svc: Svc, request: Request, simulation_id: int, body: Payload) -> dict[str, Any]:
    from bf_core.geostat.variogram import store as ms

    from . import store as st
    from . import store_check as sc
    from .spec import parse_simulation

    raw = body_of(body)
    project = svc.require_current(writable=True)
    prepared = None
    with dialect.read_tx(project.engine) as conn:
        row = sc.stored(conn, simulation_id)  # 404 first
        version = ms.clean_version(raw.get("expected_version"))
        if int(row.version) != version:
            raise sc.conflict(int(row.version))
        meta = dict(row.meta or {})
        merged = {k: meta[k] for k in st.KEYS if k in meta}
        merged.update({k: raw[k] for k in st.KEYS if k in raw})
        if any(k in raw for k in st.KEYS):  # the samples are read here, outside the write transaction
            prepared = sc.prepare(conn, project, parse_simulation(merged), old=sc.load_spec(meta),
                                  old_origin=meta.get("origin"))  # fmt: skip
    with dialect.write_tx(project.engine) as conn:
        out = st.update(conn, project, simulation_id, raw, version, prepared=prepared)
    publish(request, KIND, simulation_id)
    return out


@router.delete("/simulations/{simulation_id}")
def delete_row(svc: Svc, request: Request, simulation_id: int) -> dict[str, Any]:
    from . import store as st

    project = svc.require_current(writable=True)
    with dialect.write_tx(project.engine) as conn:
        out = st.delete(conn, simulation_id)
    publish(request, KIND, simulation_id, deleted=True)
    return out


@router.get("/simulations/{simulation_id}/summary")
def summary(svc: Svc, simulation_id: int) -> dict[str, Any]:
    """The summary of the realizations of the last run (module docstring)."""
    from bf_core.api.errors import ApiError

    from . import real
    from . import store as st
    from . import store_check as sc

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        row = sc.stored(conn, simulation_id)
        sha = st.real_sha(conn, simulation_id)
    last = (row.meta or {}).get("last_run")
    if sha is None or not isinstance(last, dict):
        raise ApiError(409, "no_realizations", f"У моделирования «{row.name}» нет реализаций.",
                       "Запустите моделирование и повторите.")  # fmt: skip
    got = last.get("summary")
    if got is None:  # a row of an older run: from the file
        got = real.summary(real.Reals(BlobStore(project.folder), sha))
    return plain({"simulation_id": simulation_id, "nodes": last.get("nodes"), "realizations": last.get("realizations"),
                  "seconds": last.get("seconds"), "counts": last.get("counts"), "cond": last.get("cond"),
                  "samples": last.get("samples"), "summary": got, "at": last.get("at")})  # fmt: skip


# --- the run ----------------------------------------------------------------------------------------------------------


@router.post("/simulate/run")
def run(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    from . import job
    from . import store_check as sc
    from .spec import parse_run

    raw = body_of(body)
    project = svc.require_current(writable=True)
    spec = parse_run(raw)
    with dialect.read_tx(project.engine) as conn:
        row, sim, model, _inputs, _plan = job.plan_run(conn, BlobStore(project.folder), spec)  # 404 / 409 / 422
        rows = sample_rows(conn, [sim])
        name = str(row.name) if row is not None else (sim.name or sc.KIND)
    registered(KIND_RUN, "Моделирование")
    params: dict[str, Any] = {"nodes": min(int(model.parents), int(model.grid.cells)), "n_real": sim.n_real,
                              "n_max": sim.search.passes[0].n_max}  # fmt: skip
    if rows:
        params["samples"] = rows  # for `memory_mb`: the rows of the table of the samples
    if spec.simulation_id is not None:
        params["simulation_id"] = spec.simulation_id
        if spec.expected_version is not None:
            params["expected_version"] = spec.expected_version
    else:
        params["simulation"] = spec.simulation
        if (who := author()) is not None:
            params["author"] = who
    key, job_id = s.submit(KIND_RUN, params, title=f"Моделирование: {name}")
    return {"source": key, "id": job_id}


# --- the post-processing ----------------------------------------------------------------------------------------------


def _submit_post(project: Any, s: Any, simulation_id: int, raw: dict[str, Any]) -> dict[str, Any]:
    from . import post as pt
    from . import store as st

    params = {**raw, "simulation_id": simulation_id}
    with dialect.read_tx(project.engine) as conn:
        pl = pt.plan_post(conn, BlobStore(project.folder), params)  # 404 / 409 / 422 as the job would answer
        last = (st.stored(conn, simulation_id).meta or {}).get("last_run") or {}
    registered(KIND_POST, "Постобработка")
    params["n_real"] = int(last.get("realizations") or pl.spec.n_real)
    params["nodes"] = int(last.get("nodes") or 0)
    key, job_id = s.submit(KIND_POST, params, title=f"Постобработка: {pl.name}")
    return {"source": key, "id": job_id}


@router.post("/simulations/{simulation_id}/post")
def post_row(svc: Svc, s: Sched, simulation_id: int, body: Payload) -> dict[str, Any]:
    raw = body_of(body)
    project = svc.require_current(writable=True)
    return _submit_post(project, s, simulation_id, raw)


@router.post("/simulate/post")
def post_body(svc: Svc, s: Sched, body: Payload) -> dict[str, Any]:
    """The same with `simulation_id` in the body (a command of the ribbon sends the id with the parameters)."""
    from bf_core.geostat.estimate.spec_parts import id_of

    raw = body_of(body)
    project = svc.require_current(writable=True)
    ident = id_of(raw.get("simulation_id"), "моделирование", "simulation_id")
    assert ident is not None
    return _submit_post(project, s, ident, raw)


__all__ = ["router"]
