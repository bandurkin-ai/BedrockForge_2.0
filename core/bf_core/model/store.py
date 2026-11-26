"""Stored implicit models (ТЗ 8.9; ADR 0026 §7 решения 22–26; подшаг 19.11; docs/progress/e19-api.md §2, §5, §11):
`objects(kind="implicit_model", parent_id=NULL)`, names unique among the models (`name_key`); the files — the blobs
`implicit_points`, `implicit_patches`, `implicit_solution` (`store_blobs.py`); the outputs — wireframes that are the
children of the model (`parent_id`, `meta.source {op: "implicit", model_id, group, wing?, threshold?}`); the checks
against the project, the inputs of the graph and «устарела» — `store_check.py`.

`meta` v1 (e19-api §2): the checked parameters (`spec.canonical()`) and `v`, `algo`, `versions`, `created_at`,
`extent` (the box of the boundary; `objects.extent` too), `state` (`ready` | `building` | `error`), `error` {code,
message, hint, group?} | null, `outputs`
[{wireframe_id, kind, group, wing, threshold?, name}], `stats`, `inputs` [{kind, id, fp, name}], `last_run`
{at, job, mode, seconds, params} | null (written by the job: `record_run`; `params` is the hash of the parameters it
ran with: a later `PUT` that changes them makes the model «устарела»).

`h` is what the user gave (None — «авто»): `h_used` is the step the model is built with now (`spec.resolved`); a `PUT`
that moves the boundary and gives no `h` takes the step again. «Идёт построение» is `meta.state == "building"` with
`meta.job_id` (the job that runs) and `meta.back` {state, error} (what the model goes back to); `objects.state` and
`objects.job_id` of a model stay `ready` / NULL — the clean-ups of the jobs and of the project snapshot delete every
`building` row of `objects`, the model with its outputs among them. The job marks the model in its own first
transaction (`job._begin`), the route only checks; a mark whose job is gone (cancelled in the queue, killed, a crash)
is not «идёт построение» (`store_base.is_building`) and the next write takes it back to `back`.

References (ADR 0026 решение 24): the trend is `object_refs(src=model, dst=trend, role="trend")` — a trend with
such rows is not deleted (409 `in_use`, `geostat/trend_store.py`); the other inputs are not references: deleting
one leaves the model readable, «источник удалён». Deleting the model deletes its outputs (and their rows of the
graph); a copy `to_wireframe` has no parent and stays.

After every write the graph gets `lineage.record(conn, ("object:implicit_model", id), inputs)`; the outputs are
recorded with the model as their input (`record_run`).

Errors: 404 `model_not_found`, `set_not_found`, `table_not_found`, `wireframe_not_found`, `dtm_not_found`,
`trend_not_found`, `layer_not_found`, `result_not_found`; 409 `name_taken`, `version_conflict {version}`,
`model_building`; 422 `bad_param {param}`, `field_not_found`, `wireframe_open`, `boundary_empty`, `too_many_points`.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from dataclasses import replace
from importlib import metadata
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import blob_owners, object_refs, objects, wireframes
from bf_core.geostat.estimate import store_base as eb
from bf_core.geostat.variogram import store as ms

from . import store_base as sb
from .spec import parse_model
from .store_base import KIND
from .store_blobs import (
    GroupData,
    commit,
    discard,
    load_fields,
    read_patches,
    read_points,
    read_solution,
    shas_of,
    stage_patches,
    stage_points,
    stage_solution,
)
from .store_check import Prepared, freshness, inputs_of, prepare

if TYPE_CHECKING:
    from bf_core.storage.blobs import BlobStore
    from bf_core.wireframes.store import StagedWireframe

    from .spec import ModelSpec

LINEAGE_KIND = sb.LINEAGE_KIND
STATES = ("ready", "building", "error")
PACKAGES = (("numpy", "numpy"), ("scipy", "scipy"), ("numba", "numba"), ("skimage", "scikit-image"),
            ("manifold3d", "manifold3d"))  # fmt: skip
#: the keys of the body of a model that `PUT` replaces whole (`name` — handled apart)
KEYS = ("boundary", "h", "levels", "kernel", "kernel_radius", "smoothing", "trend_id", "anisotropy", "pou", "data",
        "groups", "manual", "post")  # fmt: skip

__all__ = ["KEYS", "KIND", "LINEAGE_KIND", "GroupData", "Prepared", "begin", "commit", "commit_output", "create",
           "delete", "discard", "freshness", "get", "inputs_of", "list_rows", "load_fields", "load_spec", "output_meta",
           "outputs_of", "prepare", "read_patches", "read_points", "read_solution", "record_run", "replace_output",
           "set_state", "shas_of", "stage_patches", "stage_points", "stage_solution", "update"]  # fmt: skip

load_spec = sb.load_spec


# --- the graph (ADR 0025) ---------------------------------------------------------------------------------------------


def _lineage(conn: sa.Connection, model_id: int, inputs: Sequence[Mapping[str, Any]], *, keep: bool = False) -> None:
    """`lineage.record(conn, dst, inputs)`: the model is a node `("object:implicit_model", id)`; the graph takes the
    fingerprint and the name of every input itself (one form of `fp` for every kind). `keep`: an edit of a model that
    was built — an input the graph already has keeps the fingerprint of the last run, so a changed input stays
    «устарела» (only a new input gets its fingerprint as it is now)."""
    from bf_core import lineage
    from bf_core.lineage import store as lstore

    dst = lineage.Node(LINEAGE_KIND, model_id)
    old = {r.node: r for r in lstore.inputs_of(conn, [dst]).get(dst, [])} if keep else {}
    refs = lineage.record(conn, dst, [lineage.Node(str(i["kind"]), int(i["id"])) for i in inputs])
    if old:
        lstore.write_inputs(conn, dst, [lineage.InputRef(r.node, old[r.node].fp, r.name) if r.node in old else r
                                        for r in refs])  # fmt: skip


def _kept_inputs(meta: Mapping[str, Any], inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`inputs` of an edit of a model that was built: an input the last run had keeps its `fp` (what the run saw), the
    name is the current one; a new input has the fingerprint as it is now. A model never built — all as they are."""
    if not meta.get("last_run"):
        return inputs
    old = {(str(i.get("kind")), i.get("id")): i for i in meta.get("inputs") or [] if isinstance(i, Mapping)}
    return [{**i, "fp": old[(i["kind"], i["id"])]["fp"]} if "fp" in old.get((i["kind"], i["id"]), {}) else i
            for i in inputs]  # fmt: skip


