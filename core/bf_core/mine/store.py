"""Pit scenarios in the project (подшаг 21.10; ADR 0028 решения 20–23; docs/progress/e21-api.md §5, §6, §8).

A scenario is `objects(kind="pit_scenario", parent_id=NULL)`, ≤ 200 in a project (422 `too_many_scenarios`). Its life:
a new scenario (the default parameters, no result) → the parameters are edited (`update_params`, version + 1) → the
result is written in place (`write_result`, version + 1) in the transaction the caller opened, the last stage of the
job: the files, `meta`, the rows of the graph and (the hook `Built.on_write`) the fields PIT and ORE of the model go
together or not at all, so a cancel or a failure leaves the old result as it was. `objects.state` is always `ready`
(a clean-up of the jobs deletes the `building` rows, Э19 19.29б); «идёт расчёт» is `meta.state == "building"` and its
job `meta.run_job` (`mark_running`, `settle`); a mark whose job is gone is not trusted (`store_base.is_building`).

Files (owner `object`, existing roles, no migration; `store_data`): `data`, `sidecar:benches`, `sidecar:zgrid`,
`sidecar:template`, `sidecar:control`. A scenario without `data` has no result.

`meta` (v 1; решение 20): algo, params (the resolved canonical form of the result, or the current parameters of a
scenario without one), params_hash, model {id, name, cells_sha, grid}, fields {name: fp}, inputs [{kind, id, fp, name}]
(ADR 0025), template, q, objective_error, shells [{k, rf, blocks, ore_t, waste_t, value}], totals, outputs {fields,
wireframes}, control, solver, seconds, versions, warnings, rows, state, error, job_id (the job of the result), run_job
(the job running now), uid, created_at. Parameters edited after a run wait in `meta.draft` {params, hash} («параметры
изменены, нужен запуск»): the route gives `update_params` the **resolved** canonical form when it can, so that the
hash is that of the result for the same effective parameters. The calendar and the solver are not in the hash: they
are written into `meta.params` at once and never make a draft.

    out = create(conn, name, params)                                # → {id, version, name}
    out = update_params(conn, id, version, params=…, calendar=…, name=…)    # PUT → {id, version, name, params_changed}
    staged = stage(store, data, benches, zgrid, template, control)  # outside the transaction; `discard()` on failure
    out = write_result(conn, store, id, Built(params, model, fields, staged, shells, …), expected_version)
    info(conn, id, store=…), list_scenarios(conn), read_data(conn, store, id), read_shells, read_benches,
    read_zgrid, read_template, read_control, rename(…), delete(…), mark_running(…), settle(…),
    rebuild_params(conn, id), current_params(conn, id), result_params(conn, id)

Errors: 404 `pit_scenario_not_found`, `block_model_not_found`; 409 `pit_running`, `pit_no_result`,
`version_conflict {version}`, `name_taken {param}`, `model_changed`, `pit_scenario_broken`; 422
`too_many_scenarios`, `bad_param {param}`.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs, objects
from bf_core.storage.blobs import BlobStore

from . import limits, spec, spec_parts
from . import store_data as sd
from . import store_graph as sg
from .store_view import graph_of, info, list_scenarios  # noqa: F401 - the public view (21.21б-2)

KIND = spec.KIND
NODE = "object:" + KIND  # the kind of the node in the graph (ADR 0025)
OWNER = "object"
ALGO = spec.ALGO
MAX_SCENARIOS = spec_parts.MAX_SCENARIOS
MAX_NAME = 200
DEFAULT_NAME = "Сценарий карьера"
JOB_KIND = "pit.run"
DATA, BENCHES, ZGRID = "data", "sidecar:benches", "sidecar:zgrid"
TEMPLATE, CONTROL = "sidecar:template", "sidecar:control"
ROLES = (DATA, BENCHES, ZGRID, TEMPLATE, CONTROL)
NO_RESULT = "нет результата"  # the fingerprint of a scenario that has no result (решение 23)
_Staged = tuple[Path, str, int]


def not_found() -> ApiError:
    return spec.refuse("pit_scenario_not_found")


def running() -> ApiError:
    return spec.refuse("pit_running")


def no_result() -> ApiError:
    return spec.refuse("pit_no_result")


def conflict(version: int) -> ApiError:
    return spec.refuse("version_conflict", version=version)


def broken() -> ApiError:
    return ApiError(409, "pit_scenario_broken", "Файл сценария карьера не найден в проекте.",
                    "Запустите расчёт сценария заново.")  # fmt: skip


# --- names ------------------------------------------------------------------------------------------------------------


def clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())[:MAX_NAME]
    if not text:
        raise ApiError(422, "bad_param", "Название не может быть пустым.", "Введите название сценария.",
                       extra={"param": "name"})  # fmt: skip
    return text


def _taken(conn: sa.Connection, name: str, but: int | None = None) -> bool:
    q = sa.select(objects.c.id).where(objects.c.kind == KIND, objects.c.name_key == dialect.name_key(name))
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def free_name(conn: sa.Connection, name: Any) -> str:
    """The name, or «имя (2)», «имя (3)» when it is taken (a new scenario is made, not refused)."""
    name = clean_name(name or DEFAULT_NAME)
    candidate, n = name, 1
    while _taken(conn, candidate):
        n += 1
        tail = f" ({n})"
        candidate = name[: MAX_NAME - len(tail)].rstrip() + tail
    return candidate


# --- the files --------------------------------------------------------------------------------------------------------


@dataclass
class Staged:
    """The files of a scenario made outside the transaction: not owned until `write_result` commits them."""

    data: _Staged
    rows: int
    benches: _Staged | None = None
    zgrid: _Staged | None = None
    template: _Staged | None = None
    control: _Staged | None = None
    template_doc: dict[str, Any] | None = None
    control_doc: dict[str, Any] | None = None

    def files(self) -> list[tuple[str, _Staged | None]]:
        return [(DATA, self.data), (BENCHES, self.benches), (ZGRID, self.zgrid), (TEMPLATE, self.template),
                (CONTROL, self.control)]  # fmt: skip

    def discard(self) -> None:
        for _role, f in self.files():
            if f is not None:
                f[0].unlink(missing_ok=True)


def stage(store: BlobStore, data: Any, benches: Any = None, zgrid: Any = None,
          template: Mapping[str, Any] | None = None, control: Mapping[str, Any] | None = None) -> Staged:  # fmt: skip
    """`data`, `benches`, `zgrid` — the tables (`store_data`; the last two may be None), `template`, `control` — the
    JSON documents or None. `zgrid` may also be `(nx, ny, z)`: the array of the z-grids written in row groups
    (`store_data.stage_zgrid`, no copies of it as a table). Nothing is owned yet; the staged files go on a failure
    (`discard`)."""
    out = Staged(sd.stage_parquet(store, data), int(data.num_rows))
    try:
        if benches is not None:
            out.benches = sd.stage_parquet(store, benches)
        if isinstance(zgrid, tuple):
            out.zgrid = sd.stage_zgrid(store, *zgrid)
        elif zgrid is not None:
            out.zgrid = sd.stage_parquet(store, zgrid)
        if template is not None:
            out.template_doc = sd.clean_json(template)
            out.template = sd.stage_json(store, out.template_doc)
        if control is not None:
            out.control_doc = sd.clean_json(control)
            out.control = sd.stage_json(store, out.control_doc)
    except BaseException:
        out.discard()
        raise
    return out


def _put_all(conn: sa.Connection, store: BlobStore, scenario_id: int, st: Staged) -> None:
    """The files in the transaction; a role the new result has not is dropped with the old file."""
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == scenario_id,
                                              blob_owners.c.role.in_(ROLES)))  # fmt: skip
    for role, f in st.files():
        if f is not None:
            media = sd.JSON_MEDIA if role in (TEMPLATE, CONTROL) else sd.PARQUET_MEDIA
            store.commit_staged(conn, f, media_type=media, owner_kind=OWNER, owner_id=scenario_id, role=role)


def _owned(conn: sa.Connection, scenario_id: int) -> dict[str, tuple[str, int]]:
    rows = conn.execute(
        sa.select(blob_owners.c.role, blob_owners.c.sha256, blobs.c.size)
        .join(blobs, blobs.c.sha256 == blob_owners.c.sha256)
        .where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == scenario_id)
    ).all()
    return {str(r.role): (str(r.sha256), int(r.size)) for r in rows}


# --- the write --------------------------------------------------------------------------------------------------------


@dataclass
class Built:
    """What a job brings to the write: the parameters, the model, the fields it read, the files and the summaries."""

    params: dict[str, Any]  # the resolved canonical form (`PitSpec.resolved().canonical()`)
    model: Mapping[str, Any]  # {id, name, grid}; `cells_sha` is the fingerprint of the model in the graph
    fields: Sequence[str]  # the fields read (`store_graph.read_fields`); the fields of the rules are added by the graph
    staged: Staged
    shells: Sequence[Mapping[str, Any]] = ()  # [{k, rf, blocks, ore_t, waste_t, value}]
    totals: Mapping[str, Any] | None = None
    template: Mapping[str, Any] | None = None  # the summary of `meta.template`; None — made from the file
    q: float | None = None
    objective_error: float | None = None
    outputs: Mapping[str, Any] = field(default_factory=lambda: {"fields": [], "wireframes": []})
    solver: str | None = None
    seconds: Mapping[str, Any] | None = None
    versions: Mapping[str, str] | None = None
    warnings: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    job_id: int | None = None
    prints: Mapping[Any, str] | None = None  # `store_graph.input_prints` before the pass; None — not checked
    on_write: Callable[[sa.Connection, int], None] | None = None  # in the transaction: the fields PIT / ORE, the graph


def template_summary(doc: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """`meta.template` from the file `sidecar:template`: k, k_auto, per domain label, n_offsets and the error."""
    if doc is None:
        return None
    domains = [{"label": d.get("label"), "n_offsets": d.get("n_offsets"), "error": d.get("error")}
               for d in doc.get("domains") or []]  # fmt: skip
    return {"k": doc.get("k"), "k_auto": doc.get("k_auto"), "domains": domains,
            "warnings": list(doc.get("warnings") or [])}  # fmt: skip


def hash_of(params: Mapping[str, Any]) -> str:
    return spec.parse_params(dict(params)).params_hash()


def _meta(b: Built, base: Mapping[str, Any], created: str) -> dict[str, Any]:
    out = dict(base)
    out.update({
        "v": 1, "algo": ALGO, "params": b.params, "params_hash": hash_of(b.params), "model": dict(b.model),
        "template": dict(b.template) if b.template is not None else template_summary(b.staged.template_doc),
        "q": b.q, "objective_error": b.objective_error, "shells": [dict(s) for s in b.shells],
        "totals": None if b.totals is None else dict(b.totals), "outputs": dict(b.outputs),
        "control": b.staged.control_doc, "solver": b.solver, "seconds": None if b.seconds is None else dict(b.seconds),
        "versions": dict(b.versions or {}), "warnings": [dict(w) for w in b.warnings], "rows": b.staged.rows,
        "state": "ready", "error": None, "job_id": b.job_id, "created_at": created,
    })  # fmt: skip
    out.pop("draft", None)
    out.pop("run_job", None)
    return sd.clean_json(out)  # type: ignore[no-any-return]


def _record(conn: sa.Connection, scenario_id: int, b: Built) -> dict[str, Any]:
    from bf_core import lineage

    model_id = int(b.model["id"])
    nodes = sg.input_nodes(conn, b.params, b.fields)
    refs = lineage.record(conn, lineage.Node(NODE, scenario_id), nodes)  # in the transaction of the write (ADR 0025 §4)
    return sg.inputs_meta(refs, model_id, {str(f).casefold(): str(f) for f in b.fields})


def write_result(conn: sa.Connection, store: BlobStore, scenario_id: int, built: Built,
                 expected_version: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The result in place, in the transaction the caller opened: the files (the old ones go), `meta`, the hook
    `built.on_write` (the fields PIT and ORE of the model, the rows of their nodes), the rows of the graph; the name,
    the time of the creation stay; the draft of the parameters, the mark «идёт расчёт» and the error go; version + 1.
    A failure anywhere takes everything back. 409 `version_conflict`, `model_changed` (an input changed meanwhile)."""
    row = _row(conn, scenario_id)
    if expected_version is not None and int(row.version) != int(expected_version):
        raise conflict(int(row.version))
    if built.prints is not None and sg.input_prints(conn, built.params, built.fields) != built.prints:
        raise sg.inputs_changed()  # before any file is put: nothing is left without an owner (21.21б-2)
    old = dict(row.meta or {})
    meta = _meta(built, {k: old[k] for k in ("uid",) if k in old}, str(old.get("created_at") or ""))
    _put_all(conn, store, scenario_id, built.staged)
    if built.on_write is not None:
        built.on_write(conn, scenario_id)
    rec = _record(conn, scenario_id, built)
    meta["inputs"], meta["fields"] = rec["inputs"], rec["fields"]
    if rec["cells_sha"] is not None:
        meta["model"] = {**meta["model"], "cells_sha": rec["cells_sha"]}
    version = int(row.version) + 1
    conn.execute(sa.update(objects).where(objects.c.id == scenario_id).values(
        meta=meta, version=version, updated_at=dialect.utcnow()))  # fmt: skip
    return {"id": scenario_id, "version": version, "name": row.name}


