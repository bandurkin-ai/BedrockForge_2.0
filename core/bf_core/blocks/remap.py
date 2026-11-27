"""Ids inside the `meta` of the block models on a `.bfggis` import (ADR 0023 §2, §6–§9; docs/progress/e17-api.md §2).

The models come with the archive as objects (`archive/spec.py`: `objects` renumbered, the files with `blob_owners`).
Their `meta` names other objects by id, and «устарел» and «Перестроить» read those ids:
  * `inputs.wireframes[].id`, `inputs.model[].id` — the bodies of a fill or a coding, the source of a regularization
    (`fill/inputs.stale_reason`);
  * `source.params` of a fill or a coding and of the fields they made (`source.op` fill | code): `source_id` (the
    prototype or the coded model), `wireframes[].id`, the keys of `names.wireframes` / `names.model`;
  * `source.params` of a field of the calculator, the density or the categories (calc | density | classify):
    `filter_id`, the wireframes and surfaces of the quick rules `restrict`, `overrides[].wireframe_id`.
Left with the old numbers they point at other objects of the new project: a model that was current reads «изменён
каркас «…»» of a body it never used, and «Перестроить» would build it from that body. An id the archive does not have
becomes `DEAD_ID` (no object has it: «удалён каркас», `source_lost`) or null in a leaf of the rules («недоступен», as in
a filter — `filters/remap.py`). The hashes of the files (`inputs[].sha`, `source.inputs`) hold no ids; the hash of
the tree of the restrictor in `source.inputs` of a field (`calc_job.inputs_of`) does when the tree names a wireframe
or a surface — it is made again for the new ids, so a model and a field that were current stay so. The fields of an
estimate, a simulation and a pit keep their ids elsewhere (`geostat/remap.py`, `mine/remap.py`) and are not touched.

Called once all rows are written and BEFORE `remap_filters` (the old hash of a tree needs the rules of its filter
with the old ids): `remap_blocks(conn, maps["objects"])` in `importer._restore`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa

__all__ = ["DEAD_ID", "model_meta", "remap_blocks"]

DEAD_ID = 2_000_000_000  # an id no object has (the same number as `model/store_base.DEAD_ID`)
FILL_OPS = ("fill", "code")
RULE_OPS = ("calc", "density", "classify")
Lookup = Callable[[int], int | None]
#: `source.params` of a field → (the hash of the tree of its restrictor with the old ids, with the new ones) or None
Trees = Callable[[Mapping[str, Any]], "tuple[str, str] | None"]


def _new(old: Any, lookup: Lookup) -> Any:
    """The new id of `old`; `DEAD_ID` when the archive did not have it; null (nothing named) stays null."""
    if old is None or isinstance(old, bool):
        return old
    try:
        got = lookup(int(old))
    except (TypeError, ValueError):
        return DEAD_ID
    return DEAD_ID if got is None else int(got)


def _items(items: Any, lookup: Lookup, key: str = "id") -> Any:
    if not isinstance(items, list):
        return items
    return [{**i, key: _new(i.get(key), lookup)} if isinstance(i, Mapping) else i for i in items]


def _names(names: Any, lookup: Lookup) -> Any:
    """`source.params.names` {"wireframes": {id: name}, "model": {id: name}} keyed by the new ids."""
    if not isinstance(names, Mapping):
        return names
    out: dict[str, Any] = {}
    for kind, table in names.items():
        if not isinstance(table, Mapping):
            out[kind] = table
            continue
        fresh: dict[str, Any] = {}
        for old, name in table.items():
            fresh.setdefault(str(_new(old, lookup)), name)
        out[kind] = fresh
    return out


def _params(op: Any, params: Any, lookup: Lookup) -> Any:
    """`source.params` of a model or of a field made by `op` with the ids of the new project."""
    if not isinstance(params, Mapping):
        return params
    out = dict(params)
    if op in FILL_OPS:
        if "source_id" in out:
            out["source_id"] = _new(out["source_id"], lookup)
        if "wireframes" in out:
            out["wireframes"] = _items(out["wireframes"], lookup)
        if "names" in out:
            out["names"] = _names(out["names"], lookup)
    elif op in RULE_OPS:
        from .filters.schema import remap_ids

        if out.get("filter_id") is not None:
            out["filter_id"] = _new(out["filter_id"], lookup)
        if isinstance(out.get("restrict"), Mapping):
            out["restrict"] = remap_ids(out["restrict"], lookup)
        if "overrides" in out:
            out["overrides"] = _items(out["overrides"], lookup, "wireframe_id")
    return out


def _source(src: Any, lookup: Lookup, trees: Trees | None = None) -> Any:
    if not isinstance(src, Mapping) or "params" not in src:
        return src
    op = src.get("op")
    out = {**src, "params": _params(op, src["params"], lookup)}
    if (
        trees is not None
        and op in RULE_OPS
        and isinstance(src["params"], Mapping)
        and isinstance(src.get("inputs"), list)
    ):
        pair = trees(src["params"])
        if pair is not None and pair[0] != pair[1]:
            out["inputs"] = sorted(pair[1] if sha == pair[0] else sha for sha in src["inputs"])
    return out


def model_meta(meta: Mapping[str, Any], lookup: Lookup, trees: Trees | None = None) -> dict[str, Any]:
    """The `meta` of a block model with the ids of the new project (`lookup`: old id → new id or None; `trees` —
    the hashes of the tree of the restrictor of a field before and after, `remap_blocks` gives it)."""
    out = dict(meta)
    inputs = out.get("inputs")
    if isinstance(inputs, Mapping):
        out["inputs"] = {k: _items(v, lookup) if k in ("wireframes", "model") else v for k, v in inputs.items()}
    if "source" in out:
        out["source"] = _source(out["source"], lookup)
    if isinstance(out.get("fields"), list):
        out["fields"] = [
            {**f, "source": _source(f["source"], lookup, trees)} if isinstance(f, Mapping) and "source" in f else f
            for f in out["fields"]
        ]
    return out


def _trees(conn: sa.Connection, lookup: Lookup) -> Trees:
    """The hashes of the tree of the restrictor of a field (`calc_job.rules_tree`: the filter of the library AND the
    quick rules) with the ids of the archive and with those of the new project. The filter is read under its new id
    with its rules as the archive had them: `remap_filters` has not run yet."""

    def of(params: Mapping[str, Any]) -> tuple[str, str] | None:
        from bf_core.api.errors import ApiError

        from . import calc_job, restrict
        from .filters import schema as fs
        from .filters import store as filters

        rules = None
        if params.get("filter_id") is not None:
            try:
                fid = lookup(int(params["filter_id"]))
                rules = None if fid is None else filters.rules_of(conn, fid)
            except (ApiError, TypeError, ValueError):
                rules = None
            if rules is None:
                return None  # the filter is gone: the field is «устарел» whatever its hashes are
        quick = params.get("restrict")
        old = restrict.combine(rules, quick if isinstance(quick, Mapping) else None)
        if old is None:
            return None
        return calc_job._digest(old), calc_job._digest(fs.remap_ids(old, lookup))  # noqa: SLF001

    return of


def remap_blocks(conn: sa.Connection, objects_map: Mapping[int, int]) -> int:
    """Renumbers the ids inside the `meta` of every block model; returns how many models changed."""
    from bf_core.db.schema import objects

    from .schema import KIND

    changed = 0
    trees = _trees(conn, objects_map.get)
    for ident, meta in conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == KIND)).all():
        if not isinstance(meta, Mapping):
            continue
        new = model_meta(meta, objects_map.get, trees)
        if new != meta:
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=new))
            changed += 1
    return changed