# --- writing ----------------------------------------------------------------------------------------------------------


def _versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key, package in PACKAGES:
        try:
            out[key] = metadata.version(package)
        except metadata.PackageNotFoundError:
            out[key] = None
    return out


def _sync_refs(conn: sa.Connection, model_id: int, spec: ModelSpec) -> None:
    """`object_refs` to the trend the model names."""
    conn.execute(sa.delete(object_refs).where(object_refs.c.src_id == model_id, object_refs.c.role == sb.ROLE_TREND))
    if spec.trend_id is not None:
        conn.execute(sa.insert(object_refs).values(src_id=model_id, dst_id=spec.trend_id, role=sb.ROLE_TREND))


def _meta(spec: ModelSpec, name: str, prep: Prepared, *, created: str, **kept: Any) -> dict[str, Any]:
    """`kept`: what the job wrote (`state`, `error`, `outputs`, `stats`, `last_run`, `versions`). `spec` is what the
    user gave (`h` None — «авто»): `h` stays as given, the step in use is `h_used`."""
    base: dict[str, Any] = {"state": "ready", "error": None, "outputs": [], "stats": None, "last_run": None,
                            "versions": _versions()}  # fmt: skip
    return {"v": 1, **{**spec.canonical(), "name": name}, "h_used": prep.spec.h, "algo": sb.ALGO,
            "created_at": created, "extent": prep.extent, "inputs": prep.inputs, **{**base, **kept}}  # fmt: skip