# --- create, edit -----------------------------------------------------------------------------------------------------


def check_room(conn: sa.Connection) -> None:
    """422 `too_many_scenarios` when the project holds `MAX_SCENARIOS` already."""
    count = conn.execute(sa.select(sa.func.count()).select_from(objects).where(objects.c.kind == KIND)).scalar_one()
    if int(count) >= MAX_SCENARIOS:
        raise spec.refuse("too_many_scenarios", f"В проекте уже {MAX_SCENARIOS} сценариев карьера.")


def _model(conn: sa.Connection, model_id: int | None) -> dict[str, Any]:
    """{id, name} of the block model; 404 `block_model_not_found`. No model yet: {id: None, name: None}."""
    if model_id is None:
        return {"id": None, "name": None}
    name = conn.execute(sa.select(objects.c.name).where(objects.c.kind == "block_model",
                                                        objects.c.id == model_id)).scalar()  # fmt: skip
    if name is None:
        raise spec.refuse("block_model_not_found")
    return {"id": int(model_id), "name": str(name)}


def _spec_of(params: Any) -> spec.PitSpec:
    return params if isinstance(params, spec.PitSpec) else spec.parse_params({} if params is None else dict(params))


def create(conn: sa.Connection, name: Any = None, params: Any = None, author: str | None = None) -> dict[str, Any]:
    """A new scenario (no result): the parameters checked for their form (422 `bad_param`), the model for being there
    (404 `block_model_not_found`). The name is made free («имя (2)»). → {id, version: 1, name}.
    422 `too_many_scenarios`."""
    sp = _spec_of(params)
    check_room(conn)
    model = _model(conn, sp.model_id)
    final = free_name(conn, name)
    now = dialect.utcnow()
    meta = {"v": 1, "algo": ALGO, "params": sp.canonical(), "params_hash": sp.params_hash(), "model": model,
            "state": "ready", "error": None, "uid": uuid.uuid4().hex, "created_at": now.isoformat()}  # fmt: skip
    res = conn.execute(sa.insert(objects).values(
        kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="ready",
        version=1, meta=meta, author=author, created_at=now, updated_at=now))  # fmt: skip
    return {"id": int(res.inserted_primary_key[0]), "version": 1, "name": final}  # type: ignore[index]


