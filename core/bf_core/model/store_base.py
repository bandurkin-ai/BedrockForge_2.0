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
           "ROLE_PATCHES", "ROLE_POINTS", "ROLE_SOLUTION", "ROLE_TREND", "bad", "building", "conflict",
           "graph_fingerprint", "load_spec", "not_found", "params_hash", "stored", "without_ids"]  # fmt: skip


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


# --- the parameters of a stored row ---------------------------------------------------------------------------------


def load_spec(meta: Mapping[str, Any]) -> ModelSpec:
    """The checked parameters of a stored `meta` (`parse_model` skips `v`, `algo`, `extent`, `outputs`, `stats`,
    `inputs`, `state`, `error`, `versions`, `last_run`, `created_at`). A reference the archive did not have is
    `DEAD_ID` (`remap`), so the parameters parse as they are."""
    return parse_model(dict(meta))


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
