"""What an object was built from — `objects.meta.built` (ТЗ 7.23 «Хранение»; ADR 0015 §6).

An operation writes {kind "operation", op, title, algo, params, sources [{role, kind, id, name, version, lines}],
tolerances, report, at, rebuild_of} on every object it makes; «Построить ЦТМ» keeps its own record
{kind "dtm", method, sources, params, report} (`wireframes/jobs.py`). The sources are named by their ids in the
record, not by `object_refs`: a reference would forbid deleting the contours once the solid is built.

On a `.bfggis` import (`remap_objects`) every id inside a record is renumbered: of its sources, of the objects
in its parameters (the keys `REF_KEYS`, at any depth) and `rebuild_of`. A source the archive does not have
(deleted since) keeps its name and loses its id: the old number would point at another object of the new
project. The stable numbers of lines (`lines`, `line`) are numbers inside a layer and stay as they are.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

#: Bumped when an operation gives another result for the same input.
ALGORITHM = 1

#: Parameters that hold object ids (one or a list), at any depth of the parameters.
REF_KEYS = frozenset(
    {"wireframe_id", "wireframe_ids", "surface_id", "layer_id", "layer_ids", "set_ids", "a", "b", "top", "bottom",
     "series_id"}
)

NewId = Callable[[int], "int | None"]


def _id(value: Any, new_id: NewId) -> int | None:
    return new_id(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _params(value: Any, new_id: NewId) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if k in REF_KEYS and v is not None:
                out[k] = [_id(x, new_id) for x in v] if isinstance(v, list) else _id(v, new_id)
            else:
                out[k] = _params(v, new_id)
        return out
    if isinstance(value, list) and any(isinstance(x, dict | list) for x in value):
        return [_params(x, new_id) for x in value]
    return value


def remap_built(built: Any, new_id: NewId) -> Any:
    """`meta.built` with the ids of the project it was made in replaced by `new_id(old)` (None — gone).
    Anything that is not a record is left as it is."""
    if not isinstance(built, dict):
        return built
    out = dict(built)
    if isinstance(built.get("sources"), list):
        out["sources"] = [
            {**src, "id": _id(src.get("id"), new_id)} if isinstance(src, dict) else src for src in built["sources"]
        ]
    if isinstance(built.get("params"), dict):
        out["params"] = _params(built["params"], new_id)
    if "rebuild_of" in built and built["rebuild_of"] is not None:
        out["rebuild_of"] = _id(built["rebuild_of"], new_id)
    return out


def remap_objects(conn: Any, objects_map: dict[int, int]) -> int:
    """`meta.built` of every wireframe, line file and line layer on a `.bfggis` import. Returns how many
    records were renumbered."""
    import sqlalchemy as sa

    from bf_core.db.schema import objects

    kinds = ("wireframe", "line_file", "line_layer")
    rows = conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind.in_(kinds))).all()
    changed = 0
    for oid, meta in rows:
        if isinstance(meta, dict) and meta.get("built") is not None:
            built = remap_built(meta["built"], objects_map.get)
            conn.execute(sa.update(objects).where(objects.c.id == oid).values(meta={**meta, "built": built}))
            changed += 1
    return changed


def jsonable(value: Any) -> Any:
    """A report as plain JSON: numpy numbers and arrays converted, non-finite numbers to None."""
    import math

    import numpy as np

    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    if isinstance(value, bool | np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float | np.floating):
        x = float(value)
        return x if math.isfinite(x) else None
    return value


def record(
    *,
    op: str,
    title: str,
    params: dict[str, Any],
    sources: list[dict[str, Any]],
    report: dict[str, Any],
    tolerances: dict[str, Any] | None = None,
    rebuild_of: int | None = None,
) -> dict[str, Any]:
    from bf_core.db import dialect

    return {
        "kind": "operation",
        "op": op,
        "title": title,
        "algo": ALGORITHM,
        "params": jsonable(params),
        "sources": jsonable(sources),
        "tolerances": jsonable(tolerances or {}),
        "report": jsonable(report),
        "at": dialect.utcnow().isoformat(),
        "rebuild_of": rebuild_of,
    }