def update_params(conn: sa.Connection, scenario_id: int, expected_version: Any, params: Any = None,
                  calendar: Any = None, name: Any = None) -> dict[str, Any]:  # fmt: skip
    """`PUT /pit/scenarios/{id}`: the name, the parameters (a form, as `create`), the calendar. A scenario without a
    result takes them as its parameters. With a result, parameters that change the hash wait in `meta.draft`
    (`params_changed`); the same hash, the calendar and the solver are written to the parameters of the result at once.
    version + 1. 404; 409 `pit_running`, `version_conflict`, `name_taken`; 422 `bad_param`."""
    row = _row(conn, scenario_id)
    _writable(conn, row)
    if row.version != expected_version:
        raise conflict(int(row.version))
    final = row.name
    if name is not None:
        final = clean_name(name)
        if _taken(conn, final, but=scenario_id):
            raise spec.refuse("name_taken", f"Сценарий «{final}» уже есть.", param="name")
    meta = dict(row.meta or {})
    has_result = DATA in _owned(conn, scenario_id)
    cal = None if calendar is None else spec.parse_params({"calendar": calendar}).calendar.canonical()
    if params is not None:
        new = _spec_of(params).canonical()
        if cal is not None:
            new["calendar"] = cal
        _model(conn, new["model_id"])  # 404 for a model that is not there
        if not has_result:
            meta.update(params=new, params_hash=hash_of(new), model=_model(conn, new["model_id"]))
        elif hash_of(new) == meta.get("params_hash"):  # only what is not in the hash differs: no run is needed
            meta["params"] = {**meta["params"], "calendar": new["calendar"], "solver": new["solver"]}
            meta.pop("draft", None)
        else:
            meta["draft"] = {"params": new, "hash": hash_of(new)}
    elif cal is not None:
        meta["params"] = {**meta["params"], "calendar": cal}
        if isinstance(meta.get("draft"), Mapping):
            meta["draft"] = {**meta["draft"], "params": {**meta["draft"]["params"], "calendar": cal}}
    version = int(row.version) + 1
    conn.execute(sa.update(objects).where(objects.c.id == scenario_id).values(
        name=final, name_key=dialect.name_key(final), meta=meta, version=version,
        updated_at=dialect.utcnow()))  # fmt: skip
    return {"id": scenario_id, "version": version, "name": final, "params_changed": _changed(meta, has_result)}


