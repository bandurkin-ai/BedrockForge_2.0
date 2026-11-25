"""Ids inside the `meta` of an implicit model on a `.bfggis` import (ADR 0026 решение 26; ADR 0022 §12 «.bfggis,
схема»; docs/progress/e19-api.md §2.4).

The model comes with its row (`archive/spec.py`: `objects` renumbered, the blobs with `blob_owners`, the outputs as
children by `parent_id`); its `meta` names other rows by id: the wireframe and the DTM of the boundary
(`boundary.*_id`), the clip (`post.clip_id`), the trend (`trend_id`), the set and the table of the codes
(`data.set_id`, `data.table`: the id as text), the groups' own sources (`groups[].params.result_id | set_id | table
| wireframe_id`), the layers of the manual data (`manual[].layer_id`), the outputs (`outputs[].wireframe_id |
line_layer_id`) and the same in `inputs[].id` (a table by the map of the tables, composites by the map of the
results, the rest by the map of the objects). An id the archive does not have becomes `DEAD_ID` (a table — «0»): the
old number would point at another row of the new project; the model reads and shows «источник удалён»
(`store_check.freshness`). `fp` and `last_run` stay as they are but the hash of the parameters of the last run
(`last_run.params`, which holds the ids): made again for the new ids when the model was not «устарела» by its
parameters before, so it is not after the import. The groups' numbers (`groups[].id`, `faults`, `manual[].group`)
are the model's own and do not change. A wireframe that is an output names its model (`meta.source.model_id`): the
new parent.

Called once all rows are written: `remap_implicit(conn, maps["objects"], maps["dh_tables"], maps["dh_results"])`
in `importer._restore`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError

from . import store_base as sb

__all__ = ["Remap", "remap_implicit", "remap_meta"]


@dataclass(frozen=True)
class Remap:
    """The maps of the import: old id → new id of the objects, of the tables of the drillholes, of their results."""

    objects: Mapping[int, int] = field(default_factory=dict)
    tables: Mapping[int, int] = field(default_factory=dict)
    results: Mapping[int, int] = field(default_factory=dict)

    def obj(self, old: Any) -> Any:
        return _new(old, self.objects)

    def table(self, old: Any) -> Any:
        """A table id as text: the new id, or «0» (no table has it); null stays null."""
        got = _new(old, self.tables)
        return got if got is None else (str(got) if got != sb.DEAD_ID else sb.DEAD_TABLE)

    def result(self, old: Any) -> Any:
        return _new(old, self.results)


def _new(old: Any, table: Mapping[int, int]) -> Any:
    """The new id of `old`; `DEAD_ID` when the archive did not have it; null (nothing named) stays null."""
    if old is None or isinstance(old, bool):
        return old
    try:
        got = table.get(int(old))
    except (TypeError, ValueError):
        return sb.DEAD_ID
    return sb.DEAD_ID if got is None else int(got)


def _params(g: Mapping[str, Any], ids: Remap) -> dict[str, Any]:
    group = dict(g)
    p = group.get("params")
    if isinstance(p, Mapping):
        p = dict(p)
        if "result_id" in p:
            p["result_id"] = ids.result(p["result_id"])
        if "set_id" in p:
            p["set_id"] = ids.obj(p["set_id"])
        if "table" in p:
            p["table"] = ids.table(p["table"])
        if "wireframe_id" in p:
            p["wireframe_id"] = ids.obj(p["wireframe_id"])
        group["params"] = p
    return group


def _input(i: Any, ids: Remap) -> Any:
    if not isinstance(i, Mapping):
        return i
    kind, ident = i.get("kind"), i.get("id")
    if kind == "dh_table":
        got = _new(ident, ids.tables)
    elif kind == "dh_result":
        got = ids.result(ident)
    else:
        got = ids.obj(ident)
    return {**i, "id": got}


def remap_meta(meta: Mapping[str, Any], ids: Remap) -> dict[str, Any]:
    """The `meta` of an implicit model with the ids of the new project; `last_run.params` is made again. A mark
    «идёт построение» (the snapshot of a project that is building it) is not carried over: its job is not a job of the
    new project, the model is as it was before (`store_base.settled`)."""
    out = sb.settled(meta) if meta.get("state") == "building" else dict(meta)
    if isinstance(out.get("boundary"), Mapping):
        b = dict(out["boundary"])
        for key in ("wireframe_id", "dtm_id"):
            if key in b:
                b[key] = ids.obj(b[key])
        out["boundary"] = b
    if "trend_id" in out:
        out["trend_id"] = ids.obj(out["trend_id"])
    if isinstance(out.get("data"), Mapping):
        d = dict(out["data"])
        d["set_id"] = ids.obj(d.get("set_id"))
        d["table"] = ids.table(d.get("table"))
        out["data"] = d
    if isinstance(out.get("post"), Mapping):
        out["post"] = {**out["post"], "clip_id": ids.obj(out["post"].get("clip_id"))}
    if isinstance(out.get("groups"), list):
        out["groups"] = [_params(g, ids) if isinstance(g, Mapping) else g for g in out["groups"]]
    if isinstance(out.get("manual"), list):
        out["manual"] = [{**m, "layer_id": ids.obj(m.get("layer_id"))} if isinstance(m, Mapping) else m
                         for m in out["manual"]]  # fmt: skip
    if isinstance(out.get("outputs"), list):
        out["outputs"] = [_output(o, ids) for o in out["outputs"]]
    if isinstance(out.get("inputs"), list):
        out["inputs"] = [_input(i, ids) for i in out["inputs"]]
    return _rehashed(meta, out)


def _output(o: Any, ids: Remap) -> Any:
    if not isinstance(o, Mapping):
        return o
    got = dict(o)
    for key in ("wireframe_id", "line_layer_id"):
        if key in got:
            got[key] = ids.obj(got[key])
    return got


def _rehashed(meta: Mapping[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    """`last_run.params` is the hash of the parameters of the last run, ids included: made again for the new ids when
    the model was not «устарела» by its parameters before (a model that was stays so)."""
    last = out.get("last_run")
    if isinstance(last, Mapping) and last.get("params") is not None:
        try:
            if sb.params_hash(sb.load_spec(meta)) == last["params"]:
                out["last_run"] = {**last, "params": sb.params_hash(sb.load_spec(out))}
        except ApiError:
            pass  # the parameters do not read: the model stays «параметры не читаются»
    return out


def remap_implicit(conn: sa.Connection, objects_map: Mapping[int, int], tables_map: Mapping[int, int],
                   results_map: Mapping[int, int] | None = None) -> int:  # fmt: skip
    """Renumbers the ids inside the `meta` of every implicit model and of its output wireframes; returns how many
    rows changed."""
    from bf_core.db.schema import objects

    ids = Remap(objects_map, tables_map, results_map or {})
    changed = 0
    rows = conn.execute(sa.select(objects.c.id, objects.c.kind, objects.c.parent_id, objects.c.meta).where(
        objects.c.kind.in_((sb.KIND, "wireframe")))).all()  # fmt: skip
    models = {int(r.id) for r in rows if r.kind == sb.KIND}
    for ident, kind, parent, meta in rows:
        meta = meta or {}
        if kind == sb.KIND:
            new: dict[str, Any] = remap_meta(meta, ids)
        else:
            source = meta.get("source")
            if isinstance(source, Mapping) and source.get("op") == "implicit_copy":  # a copy «В каркас»: no parent
                new = {**meta, "source": {**source, "model_id": ids.obj(source.get("model_id")),
                                          "wireframe_id": ids.obj(source.get("wireframe_id"))}}
            elif parent in models and isinstance(source, Mapping) and source.get("op") == "implicit":
                new = {**meta, "source": {**source, "model_id": int(parent)}}
            else:
                continue
        if new != meta:
            conn.execute(sa.update(objects).where(objects.c.id == ident).values(meta=new))
            changed += 1
    return changed

