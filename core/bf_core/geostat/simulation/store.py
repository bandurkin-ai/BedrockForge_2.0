"""Stored «Моделирование» (ТЗ 8.8.1; ADR 0024 §2, §9, решения 2–3, 18; ADR 0025; подшаг 18.32;
docs/progress/e18-api.md §2, §9): `objects(kind="simulation", parent_id=NULL)`, names unique among the simulations
(`name_key`), the realizations — the blob `real` (Parquet: p u64, dom i2, r001 … rNNN f4; `S/real.py`) of the row.
The checks against the project — `store_check.py`.

`meta` v1: the checked parameters (`spec.canonical()`), `origin` (the centre of the extent of the samples), `inputs`
[{kind, id, fp, name}] (the graph of ADR 0025), `algo` «sim-1», `versions`, `created_at`, `last_run` {at, job, nodes,
realizations, seconds, counts, params} (written by the job: `commit_run`; `params` — the hash of the parameters).

Copies and references (ADR 0024 решение 3): a variogram model is copied into the row (`variogram.snapshot`, `sha`,
`scale: y`, Д-38) — deleting or editing the model leaves the row as it is, a new version of the model is taken on `PUT`.
The block model has no reference: a simulation of a deleted model is read («источник удалён») and cannot run. Deleting a
simulation takes its blob (the garbage collector of `storage/gc.py` removes the file); the fields `simulate.post`
wrote stay in the block model.

After a write the graph gets `lineage.record(conn, ("object:simulation", id), inputs)`.

Errors: 404 `simulation_not_found`, `block_model_not_found`, `model_not_found`, `filter_not_found`, `set_not_found`,
`table_not_found`; 409 `name_taken`, `version_conflict {version}`; 422 `bad_param {param}`, `variable_grid`,
`field_not_found`, `domain_unmapped`, `no_samples {counts}`, `variogram_scale`, `not_ready`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, objects
from bf_core.geostat.estimate import store_base as sb
from bf_core.geostat.variogram import store as ms

from .spec import ALGO
from .store_check import (
    KIND,
    LINEAGE_KIND,
    OWNER,
    ROLE,
    Prepared,
    conflict,
    freshness,
    inputs_of,
    load_spec,
    params_hash,
    prepare,
    stored,
)

if TYPE_CHECKING:
    from bf_core.storage.blobs import BlobStore

    from .spec import SimulationSpec

#: the keys of the body of a simulation that `PUT` replaces whole (the others — `name` — are handled apart)
KEYS = ("kind", "model_id", "target", "samples", "variogram", "search", "nodes", "seed", "n_real", "secondary",
        "nscore")  # fmt: skip
PARQUET_MEDIA = "application/vnd.apache.parquet"

__all__ = ["KEYS", "KIND", "LINEAGE_KIND", "Prepared", "commit_run", "create", "default_name", "delete",
           "freshness", "get", "inputs_of", "list_rows", "prepare", "real_sha", "stored", "update",
           "usage_of_variogram"]  # fmt: skip


# --- the graph (ADR 0025) ---------------------------------------------------------------------------------------------


def _lineage(conn: sa.Connection, simulation_id: int, inputs: list[dict[str, Any]]) -> None:
    from bf_core import lineage

    lineage.record(conn, lineage.Node(LINEAGE_KIND, simulation_id), [_node(lineage, i) for i in inputs])


def _node(lineage: Any, ref: Mapping[str, Any]) -> Any:
    """An entry of `meta.inputs` → a `lineage.Node`; a field of a block model `"<model>:<name>"` →
    `Node("bm_field", model, name.casefold())`."""
    kind, ident = str(ref["kind"]), ref["id"]
    if kind == "bm_field":
        model, _, name = str(ident).partition(":")
        return lineage.Node(kind, int(model), name.casefold())
    return lineage.Node(kind, int(ident))


# --- writing ----------------------------------------------------------------------------------------------------------


def default_name(spec: SimulationSpec) -> str:
    """«‹поле› · ‹ВИД› · ‹домен›»: «AU · SGS · окисл» (no domain of the blocks — «AU · SGS»)."""
    parts = [spec.samples.field, spec.kind.upper()]
    if spec.target.label:
        parts.append(spec.target.label)
    return " · ".join(parts)


def _meta(spec: SimulationSpec, name: str, prep: Prepared, *, last_run: Any, created: str) -> dict[str, Any]:
    return {"v": 1, **{**spec.canonical(), "name": name}, "origin": prep.origin, "inputs": prep.inputs,
            "algo": ALGO, "versions": ms._versions(), "created_at": created, "last_run": last_run}  # noqa: SLF001


def create(conn: sa.Connection, project: Any, spec: SimulationSpec, author: str | None = None, *,
           prepared: Prepared | None = None) -> dict[str, Any]:  # fmt: skip
    """A simulation (in the caller's write transaction): `{id, name, version: 1}`. `prepared` — the result of
    `prepare` made before the transaction (the samples are read outside it)."""
    prep = prepared or prepare(conn, project, spec)
    final = sb.final_name(conn, KIND, prep.spec.name, default_name(prep.spec))
    now = dialect.utcnow()
    meta = _meta(prep.spec, final, prep, last_run=None, created=now.isoformat())
    res = conn.execute(sa.insert(objects).values(
        kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="ready",
        version=1, meta=meta, author=author, created_at=now, updated_at=now))  # fmt: skip
    ident = int(res.inserted_primary_key[0])  # type: ignore[index]
    _lineage(conn, ident, prep.inputs)
    return {"id": ident, "name": final, "version": 1}


def update(conn: sa.Connection, project: Any, simulation_id: int, raw: Mapping[str, Any], expected_version: Any, *,
           prepared: Prepared | None = None) -> dict[str, Any]:  # fmt: skip
    """The keys of `raw` replace those stored (top level, as given); the result is checked again, the copy of the
    variogram model is taken anew (a new version of a model clears «устарел»), `origin` is recalculated when the
    samples changed. `expected_version` is required: another one — 409 `version_conflict {version}` (also when
    another window writes between the reading and the update). The realizations stay; the row is «устарел» until
    it runs again."""
    from .spec import parse_simulation

    row = stored(conn, simulation_id)
    version = ms.clean_version(expected_version)
    if int(row.version) != version:
        raise conflict(int(row.version))
    meta = dict(row.meta or {})
    old = load_spec(meta)
    body = {k: meta[k] for k in KEYS if k in meta}
    body.update({k: raw[k] for k in KEYS if k in raw})
    prep = prepared or prepare(conn, project, parse_simulation(body), old=old, old_origin=meta.get("origin"))
    final = str(row.name) if raw.get("name") is None else sb.final_name(conn, KIND, raw["name"], "", simulation_id)
    out = _meta(prep.spec, final, prep, last_run=meta.get("last_run"), created=str(meta.get("created_at") or ""))
    n = conn.execute(sa.update(objects).where(objects.c.id == simulation_id, objects.c.version == version).values(
        name=final, name_key=dialect.name_key(final), meta=out, version=version + 1,
        updated_at=dialect.utcnow())).rowcount  # fmt: skip
    if n != 1:
        raise conflict(int(stored(conn, simulation_id).version))
    _lineage(conn, simulation_id, prep.inputs)
    return {"id": simulation_id, "name": final, "version": version + 1}


def commit_run(conn: sa.Connection, store: BlobStore, simulation_id: int, staged: tuple[Any, str, int],
               last_run: Mapping[str, Any], inputs: list[dict[str, Any]], origin: list[float]) -> str:  # fmt: skip
    """The job wrote the realizations (in its transaction): the blob `real` replaces the old one, `meta.last_run` =
    `last_run` + `params` (the hash of the parameters of the row it ran with), `meta.inputs`, `origin`; the graph. The
    version does not change: this is not an edit of a window. Returns the SHA-256 of the blob."""
    row = stored(conn, simulation_id)
    meta = dict(row.meta or {})
    meta["last_run"] = {**last_run, "params": params_hash(load_spec(meta))}
    meta["inputs"] = inputs
    meta["origin"] = [float(v) for v in origin]
    conn.execute(sa.delete(blob_owners).where(
        blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == simulation_id, blob_owners.c.role == ROLE))
    sha = store.commit_staged(conn, staged, media_type=PARQUET_MEDIA, owner_kind=OWNER, owner_id=simulation_id,
                              role=ROLE)  # fmt: skip
    conn.execute(sa.update(objects).where(objects.c.id == simulation_id).values(meta=meta))
    _lineage(conn, simulation_id, inputs)
    return sha


def delete(conn: sa.Connection, simulation_id: int) -> dict[str, Any]:
    """The simulation goes with its blob `real` (the file — with the garbage collection). The fields it wrote stay in
    the block model; the graph forgets the node."""
    stored(conn, simulation_id)
    conn.execute(sa.delete(blob_owners).where(
        blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == simulation_id))  # fmt: skip
    conn.execute(sa.delete(objects).where(objects.c.id == simulation_id))
    from bf_core import lineage

    lineage.forget(conn, lineage.Node(LINEAGE_KIND, simulation_id))  # a reused id inherits no pin and no inputs
    return {"deleted": simulation_id}


# --- reading ----------------------------------------------------------------------------------------------------------


def real_sha(conn: sa.Connection, simulation_id: int) -> str | None:
    """SHA-256 of the blob `real` of the row; None — it has not run yet."""
    got = conn.execute(sa.select(blob_owners.c.sha256).where(
        blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == simulation_id,
        blob_owners.c.role == ROLE)).scalar()  # fmt: skip
    return None if got is None else str(got)


def _last(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    last = meta.get("last_run")
    if not isinstance(last, Mapping):
        return None
    return {"at": last.get("at"), "nodes": last.get("nodes"), "realizations": last.get("realizations")}


def list_rows(conn: sa.Connection, model_id: int | None = None) -> list[dict[str, Any]]:
    """The simulations by name: `{id, name, kind, model_id, label, field, n_real, seed, version, stale, stale_reason,
    source_lost, last_run {at, nodes, realizations} | null, created_at, updated_at}`."""
    out = []
    for r in conn.execute(sa.select(objects).where(objects.c.kind == KIND).order_by(objects.c.name_key, objects.c.id)):
        meta = r.meta or {}
        if model_id is not None and meta.get("model_id") != model_id:
            continue
        out.append({"id": int(r.id), "name": r.name, "kind": meta.get("kind"), "model_id": meta.get("model_id"),
                    "label": (meta.get("target") or {}).get("label"),
                    "field": (meta.get("samples") or {}).get("field"), "n_real": meta.get("n_real"),
                    "seed": meta.get("seed"), "version": int(r.version), **freshness(conn, meta),
                    "last_run": _last(meta), "created_at": r.created_at, "updated_at": r.updated_at})  # fmt: skip
    return out


def get(conn: sa.Connection, simulation_id: int) -> dict[str, Any]:
    """`{id, name, version, meta, file, stale, stale_reason, source_lost, created_at, updated_at}`; `file` —
    `{role: "real", sha, rows, columns}` of the realizations, null before the first run."""
    from .real import column_names

    row = stored(conn, simulation_id)
    meta = dict(row.meta or {})
    sha = real_sha(conn, simulation_id)
    last = meta.get("last_run") or {}
    file = None
    if sha is not None and last:
        file = {"role": ROLE, "sha": sha, "rows": last.get("nodes"),
                "columns": column_names(int(last.get("realizations") or 0))}  # fmt: skip
    return {"id": int(row.id), "name": row.name, "version": int(row.version), "meta": meta, "file": file,
            **freshness(conn, meta), "created_at": row.created_at, "updated_at": row.updated_at}


def usage_of_variogram(conn: sa.Connection, model_id: int) -> list[dict[str, Any]]:
    """«Где используется» of a variogram model: the simulations that copied it — `[{id, kind: "simulation", name, role:
    "variogram"}]` by name. Not a ban on deleting the model (the row keeps its copy)."""
    out = []
    rows = conn.execute(sa.select(objects.c.id, objects.c.name, objects.c.meta).where(objects.c.kind == KIND)
                        .order_by(objects.c.name_key, objects.c.id)).all()  # fmt: skip
    for r in rows:
        v = (r.meta or {}).get("variogram")
        if isinstance(v, Mapping) and v.get("model_id") == model_id:
            out.append({"id": int(r.id), "kind": KIND, "name": r.name, "role": "variogram"})
    return out