def rename(conn: sa.Connection, scenario_id: int, name: Any, expected_version: Any) -> dict[str, Any]:
    row = _row(conn, scenario_id)
    _writable(conn, row)
    if row.version != expected_version:
        raise conflict(int(row.version))
    clean = clean_name(name)
    if _taken(conn, clean, but=scenario_id):
        raise spec.refuse("name_taken", f"Сценарий «{clean}» уже есть.", param="name")
    version = int(row.version) + 1
    conn.execute(sa.update(objects).where(objects.c.id == scenario_id).values(
        name=clean, name_key=dialect.name_key(clean), version=version, updated_at=dialect.utcnow()))  # fmt: skip
    return {"id": scenario_id, "version": version, "name": clean}


def mark_running(conn: sa.Connection, scenario_id: int, job_id: int | None = None) -> None:
    """A run starts: `meta.state = "building"` and its job (`meta.run_job`); `objects.state` stays `ready`.
    409 `pit_running`. The row is locked: of two parallel starts on PostgreSQL the second waits and sees the mark."""
    row = _row(conn, scenario_id, lock=True)
    if _is_running(conn, row):
        raise running()
    meta = {**(row.meta or {}), "state": "building", "error": None}
    meta.pop("run_job", None)
    if job_id is not None:
        meta["run_job"] = int(job_id)
    conn.execute(sa.update(objects).where(objects.c.id == scenario_id).values(meta=meta))


