"""Stored «Параметры оценки» (ТЗ 8.8; ADR 0024 §2, решения 2–3; ADR 0025; подшаг 18.11; docs/progress/e18-api.md §2,
§4): `objects(kind="estimation", parent_id=NULL)`, names unique among the rows (`name_key`), the files — only the
blob `xval` (the table of the cross-validation, 18.14). The plans — `store_plan.py`, the checks against the project —
`store_check.py`; this module is their entrance (`store.plan_create`, `store.freshness`, …).

`meta` v1: the checked parameters (`spec.canonical()`) and `origin` (the centre of the extent of the samples, set on
creation and when the samples change), `inputs` [{kind, id, fp, name}] (the graph of ADR 0025, e18-api §2.3), `algo`,
`versions`, `created_at`, `last_run` (written by the job: `record_run`).

Copies and references (ADR 0024 решение 3): a variogram model is copied into the row (`variogram.snapshot`, `sha`,
`scale` of Д-38, `share`) — deleting or editing the model leaves the row as it is, a new version of the model is
taken on `PUT`; the trend and the filter are `object_refs(src=row, role="trend" | "filter")`: a trend or a filter
with such rows is not deleted (their stores list the row in «Где используется», 409 `in_use` / 422 `filter_in_use`).
The model of the variogram has no reference: `usage_of_variogram` lists the rows that copied it. The block model has
none either: a row of a deleted model is read («источник удалён») and cannot run.

`create` / `update` read the samples through `project` (`engine`, `uid`, `folder`) — in the caller's transaction or
before it (`prepare`, then `create(…, prepared=…)`). After the write the graph gets `lineage.record(conn,
("object:estimation", id), inputs)` — when the module exists (18.Г1); until then nothing.

Errors: 404 `estimation_not_found`, `block_model_not_found`, `model_not_found`, `trend_not_found`,
`filter_not_found`, `set_not_found`, `table_not_found`; 409 `name_taken`, `version_conflict {version}`; 422
`bad_param {param}`, `variable_grid`, `field_not_found`, `domain_unmapped`, `no_samples {counts}`,
`variogram_scale`.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, object_refs, objects
from bf_core.geostat.variogram import store as ms

from . import store_base as sb
from .store_check import Prepared, freshness, inputs_of, prepare
from .store_plan import (
    check_plan,
    plan_create,
    plan_delete,
    plan_get,
    plan_list,
    plan_update,
    plans_with,
    remove_item,
)

if TYPE_CHECKING:
    from bf_core.storage.blobs import BlobStore

    from .spec import EstimationSpec

KIND, PLAN = sb.KIND, sb.PLAN
ROLE_TREND, ROLE_FILTER = sb.ROLE_TREND, sb.ROLE_FILTER
LINEAGE_KIND = "object:estimation"
#: the keys of the body of a row that `PUT` replaces whole (the others — `name` — are handled apart)
KEYS = ("model_id", "target", "sub", "disc", "samples", "method", "variogram", "variograms", "trend_id", "search",
        "outputs")  # fmt: skip

__all__ = ["KIND", "PLAN", "Prepared", "check_plan", "create", "delete", "freshness", "get", "inputs_of",
           "list_rows", "plan_create", "plan_delete", "plan_get", "plan_list", "plan_update", "prepare",
           "record_run", "update", "usage_of_variogram"]  # fmt: skip


# --- the graph (ADR 0025) ---------------------------------------------------------------------------------------------


def _lineage(conn: sa.Connection, estimation_id: int, inputs: list[dict[str, Any]]) -> None:
    """`lineage.record(conn, dst, inputs)` of 18.Г1 — the row is a node `("object:estimation", id)`; no module yet —
    nothing (the point where the orchestrator's merge connects the graph)."""
    try:
        lineage = importlib.import_module("bf_core.lineage")
    except ModuleNotFoundError as e:
        if e.name != "bf_core.lineage":
            raise
        return
    lineage.record(conn, lineage.Node(LINEAGE_KIND, estimation_id), [_node(lineage, i) for i in inputs])


def _node(lineage: Any, ref: Mapping[str, Any]) -> Any:
    """An entry of `meta.inputs` → a `lineage.Node`: the graph takes the fingerprint and name itself (one form of
    `fp` for every kind); a field of a block model `"<model>:<name>"` → `Node("bm_field", model, name.casefold())`."""
    kind, ident = str(ref["kind"]), ref["id"]
    if kind == "bm_field":
        model, _, name = str(ident).partition(":")
        return lineage.Node(kind, int(model), name.casefold())
    return lineage.Node(kind, int(ident))


# --- writing ----------------------------------------------------------------------------------------------------------


def default_name(spec: EstimationSpec) -> str:
    """«‹поле› · ‹МЕТОД› · ‹домен›»: «AU · OK · окисл» (no domain of the blocks — «AU · OK»)."""
    parts = [spec.samples.field, spec.method.kind.upper()]
    if spec.target.label:
        parts.append(spec.target.label)
    return " · ".join(parts)


def _versions() -> dict[str, str | None]:
    return ms._versions()


def _sync_refs(conn: sa.Connection, estimation_id: int, spec: EstimationSpec) -> None:
    """`object_refs` to the trend and the filter the row names."""
    conn.execute(sa.delete(object_refs).where(
        object_refs.c.src_id == estimation_id, object_refs.c.role.in_((ROLE_TREND, ROLE_FILTER))))  # fmt: skip
    for dst, role in ((spec.trend_id, ROLE_TREND), (spec.target.filter_id, ROLE_FILTER)):
        if dst is not None:
            conn.execute(sa.insert(object_refs).values(src_id=estimation_id, dst_id=dst, role=role))


def _meta(spec: EstimationSpec, name: str, prep: Prepared, *, last_run: Any, created: str) -> dict[str, Any]:
    return {"v": 1, **{**spec.canonical(), "name": name}, "origin": prep.origin, "inputs": prep.inputs,
            "algo": sb.ALGO, "versions": _versions(), "created_at": created, "last_run": last_run}  # fmt: skip


def create(conn: sa.Connection, store: BlobStore | None, project: Any, spec: EstimationSpec,
           author: str | None = None, *, prepared: Prepared | None = None) -> dict[str, Any]:  # fmt: skip
    """A row of the estimation (in the caller's write transaction): `{id, name, version: 1}`. `store` is the blob
    store of the project (the row owns no file yet). `prepared` — the result of `prepare` made before the
    transaction (the samples are read outside it)."""
    prep = prepared or prepare(conn, project, spec)
    final = sb.final_name(conn, KIND, prep.spec.name, default_name(prep.spec))
    now = dialect.utcnow()
    meta = _meta(prep.spec, final, prep, last_run=None, created=now.isoformat())
    res = conn.execute(sa.insert(objects).values(
        kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="ready",
        version=1, meta=meta, author=author, created_at=now, updated_at=now))  # fmt: skip
    ident = int(res.inserted_primary_key[0])  # type: ignore[index]
    _sync_refs(conn, ident, prep.spec)
    _lineage(conn, ident, prep.inputs)
    return {"id": ident, "name": final, "version": 1}


def update(conn: sa.Connection, store: BlobStore | None, project: Any, estimation_id: int, raw: Mapping[str, Any],
           expected_version: Any, *, prepared: Prepared | None = None) -> dict[str, Any]:  # fmt: skip
    """The keys of `raw` replace those stored (top level, as given); the result is checked again, the copies of the
    variogram models are taken anew (a new version of a model clears «устарел»), `origin` is recalculated when the
    samples changed. `expected_version` is required: another one — 409 `version_conflict {version}` (also when
    another window writes between the reading and the update). A row in a plan keeps its block model."""
    from .spec import parse_estimation

    row = sb.stored(conn, KIND, estimation_id)
    version = ms.clean_version(expected_version)
    if int(row.version) != version:
        raise sb.conflict(int(row.version))
    meta = dict(row.meta or {})
    old = sb.load_spec(meta)
    body = {k: meta[k] for k in KEYS if k in meta}
    body.update({k: raw[k] for k in KEYS if k in raw})
    new = parse_estimation(body)
    if new.model_id != old.model_id and plans_with(conn).get(estimation_id):
        raise sb.bad("model_id", "Строка входит в планы оценки: блочную модель сменить нельзя.",
                     "Уберите строку из планов или создайте новые параметры для другой модели.")  # fmt: skip
    prep = prepared or prepare(conn, project, new, old=old, old_origin=meta.get("origin"))
    final = str(row.name) if raw.get("name") is None else sb.final_name(conn, KIND, raw["name"], "", estimation_id)
    out = _meta(prep.spec, final, prep, last_run=meta.get("last_run"), created=str(meta.get("created_at") or ""))
    n = conn.execute(sa.update(objects).where(objects.c.id == estimation_id, objects.c.version == version).values(
        name=final, name_key=dialect.name_key(final), meta=out, version=version + 1,
        updated_at=dialect.utcnow())).rowcount  # fmt: skip
    if n != 1:
        raise sb.conflict(int(sb.stored(conn, KIND, estimation_id).version))
    _sync_refs(conn, estimation_id, prep.spec)
    _lineage(conn, estimation_id, prep.inputs)
    return {"id": estimation_id, "name": final, "version": version + 1}


def record_run(conn: sa.Connection, estimation_id: int, spec: EstimationSpec, last_run: Mapping[str, Any],
               inputs: list[dict[str, Any]], origin: list[float] | None = None) -> None:  # fmt: skip
    """The job wrote the fields (in its transaction): `meta.last_run` = `last_run` + `params` (the hash of the
    parameters it ran with: a later `PUT` that changes them makes the row «устарел»), `meta.inputs` and, after a
    «Перестроить», `origin`. The version does not change: this is not an edit of a window."""
    row = sb.stored(conn, KIND, estimation_id)
    meta = dict(row.meta or {})
    meta["last_run"] = {**last_run, "params": sb.params_hash(spec)}
    meta["inputs"] = inputs
    if origin is not None:
        meta["origin"] = [float(v) for v in origin]
    conn.execute(sa.update(objects).where(objects.c.id == estimation_id).values(meta=meta))
    _lineage(conn, estimation_id, inputs)


def delete(conn: sa.Connection, estimation_id: int) -> dict[str, Any]:
    """The row goes: out of its plans (their `version` + 1; the ids are in the answer for the events), its blob
    `xval` and its references. The fields it wrote stay in the model."""
    sb.stored(conn, KIND, estimation_id)
    plans = remove_item(conn, estimation_id)
    conn.execute(sa.delete(blob_owners).where(
        blob_owners.c.owner_kind == sb.OWNER, blob_owners.c.owner_id == estimation_id))  # fmt: skip
    conn.execute(sa.delete(objects).where(objects.c.id == estimation_id))  # `object_refs` of the row go by cascade
    from bf_core import lineage

    lineage.forget(conn, lineage.Node(LINEAGE_KIND, estimation_id))  # a reused id inherits no pin and no inputs
    return {"deleted": estimation_id, "plans": plans}


def field_owner(conn: sa.Connection, node: Any) -> Any | None:
    """The row that fills a field of an estimation (`node` — `bm_field`: the model and the casefolded name): of the
    rows the graph records as its inputs (they follow the ids of a `.bfggis` import, `source.params` do not), the one
    whose prefix starts the name of the field; None — the field is not one of an estimation or the row is gone. Of the
    rows of one prefix (`field_rows`) — the first that is «устарел» by its own check: its job is what makes the field
    current (the job of another row would carry the old blocks of this one, and the field would stay «устарел»)."""
    rows = field_rows(conn, node)
    if len(rows) > 1:  # the rows of one prefix: «Перестроить» runs the one that is «устарел», not the first by habit
        from .store_check import freshness

        for n in rows:
            meta = conn.execute(sa.select(objects.c.meta).where(objects.c.id == n.id, objects.c.kind == KIND)).scalar()
            if meta is not None and freshness(conn, meta)["stale"]:
                return n
    return rows[0] if rows else None


def field_rows(conn: sa.Connection, node: Any) -> list[Any]:
    """Every row that fills a field of an estimation, in the order of the graph: the rows of one prefix share the
    field, each its own domain (№ 77), so a field of a plan has several (`field_owner` is the first of them: one job
    to rebuild the field). Empty — the field is not one of an estimation or the rows are gone."""
    from bf_core.blocks import schema as bs
    from bf_core.lineage import store as ls

    meta = conn.execute(sa.select(objects.c.meta).where(
        objects.c.kind == bs.KIND, objects.c.id == node.id)).scalar()  # fmt: skip
    field = next((f for f in (meta or {}).get("fields") or [] if str(f.get("name", "")).casefold() == node.sub), None)
    if ((field or {}).get("source") or {}).get("op") != "estimate":
        return []
    out = []
    for ref in ls.inputs_of(conn, [node]).get(node, []):
        if ref.node.kind != LINEAGE_KIND:
            continue
        got = conn.execute(sa.select(objects.c.meta).where(
            objects.c.id == ref.node.id, objects.c.kind == sb.KIND)).scalar()  # fmt: skip
        prefix = str(((got or {}).get("outputs") or {}).get("prefix") or "").casefold()
        if got is not None and prefix and node.sub.startswith(prefix):
            out.append(ref.node)
    return out


# --- reading ----------------------------------------------------------------------------------------------------------


def _last(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    last = meta.get("last_run")
    if not isinstance(last, Mapping):
        return None
    return {"at": last.get("at"), "by_status": (last.get("counts") or {}).get("by_status")}


def list_rows(conn: sa.Connection, model_id: int | None = None) -> list[dict[str, Any]]:
    """The rows by name: `{id, name, model_id, method, prefix, label, version, stale, stale_reason, source_lost,
    plans, last_run {at, by_status} | null, created_at, updated_at}`."""
    plans = plans_with(conn)
    out = []
    for r in conn.execute(sa.select(objects).where(objects.c.kind == KIND).order_by(objects.c.name_key, objects.c.id)):
        meta = r.meta or {}
        if model_id is not None and meta.get("model_id") != model_id:
            continue
        out.append({"id": int(r.id), "name": r.name, "model_id": meta.get("model_id"),
                    "method": (meta.get("method") or {}).get("kind"),
                    "prefix": (meta.get("outputs") or {}).get("prefix"),
                    "label": (meta.get("target") or {}).get("label"), "version": int(r.version),
                    **freshness(conn, meta), "plans": plans.get(int(r.id), []), "last_run": _last(meta),
                    "created_at": r.created_at, "updated_at": r.updated_at})  # fmt: skip
    return out


def _model_fields(conn: sa.Connection, model_id: Any) -> set[str]:
    if not isinstance(model_id, int):
        return set()
    meta = conn.execute(sa.select(objects.c.meta).where(
        objects.c.id == model_id, objects.c.kind == "block_model")).scalar()  # fmt: skip
    return {str(f["name"]).casefold() for f in (meta or {}).get("fields") or []}


def get(conn: sa.Connection, estimation_id: int) -> dict[str, Any]:
    """`{id, name, version, meta, columns, fields_exist, stale, stale_reason, source_lost, plans, created_at,
    updated_at}`: `columns` — the fields the row writes (`spec.columns()`), `fields_exist` — those the model has."""
    from bf_core.api.errors import ApiError

    row = sb.stored(conn, KIND, estimation_id)
    meta = dict(row.meta or {})
    try:
        columns = sb.load_spec(meta).columns()
    except ApiError:
        columns = []  # the parameters do not read (an old archive): the window shows the meta as it is
    have = _model_fields(conn, meta.get("model_id"))
    return {"id": int(row.id), "name": row.name, "version": int(row.version), "meta": meta, "columns": columns,
            "fields_exist": [c["name"] for c in columns if c["name"].casefold() in have], **freshness(conn, meta),
            "plans": plans_with(conn).get(int(row.id), []), "created_at": row.created_at,
            "updated_at": row.updated_at}  # fmt: skip


def usage_of_variogram(conn: sa.Connection, model_id: int) -> list[dict[str, Any]]:
    """«Где используется» of a variogram model: the rows that copied it — `[{id, kind: "estimation", name, role:
    "variogram"}]` by name. Not a ban on deleting the model (the row keeps its copy)."""
    out = []
    rows = conn.execute(sa.select(objects.c.id, objects.c.name, objects.c.meta).where(objects.c.kind == KIND)
                        .order_by(objects.c.name_key, objects.c.id)).all()  # fmt: skip
    for r in rows:
        meta = r.meta or {}
        refs = [meta.get("variogram"), *(meta.get("variograms") or [])]
        if any(isinstance(v, Mapping) and v.get("model_id") == model_id for v in refs):
            out.append({"id": int(r.id), "kind": KIND, "name": r.name, "role": "variogram"})
    return out