def create(conn: sa.Connection, store: BlobStore | None, project: Any, spec: ModelSpec, author: str | None = None,
           *, prepared: Prepared | None = None, points: int | None = None) -> dict[str, Any]:  # fmt: skip
    """A model (in the caller's write transaction): `{id, name, version: 1}`. The references are checked, the extent
    of the boundary and h are resolved, the inputs and the reference to the trend are written. `store` and `project`
    are not needed yet (the row owns no file); they keep the form of the stores of Э18. `points` — see `prepare`."""
    prep = prepared or prepare(conn, spec, points=points)
    final = eb.final_name(conn, KIND, prep.spec.name, sb.DEFAULT_NAME)
    now = dialect.utcnow()
    meta = _meta(replace(prep.spec, h=spec.h), final, prep, created=now.isoformat())
    res = conn.execute(sa.insert(objects).values(
        kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="ready",
        version=1, meta=meta, extent=prep.extent, author=author, created_at=now, updated_at=now))  # fmt: skip
    ident = int(res.inserted_primary_key[0])  # type: ignore[index]
    _sync_refs(conn, ident, prep.spec)
    _lineage(conn, ident, prep.inputs)
    return {"id": ident, "name": final, "version": 1}


def update(conn: sa.Connection, store: BlobStore | None, project: Any, model_id: int, raw: Mapping[str, Any],
           expected_version: Any, *, prepared: Prepared | None = None,
           points: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The keys of `raw` (`KEYS`, `name`) replace those stored (top level, as given: `groups` is the whole list);
    the result is checked again. `expected_version` is required (422 `bad_param {param: "expected_version"}`):
    another one — 409 `version_conflict {version}` (also when another window writes between the reading and the
    update); a model that is being built — 409 `model_building`. The result of the last build stays (the model is
    «устарела» by its parameters until it is built again), and so do the fingerprints of its inputs: an edit does not
    make a changed input look unchanged (`_kept_inputs`). `h` is what the user gave: a boundary changed without `h`
    takes the step again."""
    row = sb.stored(conn, model_id)
    sb.refuse_building(conn, row)
    version = ms.clean_version(expected_version)
    if int(row.version) != version:
        raise sb.conflict(int(row.version))
    meta = sb.settled(row.meta)  # a mark whose job is gone is not kept
    body = {k: meta[k] for k in KEYS if k in meta}
    body.update({k: raw[k] for k in KEYS if k in raw})
    new = parse_model({**body, "name": meta.get("name")})
    prep = prepared or prepare(conn, new, points=points)
    final = str(row.name) if raw.get("name") is None else eb.final_name(conn, KIND, raw["name"], "", model_id)
    kept = {k: meta.get(k) for k in ("state", "error", "outputs", "stats", "last_run", "versions") if k in meta}
    prep = replace(prep, inputs=_kept_inputs(meta, prep.inputs))
    out = _meta(replace(prep.spec, h=new.h), final, prep, created=str(meta.get("created_at") or ""), **kept)
    n = conn.execute(sa.update(objects).where(objects.c.id == model_id, objects.c.version == version).values(
        name=final, name_key=dialect.name_key(final), meta=out, extent=prep.extent, version=version + 1,
        updated_at=dialect.utcnow())).rowcount  # fmt: skip
    if n != 1:
        raise sb.conflict(int(sb.stored(conn, model_id).version))
    _sync_refs(conn, model_id, prep.spec)
    _lineage(conn, model_id, prep.inputs, keep=bool(meta.get("last_run")))
    return {"id": model_id, "name": final, "version": version + 1}


def set_state(conn: sa.Connection, model_id: int, state: str, *, error: Mapping[str, Any] | None = None,
              job_id: int | None = None) -> None:  # fmt: skip
    """The job begins (`building`, its id) or ends without a result (`ready` / `error` with `error {code, message,
    hint, group?}`): `meta.state`, `meta.error`, `meta.job_id` and `meta.back` — all in `meta`; `objects.state` and
    `objects.job_id` are not touched (see the module text). The version does not change: this is not an edit of a
    window."""
    if state not in STATES:
        raise ValueError(f"not a state of an implicit model: {state!r}")
    row = sb.stored(conn, model_id)
    meta = dict(row.meta or {})
    if state == "building":
        back_state, back_error = sb.back_of(meta)  # the first mark keeps what the model goes back to
        meta.update(state=state, error=None, job_id=job_id, back={"state": back_state, "error": back_error})
    else:
        meta.pop("job_id", None)
        meta.pop("back", None)
        meta.update(state=state, error=dict(error) if error else None)
    conn.execute(sa.update(objects).where(objects.c.id == model_id).values(meta=meta))


def begin(conn: sa.Connection, model_id: int, expected_version: Any, job_id: int | None = None) -> Any:
    """The model is checked (404, 409 `version_conflict`, `model_building`) and marked `building`; returns the row as
    it was. The route does not call it (it only checks: `check_startable`); the job marks its model itself."""
    row = check_startable(conn, model_id, expected_version)
    set_state(conn, model_id, "building", job_id=job_id)
    return row


def check_startable(conn: sa.Connection, model_id: int, expected_version: Any) -> Any:
    """What the route checks before it queues a job: 404 `model_not_found`, 409 `model_building`, `version_conflict`;
    returns the row."""
    row = sb.stored(conn, model_id)
    sb.refuse_building(conn, row)
    version = ms.clean_version(expected_version)
    if int(row.version) != version:
        raise sb.conflict(int(row.version))
    return row


def record_run(conn: sa.Connection, model_id: int, spec: ModelSpec, last_run: Mapping[str, Any],
               inputs: list[dict[str, Any]], *, outputs: list[dict[str, Any]], stats: Mapping[str, Any] | None,
               state: str = "ready", error: Mapping[str, Any] | None = None,
               versions: Mapping[str, Any] | None = None, extent: Sequence[float] | None = None) -> None:  # fmt: skip
    """The job wrote the files and the outputs (in its transaction, after `commit` and `commit_output`):
    `meta.last_run` = `last_run` + `params` (the hash of the parameters it ran with), `meta.inputs`, `outputs`,
    `stats`, `state`, `error`, `extent` (what the run read, when it says); the outputs get the model as their input in
    the graph. `last_run.params` is the hash of the parameters as the user gave them (`h` None stays None), as
    `freshness` reads them. The version does not change."""
    if state not in STATES or state == "building":
        raise ValueError(f"not a final state of an implicit model: {state!r}")
    row = sb.stored(conn, model_id)
    meta = dict(row.meta or {})
    given = sb.load_spec(meta)  # `h` as the user gave it: `freshness` hashes the stored meta the same way
    meta.pop("job_id", None)
    meta.pop("back", None)
    meta.update(last_run={**last_run, "params": sb.params_hash(replace(spec, h=given.h))}, inputs=inputs,
                outputs=outputs, stats=None if stats is None else dict(stats), state=state,
                error=dict(error) if error else None,
                versions=dict(versions) if versions is not None else _versions())  # fmt: skip
    values: dict[str, Any] = {"meta": meta}
    if extent is not None:
        meta.update(extent=[float(v) for v in extent], h_used=spec.h)
        values["extent"] = meta["extent"]
    conn.execute(sa.update(objects).where(objects.c.id == model_id).values(**values))
    _lineage(conn, model_id, inputs)
    from bf_core import lineage

    for o in outputs:
        wid = o.get("wireframe_id")
        if isinstance(wid, int) and not isinstance(wid, bool):
            lineage.record(conn, lineage.Node("wireframe", wid), [lineage.Node(LINEAGE_KIND, model_id)])


def delete(conn: sa.Connection, store: BlobStore | None, model_id: int) -> dict[str, Any]:
    """The model goes with its outputs (the children), its blobs, its references and its rows of the graph. A model
    that is being built — 409 `model_building`; an output other objects refer to — 422 `wireframe_in_use` (the whole
    deletion is refused). Returns `{deleted, wireframes: [id], removed: [{id, shas, render, shared}]}`: `removed` — what
    `wireframes.store.delete_wireframe` says of each output, for the caller that drops the levels of detail of a
    geometry nobody has any more (`wireframes/router.py`); `line_files` — the files of lines (the crossing of the veins
    of a system, `store_lines`) that went with it, `line_layers` — their layers."""
    from bf_core import lineage
    from bf_core.wireframes import store as wf
    from bf_core.wireframes.cloud_dz import forget_surface

    from . import store_lines

    row = sb.stored(conn, model_id)
    sb.refuse_building(conn, row)
    kids = [int(i) for i in conn.execute(sa.select(objects.c.id).where(
        objects.c.parent_id == model_id, objects.c.kind == wf.WIREFRAME).order_by(objects.c.id)).scalars()]  # fmt: skip
    removed = []
    for wid in kids:
        removed.append(wf.delete_wireframe(conn, wid))
        forget_surface(conn, wid)
        lineage.forget(conn, lineage.Node("wireframe", wid))
    files = store_lines.files_of(conn, model_id)
    gone = store_lines.drop(conn, model_id)
    layers = [i for i in gone if i not in files]
    conn.execute(sa.delete(blob_owners).where(
        blob_owners.c.owner_kind == sb.OWNER, blob_owners.c.owner_id == model_id))  # fmt: skip
    conn.execute(sa.delete(objects).where(objects.c.id == model_id))  # `object_refs` of the model go by cascade
    lineage.forget(conn, lineage.Node(LINEAGE_KIND, model_id))  # a reused id inherits no pin and no inputs
    if store is not None:
        shutil.rmtree(store.root.parent / "cache" / "implicit" / str(model_id), ignore_errors=True)
    return {"deleted": model_id, "wireframes": kids, "removed": removed, "line_files": files, "line_layers": layers}


# --- the outputs ------------------------------------------------------------------------------------------------------


#: what an output of Э19б tells besides its group and wing (`extra` of `commit_output`): the code of a vein of a system,
#: the common body of a system, the body that is the space of a wing of a fault (not rock)
EXTRA_KEYS = ("code", "common", "region")


def output_meta(model_id: int, group: int | None, wing: int | None = None, threshold: float | None = None,
                extra: Mapping[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """`meta` of a wireframe that is an output: `source {op: "implicit", model_id, group, wing?, threshold?, code?,
    common?, region?}` (ADR 0026 решение 24). `group` None — «Прочее»."""
    source: dict[str, Any] = {"op": "implicit", "model_id": model_id, "group": group}
    if wing is not None:
        source["wing"] = wing
    if threshold is not None:
        source["threshold"] = threshold
    source.update({k: v for k, v in (extra or {}).items() if k in EXTRA_KEYS and v})
    return {"source": source}


def commit_output(conn: sa.Connection, store: BlobStore, staged: StagedWireframe, model_id: int, *, name: str,
                  kind: str, group: int | None, wing: int | None = None, threshold: float | None = None,
                  color: str | None = None, author: str | None = None,
                  extra: Mapping[str, Any] | None = None) -> dict[str, Any]:  # fmt: skip
    """Inside the write transaction: a wireframe child of the model (`commit_wireframe(parent_id=model)`), its name
    made free among the wireframes. Returns the entry of `meta.outputs`: `{wireframe_id, kind, group, wing,
    threshold?, name, code?, common?, region?}`."""
    from bf_core.wireframes import store as wf

    final = wf.free_name(conn, name)
    wid = wf.commit_wireframe(conn, store, staged, name=final, color=color, parent_id=model_id, author=author,
                              meta=output_meta(model_id, group, wing, threshold, extra))  # fmt: skip
    entry: dict[str, Any] = {"wireframe_id": wid, "kind": kind, "group": group, "wing": wing, "name": final}
    if threshold is not None:
        entry["threshold"] = threshold
    entry.update({k: v for k, v in (extra or {}).items() if k in EXTRA_KEYS and v})
    return entry


def replace_output(conn: sa.Connection, store: BlobStore, staged: StagedWireframe, model_id: int, wireframe_id: int,
                   *, name: str, kind: str, group: int | None, wing: int | None = None,
                   threshold: float | None = None,
                   extra: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:  # fmt: skip
    """Inside the write transaction: the geometry of an output of the model replaced in place («Обновить», e19-api §2):
    the same id and the same colour and attributes, the new blobs, the rows `objects` (extent, name, `meta.source`,
    version + 1) and `wireframes` (counts, origin, stats, part table). A wireframe is immutable (`wireframes/store.py`
    writes a geometry once): this is the one place that changes it, for the children of a model only (404 when it
    is not one of them). Returns (the entry of `meta.outputs`, what `wireframes.store.delete_wireframe` says of the
    geometry that went — `{id, shas, render, shared}` — for the caller that drops its levels of detail)."""
    from bf_core.wireframes import store as wf

    row = conn.execute(sa.select(objects.c.version, objects.c.meta, objects.c.parent_id).where(
        objects.c.id == wireframe_id, objects.c.kind == wf.WIREFRAME)).first()  # fmt: skip
    if row is None or row.parent_id != model_id:
        raise wf.not_found()
    old_shas = wf.blob_shas(conn, wireframe_id)
    old_render = conn.execute(sa.select(wireframes.c.render).where(wireframes.c.object_id == wireframe_id)).scalar()
    conn.execute(sa.delete(blob_owners).where(
        blob_owners.c.owner_kind == wf.OWNER, blob_owners.c.owner_id == wireframe_id,
        sa.or_(blob_owners.c.role.in_(list(wf.ARRAYS)), blob_owners.c.role.like("pack:%"))))  # fmt: skip
    for role, tmp in list(staged.files.items()):
        store.commit_staged(conn, tmp, media_type=wf.ARRAY_MEDIA, owner_kind=wf.OWNER, owner_id=wireframe_id,
                            role=role)  # fmt: skip
        del staged.files[role]
    final = wf.free_name(conn, name, exclude_id=wireframe_id)
    meta = {**dict(row.meta or {}), **output_meta(model_id, group, wing, threshold, extra)}
    n = conn.execute(sa.update(objects).where(objects.c.id == wireframe_id, objects.c.version == row.version).values(
        name=final, name_key=dialect.name_key(final), meta=meta, extent=staged.extent, version=int(row.version) + 1,
        updated_at=dialect.utcnow())).rowcount  # fmt: skip
    if n != 1:
        raise wf.not_found()
    conn.execute(sa.update(wireframes).where(wireframes.c.object_id == wireframe_id).values(
        point_count=len(staged.xyz), triangle_count=len(staged.tri), origin=staged.origin, stats=staged.stats,
        render=staged.render))  # fmt: skip
    entry: dict[str, Any] = {"wireframe_id": wireframe_id, "kind": kind, "group": group, "wing": wing, "name": final}
    if threshold is not None:
        entry["threshold"] = threshold
    entry.update({k: v for k, v in (extra or {}).items() if k in EXTRA_KEYS and v})
    gone = {"id": wireframe_id, "shas": old_shas, "render": old_render, "shared": wf.geometry_shared(conn, old_shas)}
    return entry, gone


def outputs_of(conn: sa.Connection, meta: Mapping[str, Any], *, lines: bool = False) -> list[dict[str, Any]]:
    """`meta.outputs` with what the wireframes say now: `{wireframe_id, kind, group, wing, threshold, name, volume,
    area, triangles}` (+ `code`, `common`, `region` of an output of Э19б that has them); an output whose wireframe is
    gone is left out. `lines` — the layers of strings of the model (`store_lines.listed`: `{line_layer_id, kind:
    "line", …}`, no `wireframe_id`) follow the wireframes; the readers that take every output for a wireframe (the
    check, the support, the comparison, the model on the holes) leave it False."""
    given = [o for o in meta.get("outputs") or [] if isinstance(o, Mapping)]
    ids = [int(o["wireframe_id"]) for o in given if isinstance(o.get("wireframe_id"), int)]
    info: dict[int, Any] = {}
    if ids:
        q = sa.select(objects.c.id, objects.c.name, wireframes.c.stats, wireframes.c.triangle_count).join(
            wireframes, wireframes.c.object_id == objects.c.id).where(objects.c.id.in_(ids))
        info = {int(r.id): r for r in conn.execute(q)}
    out = []
    for o in given:
        r = info.get(o.get("wireframe_id"))  # type: ignore[arg-type]
        if r is None:
            continue
        stats = r.stats or {}
        item = {"wireframe_id": int(r.id), "kind": o.get("kind"), "group": o.get("group"), "wing": o.get("wing"),
                "threshold": o.get("threshold"), "name": str(r.name), "volume": stats.get("volume"),
                "area": stats.get("area"), "triangles": int(r.triangle_count)}  # fmt: skip
        item.update({k: o[k] for k in EXTRA_KEYS if o.get(k)})
        out.append(item)
    if lines:
        from . import store_lines

        out.extend(store_lines.listed(conn, given))
    return out


# --- reading ----------------------------------------------------------------------------------------------------------


def _stats_row(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    s = meta.get("stats")
    if not isinstance(s, Mapping):
        return None
    seconds = s.get("seconds")
    return {"points": s.get("points"), "patches": s.get("patches"), "seconds": seconds}


def list_rows(conn: sa.Connection) -> list[dict[str, Any]]:
    """The models by name: `{id, name, version, state, stale, stale_reason, source_lost, groups [{id, name, type}],
    outputs [{wireframe_id, kind, group, wing, threshold, name}], stats {points, patches, seconds} | null,
    created_at, updated_at}`."""
    out = []
    for r in conn.execute(sa.select(objects).where(objects.c.kind == KIND).order_by(objects.c.name_key, objects.c.id)):
        meta = sb.viewed(conn, r.meta, int(r.id))
        out.append({"id": int(r.id), "name": r.name, "version": int(r.version), "state": meta.get("state") or "ready",
                    **freshness(conn, meta),
                    "groups": [{"id": g.get("id"), "name": g.get("name"), "type": g.get("type")}
                               for g in meta.get("groups") or []],
                    "outputs": [{k: o.get(k) for k in ("wireframe_id", "kind", "group", "wing", "threshold", "name")}
                                for o in outputs_of(conn, meta)],
                    "stats": _stats_row(meta), "created_at": r.created_at, "updated_at": r.updated_at})  # fmt: skip
    return out


def get(conn: sa.Connection, model_id: int, *, lines: bool = False) -> dict[str, Any]:
    """`{id, name, version, meta, state, error, stale, stale_reason, source_lost, outputs, created_at, updated_at}`
    (e19-api §5); 404 `model_not_found`. `lines` — the layers of strings among the outputs (`outputs_of`)."""
    row = sb.stored(conn, model_id)
    meta = sb.viewed(conn, row.meta, int(row.id))
    return {"id": int(row.id), "name": row.name, "version": int(row.version), "meta": meta,
            "state": meta.get("state") or "ready", "error": meta.get("error"), **freshness(conn, meta),
            "outputs": outputs_of(conn, meta, lines=lines), "created_at": row.created_at,
            "updated_at": row.updated_at}  # fmt: skip