def settle(conn: sa.Connection, scenario_id: int, error: Mapping[str, Any] | None = None) -> None:
    """A run ended without a result (cancel, failure): the scenario is as it was; a failure leaves its `error`
    {code, message, hint} (the state `error`) until the next run."""
    row = _row(conn, scenario_id)
    meta = {**(row.meta or {}), "state": "error" if error else "ready", "error": None if not error else dict(error)}
    meta.pop("run_job", None)
    conn.execute(sa.update(objects).where(objects.c.id == scenario_id).values(meta=meta))


def delete(conn: sa.Connection, scenario_id: int) -> dict[str, Any]:
    """The object, its files and the rows of its node of the graph. The fields PIT / ORE and the wireframes it made stay
    (ADR 0028 / docs/progress/e21-api.md §15.10): what is built from the scenario says «источник удалён» — and belongs
    to no scenario any more (`store_graph.orphan_outputs`: a scenario made later under the same number does not own
    them). → {id, name, outputs} (what stays, for the events of the route). 404; 409 `pit_running`."""
    row = _row(conn, scenario_id)
    _writable(conn, row)
    meta = dict(row.meta or {})
    sg.orphan_outputs(conn, scenario_id, (meta.get("model") or {}).get("id"))  # before the rows go: the graph is read
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == scenario_id))
    conn.execute(sa.delete(objects).where(objects.c.id == scenario_id))
    from bf_core import lineage

    lineage.forget_object(conn, KIND, scenario_id)
    return {"id": scenario_id, "name": row.name, "model_id": (meta.get("model") or {}).get("id"),
            "outputs": dict(meta.get("outputs") or {"fields": [], "wireframes": []})}  # fmt: skip


