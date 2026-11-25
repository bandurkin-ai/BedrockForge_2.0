"""Ids inside the `meta` of the geostatistics objects on a `.bfggis` import (ADR 0022 §12 «.bfggis, схема»).

The objects come with their rows (`archive/spec.py`: `objects` renumbered, the files with `blob_owners`); their
`meta` names other rows by id: a variogram model — its set (`source.set_id`), its table and the table of the domain
(`source.table`, `source.domain.table`: the id as text) and the tables it was computed from (`inputs.tables[].id`).
An id the archive does not have becomes null — the model shows «источник удалён» (`store.freshness`); the old number
would point at another row of the new project. Names, tokens and revisions need nothing. A trend (16.16) names
the model it was copied from (`origin.model_id`; not in the archive — null, the trend stays as it is); a structural
trend (Э19) names its set, the table of the measurements and the wireframe the same way. A row of the
estimation (18.11) names the block model, the samples (set, tables), the variogram models, the trend, the filter and the
wireframes of its inline rules, and keeps the same in `inputs[].id` (a table by the map of the tables, a field of a
model `"<model>:<name>"` by the map of the objects); an id the archive does not have becomes null (a table of the
samples — «0») — the row reads (`estimate.store_base.lenient`) and shows «источник удалён». A simulation
(18.32) is renumbered as a row of the estimation (its `variogram`, `variograms`, `inputs`, `target`, `samples`; no
trend, no plans). The hash of the parameters of the last run (`last_run.params`) of both is made again for the new ids.
A plan of the estimation renumbers `model_id` and `items` (a row the archive does not have leaves the
plan). `fp` and `last_run` stay as they are (but the hash above): the project keeps its states after the import.

Called once all rows are written: `remap_geostat(conn, maps["objects"], maps["dh_tables"])` in `importer._restore`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import sqlalchemy as sa

Lookup = Callable[[int], int | None]


def _new(old: Any, lookup: Lookup) -> int | None:
    if old is None or isinstance(old, bool):
        return None
    try:
        return lookup(int(old))
    except (TypeError, ValueError):
        return None


def _table(old: Any, tables: Lookup) -> str | None:
    new = _new(old, tables)
    return None if new is None else str(new)


def model_meta(meta: Mapping[str, Any], objects_of: Lookup, tables_of: Lookup) -> dict[str, Any]:
    """The `meta` of a variogram model with the ids of the new project."""
    out = dict(meta)
    src = dict(out.get("source") or {})
    if "set_id" in src:
        src["set_id"] = _new(src["set_id"], objects_of)
    if "table" in src:
        src["table"] = _table(src["table"], tables_of)
    if isinstance(src.get("domain"), Mapping) and src["domain"].get("table") is not None:
        src["domain"] = {**src["domain"], "table": _table(src["domain"]["table"], tables_of)}
    out["source"] = src
    inputs = out.get("inputs")
    if isinstance(inputs, Mapping):
        listed = [t for t in inputs.get("tables") or [] if isinstance(t, Mapping)]
        tables = [{**t, "id": _new(t.get("id"), tables_of)} for t in listed]
        out["inputs"] = {**inputs, "tables": tables}
    return out


def trend_meta(meta: Mapping[str, Any], objects_of: Lookup, tables_of: Lookup | None = None) -> dict[str, Any]:
    """The `meta` of a trend with the id of the model of its origin in the new project; of a structural one (Э19) —
    also the set (`source.set_id`), the table of the measurements (`source.table`, the id as text) and the
    wireframe (`source.wireframe_id`): one the archive does not have becomes null, the trend shows «источник удалён»."""
    out = dict(meta)
    origin = out.get("origin")
    if isinstance(origin, Mapping) and "model_id" in origin:
        out["origin"] = {**origin, "model_id": _new(origin["model_id"], objects_of)}
    src = out.get("source")
    if isinstance(src, Mapping):
        new = dict(src)
        for key in ("set_id", "wireframe_id"):
            if key in new:
                new[key] = _new(new[key], objects_of)
        if "table" in new:
            new["table"] = None if tables_of is None else _table(new["table"], tables_of)
        out["source"] = new
    return out


SIMULATION = "simulation"  # the kind of the stored simulations (`geostat/simulation/store_check.KIND`; 18.32)
DEAD_TABLE = "0"  # a table of an estimation the archive did not have: no table has this id, the row stays readable


def _vario(v: Any, objects_of: Lookup) -> Any:
    return {**v, "model_id": _new(v.get("model_id"), objects_of)} if isinstance(v, Mapping) else v


def _input(i: Any, objects_of: Lookup, tables_of: Lookup) -> Any:
    if not isinstance(i, Mapping):
        return i
    kind, ident = i.get("kind"), i.get("id")
    if kind == "dh_table":
        return {**i, "id": _new(ident, tables_of)}
    if kind == "bm_field":  # "<block model id>:<field>"
        model, _, name = str(ident).partition(":")
        new = _new(model, objects_of)
        return {**i, "id": None if new is None else f"{new}:{name}"}
    return {**i, "id": _new(ident, objects_of)}


def _ids_meta(meta: Mapping[str, Any], objects_of: Lookup, tables_of: Lookup) -> dict[str, Any]:
    """The `meta` of a row of the estimation (18.11) or of a simulation with the ids of the new project."""
    out = dict(meta)
    if "model_id" in out:
        out["model_id"] = _new(out["model_id"], objects_of)
    if "trend_id" in out:
        out["trend_id"] = _new(out["trend_id"], objects_of)
    target = out.get("target")
    if isinstance(target, Mapping):
        t = dict(target)
        if "filter_id" in t:
            t["filter_id"] = _new(t["filter_id"], objects_of)
        if isinstance(t.get("rules"), Mapping):
            from bf_core.blocks.filters.schema import remap_ids

            t["rules"] = remap_ids(t["rules"], objects_of)
        out["target"] = t
    samples = out.get("samples")
    if isinstance(samples, Mapping):
        s = dict(samples)
        if "set_id" in s:
            s["set_id"] = _new(s["set_id"], objects_of)
        if "table" in s:
            s["table"] = _table(s["table"], tables_of) or DEAD_TABLE
        if isinstance(s.get("domain"), Mapping) and s["domain"].get("table") is not None:  # null — the same table
            s["domain"] = {**s["domain"], "table": _table(s["domain"]["table"], tables_of) or DEAD_TABLE}
        out["samples"] = s
    if isinstance(out.get("variogram"), Mapping):
        out["variogram"] = _vario(out["variogram"], objects_of)
    if isinstance(out.get("variograms"), list):
        out["variograms"] = [_vario(v, objects_of) for v in out["variograms"]]
    if isinstance(out.get("inputs"), list):
        out["inputs"] = [_input(i, objects_of, tables_of) for i in out["inputs"]]
    return out


def _rehashed(meta: Mapping[str, Any], out: dict[str, Any], load: Callable[[Mapping[str, Any]], Any],
              params_hash: Callable[[Any], str]) -> dict[str, Any]:  # fmt: skip
    """`last_run.params` is the hash of the parameters of the last run, ids included: made again for the new ids when
    the row was not «устарел» by its parameters before, so it is not after the import (a row that was stays so)."""
    from bf_core.api.errors import ApiError

    last = out.get("last_run")
    if isinstance(last, Mapping) and last.get("params") is not None:
        try:
            if params_hash(load(meta)) == last["params"]:
                out["last_run"] = {**last, "params": params_hash(load(out))}
        except ApiError:
            pass  # the parameters do not read: the row stays «параметры не читаются»
    return out


def estimation_meta(meta: Mapping[str, Any], objects_of: Lookup, tables_of: Lookup) -> dict[str, Any]:
    """The `meta` of a row of the estimation (18.11) with the ids of the new project; the hash of the parameters of its
    last run (`last_run.params`, which holds the ids) is made again (`_rehashed`)."""
    from bf_core.geostat.estimate import store_base as sb

    return _rehashed(meta, _ids_meta(meta, objects_of, tables_of), sb.load_spec, sb.params_hash)


def simulation_meta(meta: Mapping[str, Any], objects_of: Lookup, tables_of: Lookup) -> dict[str, Any]:
    """The `meta` of a simulation (18.32) with the ids of the new project: as a row of the estimation (`_rehashed`)."""
    from bf_core.geostat.simulation import store_check as sc

    return _rehashed(meta, _ids_meta(meta, objects_of, tables_of), sc.load_spec, sc.params_hash)


def plan_meta(meta: Mapping[str, Any], objects_of: Lookup) -> dict[str, Any]:
    """The `meta` of a plan of the estimation: the block model and the rows in the new project."""
    out = dict(meta)
    if "model_id" in out:
        out["model_id"] = _new(out["model_id"], objects_of)
    items = [_new(i, objects_of) for i in out.get("items") or []]
    out["items"] = [i for i in items if i is not None]
    return out


def remap_geostat(conn: sa.Connection, objects_map: Mapping[int, int], tables_map: Mapping[int, int]) -> int:
    """Renumbers the ids inside the `meta` of every variogram model, trend, estimation and plan; returns how many
    changed."""
    from bf_core.db.schema import objects

    from .estimate import store_base as eb
    from .trend_store import KIND as TREND
    from .variogram.store import KIND

    changed = 0
    rows = conn.execute(
        sa.select(objects.c.id, objects.c.kind, objects.c.meta).where(
            objects.c.kind.in_((KIND, TREND, eb.KIND, eb.PLAN, SIMULATION))
        )
    )
    for model_id, kind, meta in rows.all():
        if kind == TREND:
            new = trend_meta(meta or {}, objects_map.get, tables_map.get)
        elif kind == eb.KIND:
            new = estimation_meta(meta or {}, objects_map.get, tables_map.get)
        elif kind == SIMULATION:
            new = simulation_meta(meta or {}, objects_map.get, tables_map.get)
        elif kind == eb.PLAN:
            new = plan_meta(meta or {}, objects_map.get)
        else:
            new = model_meta(meta or {}, objects_map.get, tables_map.get)
        if new != (meta or {}):
            conn.execute(sa.update(objects).where(objects.c.id == model_id).values(meta=new))
            changed += 1
    return changed
