"""The common part of the store of the implicit models (подшаг 19.11; ADR 0026 §7 решения 22–26; docs/progress/
e19-api.md §2): the kind, the roles of the blobs and of `object_refs`, the stored object, the errors, the parameters
of a stored `meta` and the hashes that «устарела» and the graph compare. Light: sqlalchemy and the pure parts of the
parameters only (nothing heavy before BF-READY).

    row = stored(conn, id)                  # 404 `model_not_found`
    spec = load_spec(meta)                  # ModelSpec of a stored `meta` (the keys of the store are skipped)
    h = params_hash(spec)                   # `last_run.params`: with the numbers of the objects it names
    fp = graph_fingerprint(meta, shas)      # without them: a project import renumbers the objects, not the state
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db.schema import objects

from .spec import ModelSpec, parse_model, refuse
from .spec_parts import ALGO

KIND = "implicit_model"  # objects(kind="implicit_model", parent_id=NULL): the tree «Неявные модели»
OWNER = "object"
LINEAGE_KIND = "object:" + KIND
ROLE_POINTS, ROLE_PATCHES, ROLE_SOLUTION = "implicit_points", "implicit_patches", "implicit_solution"
BLOB_ROLES = (ROLE_POINTS, ROLE_PATCHES, ROLE_SOLUTION)
ROLE_TREND = "trend"  # object_refs(src=model, dst=trend): the trend with such rows is not deleted (409 `in_use`)
DEFAULT_NAME = "Неявная модель"
MAX_NAME = 200
#: an id that no object has: stands for a reference the archive did not have (`remap`); the model is read, the
#: reference is «источник удалён»
DEAD_ID = 2_000_000_000
DEAD_TABLE = "0"

__all__ = ["ALGO", "BLOB_ROLES", "DEAD_ID", "DEAD_TABLE", "DEFAULT_NAME", "KIND", "LINEAGE_KIND", "OWNER",
           "ROLE_PATCHES", "ROLE_POINTS", "ROLE_SOLUTION", "ROLE_TREND", "back_of", "bad", "building", "conflict",
           "graph_fingerprint", "is_building", "job_alive", "load_spec", "not_found", "params_hash", "refuse_building",
           "resolved_spec", "settled", "stored", "viewed", "without_ids"]  # fmt: skip


def not_found(param: str | None = None) -> ApiError:
    return refuse("model_not_found", **({"param": param} if param else {}))


def building() -> ApiError:
    return refuse("model_building")


def conflict(version: int) -> ApiError:
    return ApiError(409, "version_conflict", "Неявная модель изменена в другом окне.",
                    "Перечитайте модель или сохраните свою как новую.", {"version": version})  # fmt: skip


def bad(param: str, message: str, hint: str, code: str = "bad_param", status: int = 422, **extra: Any) -> ApiError:
    return ApiError(status, code, message, hint, {"param": param, **extra})


def stored(conn: sa.Connection, model_id: int) -> Any:
    """The row of the model; 404 `model_not_found`."""
    r = conn.execute(sa.select(objects).where(objects.c.id == model_id, objects.c.kind == KIND)).first()
    if r is None:
        raise not_found()
    return r


# --- «идёт построение» --------------------------------------------------------------------------------------------
# The state lives in `meta` only (`meta.state == "building"`, `meta.job_id`, `meta.back`): `objects.state` and
# `objects.job_id` of a model stay `ready` / NULL, because the clean-ups of the jobs and of the project snapshot
# (`jobs.store.drop_partial`, `archive.export`) delete every `objects` row that is `building` — the model with its
# outputs among them.


def job_alive(conn: sa.Connection, job_id: int) -> bool:
    """The job is queued or running (its row of `jobs`); a job that is gone, done, failed or cancelled is not."""
    from bf_core.db.schema import jobs
    from bf_core.jobs.store import ACTIVE

    status = conn.execute(sa.select(jobs.c.status).where(jobs.c.id == job_id)).scalar()
    return status in ACTIVE


def queued_job(conn: sa.Connection, model_id: int) -> int | None:
    """The id of a job `implicit.build` / `implicit.update` of the model that is queued or running (its row of `jobs`;
    `params.model_id`), else None: the model is «идёт построение» from the moment the job is queued, though the job
    marks it in `meta` only when it starts."""
    from bf_core.db.schema import jobs
    from bf_core.jobs.store import ACTIVE

    q = sa.select(jobs.c.id, jobs.c.params).where(
        jobs.c.status.in_(ACTIVE), jobs.c.kind.in_(("implicit.build", "implicit.update"))).order_by(jobs.c.id)
    for r in conn.execute(q):
        p = r.params
        if isinstance(p, Mapping) and p.get("model_id") == model_id:
            return int(r.id)
    return None


def is_building(conn: sa.Connection, meta: Mapping[str, Any] | None, model_id: int | None = None) -> bool:
    """The model is being built now: a job of it is queued or running (`queued_job`, when `model_id` is given), or
    `meta.state == "building"` and its job (`meta.job_id`) is alive (a mark without a job id is trusted). A mark whose
    job is gone (a crash, a kill, a restart, a cancel in the queue) is not: the model is as it was."""
    if model_id is not None and queued_job(conn, model_id) is not None:
        return True
    m = meta or {}
    if m.get("state") != "building":
        return False
    job = m.get("job_id")
    return True if job is None or isinstance(job, bool) or not isinstance(job, int) else job_alive(conn, job)


def back_of(meta: Mapping[str, Any] | None) -> tuple[str, Any]:
    """(state, error) the model goes back to when its build ends without a result."""
    m = meta or {}
    if m.get("state") != "building":
        return str(m.get("state") or "ready"), m.get("error")
    back = m.get("back")
    if isinstance(back, Mapping) and back.get("state") in ("ready", "error"):
        return str(back["state"]), back.get("error")
    return "ready", None


def settled(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """`meta` of a model whose build is over (or is not alive): the state it went back to, no job and no mark."""
    out = dict(meta or {})
    state, error = back_of(out)
    out.pop("job_id", None)
    out.pop("back", None)
    out.update(state=state, error=error)
    return out


def viewed(conn: sa.Connection, meta: Mapping[str, Any] | None, model_id: int | None = None) -> dict[str, Any]:
    """`meta` as the readers show it: «идёт построение» while a job of the model is queued or running, even before the
    job has marked it; a mark whose job is gone is shown as what the model was."""
    if not is_building(conn, meta, model_id):
        return settled(meta)
    out = dict(meta or {})
    if out.get("state") != "building":
        state, error = back_of(out)
        out.update(state="building", error=None, back={"state": state, "error": error})
    return out


def refuse_building(conn: sa.Connection, row: Any) -> None:
    """409 `model_building` for a model that is being built (`is_building`)."""
    if is_building(conn, row.meta, int(row.id)):
        raise building()


# --- the parameters of a stored row ---------------------------------------------------------------------------------


def load_spec(meta: Mapping[str, Any]) -> ModelSpec:
    """The checked parameters of a stored `meta` (`parse_model` skips `v`, `algo`, `extent`, `outputs`, `stats`,
    `inputs`, `state`, `error`, `versions`, `last_run`, `created_at`). A reference the archive did not have is
    `DEAD_ID` (`remap`), so the parameters parse as they are."""
    return parse_model(dict(meta))


def resolved_spec(meta: Mapping[str, Any]) -> ModelSpec:
    """`load_spec` with the step `h` the model is built with when the user left it «авто» (the extent of the boundary
    is in `meta.extent`)."""
    spec = load_spec(meta)
    extent = meta.get("extent")
    if spec.h is None and isinstance(extent, list) and len(extent) == 6:
        try:
            spec = spec.resolved(tuple(float(v) for v in extent))
        except ApiError:
            pass
    return spec


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def params_hash(spec: ModelSpec) -> str:
    """SHA-256 of the parameters without the name (`spec.hash_params`): the job writes it into `last_run.params`, a
    model whose parameters differ from it is «устарела». The numbers of the objects are in it (naming another
    wireframe is a change); `remap` makes it again after an import."""
    return hashlib.sha256(_dump(spec.hash_params()).encode("utf-8")).hexdigest()


def without_ids(params: Mapping[str, Any]) -> dict[str, Any]:
    """`spec.hash_params()` without the numbers of the project's objects it names (the wireframe and the DTM of the
    boundary, the trend, the set and the table of the codes, the layers of the manual data, the clip, the results, the
    sets, the tables and the wireframes of the groups): a project import renumbers them, the objects themselves are
    among the inputs with their own states. The numbers of the groups (`groups[].id`, `faults`, `manual[].group`)
    are the model's own and stay."""
    out = dict(params)
    out.pop("trend_id", None)
    if isinstance(out.get("boundary"), Mapping):
        out["boundary"] = {k: v for k, v in out["boundary"].items() if k not in ("wireframe_id", "dtm_id")}
    if isinstance(out.get("data"), Mapping):
        out["data"] = {k: v for k, v in out["data"].items() if k not in ("set_id", "table")}
    if isinstance(out.get("post"), Mapping):
        out["post"] = {k: v for k, v in out["post"].items() if k != "clip_id"}
    if isinstance(out.get("manual"), list):
        out["manual"] = [{k: v for k, v in m.items() if k != "layer_id"} for m in out["manual"]]
    if isinstance(out.get("groups"), list):
        groups = []
        for g in out["groups"]:
            p = g.get("params")
            if isinstance(p, Mapping):
                g = {**g, "params": {k: v for k, v in p.items()
                                     if k not in ("result_id", "set_id", "table", "wireframe_id")}}  # fmt: skip
            groups.append(g)
        out["groups"] = groups
    return out


def graph_fingerprint(params: Mapping[str, Any], points_sha: str | None, solution_sha: str | None) -> str:
    """ADR 0026 решение 25: SHA-256 of the parameters (without the numbers of the objects, `without_ids`; `algo` is
    among them) and of the blobs `implicit_points` and `implicit_solution` — the data and the solution the outputs
    were made of. The inputs are not in it: a changed input makes the model «устарела» through the graph, the
    outputs follow."""
    text = _dump([without_ids(params), points_sha, solution_sha])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