# --- read -------------------------------------------------------------------------------------------------------------


def _row(conn: sa.Connection, scenario_id: int, lock: bool = False) -> Any:
    q = sa.select(objects).where(objects.c.id == scenario_id, objects.c.kind == KIND)
    r = conn.execute(q.with_for_update() if lock else q).first()  # `lock`: FOR UPDATE on PostgreSQL (no-op on SQLite)
    if r is None:
        raise not_found()
    return r


def _is_running(conn: sa.Connection, row: Any) -> bool:
    from bf_core.model.store_base import is_building

    meta = row.meta or {}
    return is_building(conn, {"state": meta.get("state"), "job_id": meta.get("run_job")})


def _writable(conn: sa.Connection, row: Any) -> None:
    if _is_running(conn, row):
        raise running()


def _changed(meta: Mapping[str, Any], has_result: bool) -> bool:
    draft = meta.get("draft")
    return bool(has_result and isinstance(draft, Mapping) and draft.get("hash") != meta.get("params_hash"))


def current_params(conn: sa.Connection, scenario_id: int) -> dict[str, Any]:
    """The parameters of the next run (`POST …/run`): the draft when the scenario has one, else its parameters."""
    meta = _row(conn, scenario_id).meta or {}
    draft = meta.get("draft")
    return dict(draft["params"] if isinstance(draft, Mapping) else meta.get("params") or {})


def result_params(conn: sa.Connection, scenario_id: int) -> dict[str, Any]:
    """The parameters of the result (resolved) — what a rebuild runs again."""
    return dict((_row(conn, scenario_id).meta or {}).get("params") or {})


def data_sha(conn: sa.Connection, scenario_id: int) -> str | None:
    """The SHA-256 of the file `data`; None — a scenario without a result. 404."""
    found = _owned(conn, scenario_id).get(DATA)
    if found is None:
        _row(conn, scenario_id)
        return None
    return found[0]


def _path(conn: sa.Connection, store: BlobStore, scenario_id: int, role: str) -> Path | None:
    found = _owned(conn, scenario_id).get(role)
    if found is None:
        _row(conn, scenario_id)  # 404 for a scenario that is not there; a file the scenario does not have: None
        return None
    path = store.path_of(found[0])
    if not path.is_file():
        raise broken()
    return path


def read_data(conn: sa.Connection, store: BlobStore, scenario_id: int) -> Any:
    """The whole table `data` (pyarrow). 404; 409 `pit_no_result`, `pit_scenario_broken` (the file is gone)."""
    import pyarrow.parquet as pq

    path = _path(conn, store, scenario_id, DATA)
    if path is None:
        raise no_result()
    return pq.read_table(path)


def read_shells(conn: sa.Connection, store: BlobStore, scenario_id: int) -> list[dict[str, Any]]:
    """The `Shell` dicts of the contract (§7) from `data` (without `best` / `worst`)."""
    return sd.table_shells(read_data(conn, store, scenario_id))


def _read_parquet(conn: sa.Connection, store: BlobStore, scenario_id: int, role: str) -> Any:
    import pyarrow.parquet as pq

    path = _path(conn, store, scenario_id, role)
    return None if path is None else pq.read_table(path)


def read_benches(conn: sa.Connection, store: BlobStore, scenario_id: int) -> Any:
    """The table of the worst case or None."""
    return _read_parquet(conn, store, scenario_id, BENCHES)


def read_zgrid(conn: sa.Connection, store: BlobStore, scenario_id: int, shells: Sequence[int] | None = None) -> Any:
    """The table of the z-grids of the shells or None. `shells` — the numbers wanted: only the columns `i`, `j` and
    those `z<k>` are read (the others stay on the disk); a shell the file has not — 409 `pit_scenario_broken`."""
    if shells is None:
        return _read_parquet(conn, store, scenario_id, ZGRID)
    import pyarrow.parquet as pq

    path = _path(conn, store, scenario_id, ZGRID)
    if path is None:
        return None
    want = [f"z{int(k)}" for k in sorted({int(k) for k in shells})]
    if not set(want) <= set(pq.read_schema(path).names):
        raise broken()
    return pq.read_table(path, columns=["i", "j", *want])


def _read_json(conn: sa.Connection, store: BlobStore, scenario_id: int, role: str) -> dict[str, Any] | None:
    import json

    path = _path(conn, store, scenario_id, role)
    return None if path is None else dict(json.loads(path.read_bytes()))


def read_template(conn: sa.Connection, store: BlobStore, scenario_id: int) -> dict[str, Any] | None:
    return _read_json(conn, store, scenario_id, TEMPLATE)


def read_control(conn: sa.Connection, store: BlobStore, scenario_id: int) -> dict[str, Any] | None:
    return _read_json(conn, store, scenario_id, CONTROL)


def rebuild_params(conn: sa.Connection, scenario_id: int) -> dict[str, Any]:
    """What the job `pit.run` is given to rebuild the scenario in place («Перестроить», `lineage.rebuild`): the
    parameters of the result (the same effective parameters, the same bytes), the scenario and its version. 404;
    409 `pit_running`, `pit_no_result`."""
    row = _row(conn, scenario_id)
    _writable(conn, row)
    if DATA not in _owned(conn, scenario_id):
        raise no_result()
    params = result_params(conn, scenario_id)
    counts = ((row.meta or {}).get("model") or {}).get("grid", {}).get("counts")
    size = limits.hints(counts, len(params.get("rf") or ())) if counts else {}  # for the memory of the job
    return {"scenario_id": int(row.id), "params": params, "name": row.name, "expected_version": int(row.version),
            "rebuild": True, **size}  # fmt: skip


def fingerprint(data_sha_: str | None, algo: str = ALGO) -> str:
    """The state of a scenario in the graph: the SHA-256 of its `data` and ALGO (решение 23); without a result —
    «нет результата»."""
    if not data_sha_:
        return NO_RESULT
    return "p:" + hashlib.sha256(f"{data_sha_}|{algo}".encode()).hexdigest()[:24]
