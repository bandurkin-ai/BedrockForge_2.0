"""Resource reports in the project (подшаг 20.6; ADR 0027 решения 14–16, 21; docs/progress/e20-api.md §7).

A report is `objects(kind="resource_report", parent_id=NULL)`, ≤ 1000 in a project (the pattern of `qaqc/report.py`).
`objects.state` is always `ready` (a clean-up of the jobs deletes the `building` rows of `objects`, Э19 19.29б): a
rebuild marks the report «идёт построение» in `meta.state` / `meta.job_id` only (`mark_building`, `settle`) and a mark
whose job is gone is not trusted (`model.store_base.is_building`). A new report is written in one transaction at the
end of the job (`create`), a rebuild replaces it in place (`replace`, version + 1): a job that is cancelled or fails
leaves nothing — the rows, the files and the rows of the graph are in the transaction of the write.

Files (owner `object`, existing roles, no migration): `data` (Parquet, `store_data`), `sidecar:curve` (Parquet),
`sidecar:control` (JSON), `sidecar:xlsx` (the last book; dropped by a rebuild: its numbers are old).

`meta` (v 1): algo, params (the resolved canonical form), params_hash, model {id, name, cells_sha, grid}, fields
{name: fp}, inputs [{kind, id, fp, name}] (ADR 0025; a field of a model — id «<model>:<name>»), totals, rows, curve,
state, job_id, error, files [{format, path, sha256, at}] (the exports), versions, created_at.

    staged = stage(store, table, curve_table, control)           # outside the transaction; `discard()` on failure
    out = create(conn, store, name, Built(params, model, fields, totals, staged))      # → {id, version}
    out = replace(conn, store, report_id, Built(…), expected_version)                   # «Перестроить»
    info(conn, report_id, store=…), list_reports(conn), read_rows(conn, store, report_id, page, size)
    read_table(conn, store, report_id), read_curve(…), read_control(…), rename(…), delete(…)
    put_xlsx(conn, store, report_id, staged), record_file(conn, report_id, entry), rebuild_params(conn, report_id)

Errors: 404 `resource_report_not_found`; 409 `report_building`, `version_conflict {version}`, `name_taken`,
`resource_report_broken`; 422 `too_many_reports`, `bad_param {param}`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs, objects
from bf_core.storage.blobs import BlobStore

from . import spec
from . import store_data as sd

KIND = "resource_report"
NODE = "object:" + KIND  # the kind of the node in the graph (ADR 0025)
OWNER = "object"
ALGO = spec.ALGO
MAX_REPORTS = 1000
MAX_NAME = 200
MAX_FILES = 100  # exports remembered in `meta.files`
DEFAULT_NAME = "Отчёт по ресурсам"
DATA, CURVE, CONTROL, XLSX = "data", "sidecar:curve", "sidecar:control", "sidecar:xlsx"
JSON_MEDIA = "application/json"
XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_Staged = tuple[Path, str, int]


def not_found() -> ApiError:
    return spec.refuse("resource_report_not_found")


def building() -> ApiError:
    return spec.refuse("report_building")


def conflict(version: int) -> ApiError:
    return spec.refuse("version_conflict", version=version)


def broken() -> ApiError:
    return ApiError(409, "resource_report_broken", "Файл отчёта по ресурсам не найден в проекте.",
                    "Перестройте отчёт.")  # fmt: skip


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


# --- names ----------------------------------------------------------------------------------------------------------


def clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())[:MAX_NAME]
    if not text:
        raise ApiError(422, "bad_param", "Название не может быть пустым.", "Введите название отчёта.",
                       extra={"param": "name"})  # fmt: skip
    return text


def _taken(conn: sa.Connection, name: str, but: int | None = None) -> bool:
    q = sa.select(objects.c.id).where(objects.c.kind == KIND, objects.c.name_key == dialect.name_key(name))
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def free_name(conn: sa.Connection, name: Any) -> str:
    """The name, or «имя (2)», «имя (3)» when it is taken (a report is made, not refused)."""
    name = clean_name(name or DEFAULT_NAME)
    candidate, n = name, 1
    while _taken(conn, candidate):
        n += 1
        tail = f" ({n})"
        candidate = name[: MAX_NAME - len(tail)].rstrip() + tail
    return candidate


# --- the files ------------------------------------------------------------------------------------------------------


@dataclass
class Staged:
    """The files of a report made outside the transaction: not owned until `create` / `replace` commit them."""

    data: _Staged
    rows: int
    curve: _Staged | None = None
    curve_points: int = 0
    control: _Staged | None = None

    def discard(self) -> None:
        for f in (self.data, self.curve, self.control):
            if f is not None:
                f[0].unlink(missing_ok=True)


def stage(store: BlobStore, data: Any, curve: Any = None, control: Mapping[str, Any] | None = None) -> Staged:
    """`data` — the table of the rows (`store_data.build_table`), `curve` — the table of the curve or None, `control` —
    the JSON of the control or None. Nothing is owned yet; the staged files go on a failure (`discard`)."""
    out = Staged(sd.stage_parquet(store, data), int(data.num_rows))
    try:
        if curve is not None:
            out.curve, out.curve_points = sd.stage_parquet(store, curve), int(curve.num_rows)
        if control is not None:
            out.control = store.stage_bytes(_canonical(control))
    except BaseException:
        out.discard()
        raise
    return out


def _put(conn: sa.Connection, store: BlobStore, report_id: int, role: str, staged: _Staged, media: str) -> str:
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id,
                                              blob_owners.c.role == role))  # fmt: skip
    return store.commit_staged(conn, staged, media_type=media, owner_kind=OWNER, owner_id=report_id, role=role)


def _put_all(conn: sa.Connection, store: BlobStore, report_id: int, st: Staged) -> None:
    """The three files in the transaction; a role the new result has not (no curve) is dropped with the old file."""
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id,
                                              blob_owners.c.role.in_((CURVE, CONTROL, XLSX))))  # fmt: skip
    _put(conn, store, report_id, DATA, st.data, sd.PARQUET_MEDIA)
    if st.curve is not None:
        _put(conn, store, report_id, CURVE, st.curve, sd.PARQUET_MEDIA)
    if st.control is not None:
        _put(conn, store, report_id, CONTROL, st.control, JSON_MEDIA)


def _owned(conn: sa.Connection, report_id: int) -> dict[str, tuple[str, int]]:
    rows = conn.execute(
        sa.select(blob_owners.c.role, blob_owners.c.sha256, blobs.c.size)
        .join(blobs, blobs.c.sha256 == blob_owners.c.sha256)
        .where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id)
    ).all()
    return {str(r.role): (str(r.sha256), int(r.size)) for r in rows}


# --- the graph (ADR 0025, решение 21) ---------------------------------------------------------------------------------


def input_nodes(conn: sa.Connection, model_id: int, fields: Sequence[str], filter_id: int | None = None,
                restrict: Mapping[str, Any] | None = None) -> list[Any]:  # fmt: skip
    """The nodes the report is built from: the model (its grid), every field it reads (`fields` — elements, the
    cut-off field, the density, the keys, `FRAC`, `STATUS`, `DENS_SRC`; and the fields of the rules), the filter of the
    library, and the wireframes and surfaces of the rules (also those inside the filter: a filter has no inputs of its
    own in the graph). What is not there any more is left out. Order: model, fields, filter, wireframes."""
    from bf_core import lineage
    from bf_core.blocks.filters import schema as fs
    from bf_core.blocks.filters import store as filters

    rules: list[Mapping[str, Any]] = [restrict] if restrict else []
    if filter_id is not None:
        try:
            rules.append(filters.rules_of(conn, int(filter_id)))
        except ApiError:
            pass  # a filter that is gone: the node is left out and the graph says «источник удалён»
    names: dict[str, str] = {str(f).casefold(): str(f) for f in fields}
    wires: dict[int, None] = {}
    for r in rules:
        names.update({n.casefold(): n for n in fs.fields_of(r)})
        wires.update(dict.fromkeys(fs.object_ids(r)))
    nodes = [lineage.Node("object:block_model", int(model_id))]
    nodes += [lineage.Node("bm_field", int(model_id), key) for key in names]
    if filter_id is not None:
        nodes.append(lineage.Node("object:filter", int(filter_id)))
    nodes += [lineage.Node("wireframe", i) for i in wires]
    return [n for n in nodes if lineage.fingerprint(conn, n) is not None]


def input_prints(conn: sa.Connection, model_id: int, fields: Sequence[str], filter_id: int | None = None,
                 restrict: Mapping[str, Any] | None = None) -> dict[Any, str]:  # fmt: skip
    """The fingerprint of every input node (`input_nodes`) as it is now: what a job takes before it reads the data and
    `create` / `replace` compare in the transaction of the write (the model, a field, a filter or a surface changed
    meanwhile — 409 `model_changed`: the rows of the graph would name what the pass did not read)."""
    from bf_core import lineage

    return {n: str(lineage.fingerprint(conn, n)) for n in input_nodes(conn, model_id, fields, filter_id, restrict)}


def inputs_changed() -> ApiError:
    return ApiError(409, "model_changed", "Данные, из которых строится отчёт, изменились, пока он считался.",
                    "Повторите расчёт отчёта.")  # fmt: skip


def _inputs_meta(refs: Sequence[Any], model_id: int, names: Mapping[str, str]) -> dict[str, Any]:
    """`meta.inputs`, `meta.fields` and the fingerprint of the model from what the graph recorded."""
    inputs: list[dict[str, Any]] = []
    fields: dict[str, str] = {}
    cells: str | None = None
    for r in refs:
        n = r.node
        if n.kind == "bm_field":
            name = names.get(n.sub, n.sub)
            inputs.append({"kind": n.kind, "id": f"{n.id}:{name}", "fp": r.fp, "name": r.name})
            fields[name] = r.fp
        else:
            inputs.append({"kind": n.kind, "id": n.id, "fp": r.fp, "name": r.name})
            if n.kind == "object:block_model" and n.id == model_id:
                cells = r.fp
    return {"inputs": inputs, "fields": fields, "cells_sha": cells}


@dataclass
class Built:
    """What a job brings to the write: the parameters, the model, the fields it read, the totals and the files."""

    params: dict[str, Any]  # the resolved canonical form (`ReportSpec.canonical()`)
    model: Mapping[str, Any]  # {id, name, grid}; `cells_sha` is the fingerprint of the model in the graph
    fields: Sequence[str]
    totals: Mapping[str, Any] | None
    staged: Staged
    versions: Mapping[str, str] | None = None
    warnings: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    prints: Mapping[Any, str] | None = None  # `input_prints` before the pass; None — not checked


def _params_hash(params: Mapping[str, Any]) -> str:
    return spec.parse_params(dict(params)).params_hash()


def _meta(b: Built, base: Mapping[str, Any] | None, created: str) -> dict[str, Any]:
    out = dict(base or {})
    out.update({"v": 1, "algo": ALGO, "params": b.params, "params_hash": _params_hash(b.params),
                "model": dict(b.model), "totals": None if b.totals is None else dict(b.totals),
                "rows": b.staged.rows, "curve": b.staged.curve_points, "versions": dict(b.versions or {}),
                "warnings": [dict(w) for w in b.warnings], "state": "ready", "error": None, "created_at": created})
    out.pop("job_id", None)
    out.setdefault("files", [])
    return out


def check_inputs(conn: sa.Connection, b: Built) -> None:
    """409 `model_changed` when an input changed since the pass took its fingerprints. Called before any file is put
    (as in `mine.store.write_result`, 21.21б-2): a refused write leaves no file without an owner."""
    if b.prints is None:
        return
    p = b.params
    if input_prints(conn, int(b.model["id"]), b.fields, p.get("filter_id"), p.get("restrict")) != b.prints:
        raise inputs_changed()


def _record(conn: sa.Connection, report_id: int, b: Built) -> dict[str, Any]:
    from bf_core import lineage

    p = b.params
    model_id = int(b.model["id"])
    flt, rules = p.get("filter_id"), p.get("restrict")
    nodes = input_nodes(conn, model_id, b.fields, flt, rules)
    refs = lineage.record(conn, lineage.Node(NODE, report_id), nodes)  # in the transaction of the write (ADR 0025 §4)
    return _inputs_meta(refs, model_id, {str(f).casefold(): str(f) for f in b.fields})


def _apply(conn: sa.Connection, report_id: int, meta: dict[str, Any], rec: Mapping[str, Any],
           **values: Any) -> None:  # fmt: skip
    meta["inputs"], meta["fields"] = rec["inputs"], rec["fields"]
    if rec["cells_sha"] is not None:
        meta["model"] = {**meta["model"], "cells_sha": rec["cells_sha"]}
    conn.execute(sa.update(objects).where(objects.c.id == report_id).values(meta=meta, **values))


# --- write ----------------------------------------------------------------------------------------------------------


def check_room(conn: sa.Connection) -> None:
    """422 `too_many_reports` when the project holds `MAX_REPORTS` already (the route asks before the pass, `create`
    again in the transaction of the write)."""
    count = conn.execute(sa.select(sa.func.count()).select_from(objects).where(objects.c.kind == KIND)).scalar_one()
    if int(count) >= MAX_REPORTS:
        raise ApiError(422, "too_many_reports", f"В проекте уже {MAX_REPORTS} отчётов по ресурсам.",
                       "Удалите ненужные отчёты.")  # fmt: skip


def create(conn: sa.Connection, store: BlobStore, name: Any, built: Built, author: str | None = None) -> dict[str, Any]:
    """The report object with its files and the rows of the graph, in the transaction the caller opened (the last stage
    of the job). → {id, version, name}. 422 `too_many_reports`."""
    check_room(conn)
    check_inputs(conn, built)
    final = free_name(conn, name)
    now = dialect.utcnow()
    meta = {**_meta(built, None, now.isoformat()), "uid": uuid.uuid4().hex}
    res = conn.execute(sa.insert(objects).values(
        kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="ready",
        version=1, meta=meta, author=author, created_at=now, updated_at=now))  # fmt: skip
    report_id = int(res.inserted_primary_key[0])  # type: ignore[index]
    _put_all(conn, store, report_id, built.staged)
    _apply(conn, report_id, meta, _record(conn, report_id, built))
    return {"id": report_id, "version": 1, "name": final}


def replace(conn: sa.Connection, store: BlobStore, report_id: int, built: Built,
            expected_version: int | None = None) -> dict[str, Any]:  # fmt: skip
    """«Перестроить»: the files, the parameters and the rows of the graph of the report replaced in place; the name,
    the time of the creation and the list of the exports stay; version + 1. 409 `version_conflict`."""
    row = _row(conn, report_id)
    if expected_version is not None and int(row.version) != int(expected_version):
        raise conflict(int(row.version))
    check_inputs(conn, built)
    old = dict(row.meta or {})
    meta = _meta(built, {k: old[k] for k in ("uid", "files") if k in old}, str(old.get("created_at") or ""))
    _put_all(conn, store, report_id, built.staged)
    version = int(row.version) + 1
    _apply(conn, report_id, meta, _record(conn, report_id, built), version=version, updated_at=dialect.utcnow())
    return {"id": report_id, "version": version, "name": row.name}


def mark_building(conn: sa.Connection, report_id: int, job_id: int | None = None) -> None:
    """A rebuild starts: `meta.state = "building"` and its job. `objects.state` stays `ready`. 409 `report_building`."""
    from bf_core.model.store_base import is_building

    row = _row(conn, report_id)
    if is_building(conn, row.meta):
        raise building()
    meta = {**(row.meta or {}), "state": "building"}
    meta.pop("job_id", None)
    if job_id is not None:
        meta["job_id"] = int(job_id)
    conn.execute(sa.update(objects).where(objects.c.id == report_id).values(meta=meta))


def settle(conn: sa.Connection, report_id: int) -> None:
    """A rebuild ended without a result (cancel, failure): the report is as it was."""
    row = _row(conn, report_id)
    meta = {**(row.meta or {}), "state": "ready"}
    meta.pop("job_id", None)
    conn.execute(sa.update(objects).where(objects.c.id == report_id).values(meta=meta))


def rename(conn: sa.Connection, report_id: int, name: Any, expected_version: Any) -> dict[str, Any]:
    row = _row(conn, report_id)
    _writable(conn, row)
    if row.version != expected_version:
        raise conflict(int(row.version))
    clean = clean_name(name)
    if _taken(conn, clean, but=report_id):
        raise ApiError(409, "name_taken", f"Отчёт «{clean}» уже есть.", "Выберите другое название.",
                       extra={"param": "name"})  # fmt: skip
    version = int(row.version) + 1
    conn.execute(sa.update(objects).where(objects.c.id == report_id).values(
        name=clean, name_key=dialect.name_key(clean), version=version, updated_at=dialect.utcnow()))  # fmt: skip
    return {"id": report_id, "version": version, "name": clean}


def delete(conn: sa.Connection, report_id: int) -> None:
    """The object, its files and the rows of its node of the graph (those built from it say «источник удалён»)."""
    _writable(conn, _row(conn, report_id))
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id))
    conn.execute(sa.delete(objects).where(objects.c.id == report_id))
    from bf_core import lineage

    lineage.forget_object(conn, KIND, report_id)


def put_xlsx(conn: sa.Connection, store: BlobStore, report_id: int, staged: _Staged) -> str:
    """The last book of the report (`sidecar:xlsx`), replacing the previous one; the version stays (the numbers do)."""
    _row(conn, report_id)
    return _put(conn, store, report_id, XLSX, staged, XLSX_MEDIA)


def record_file(conn: sa.Connection, report_id: int, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """An export {format, path, sha256, at} into `meta.files` (the last `MAX_FILES`; the same path is replaced)."""
    row = _row(conn, report_id)
    meta = dict(row.meta or {})
    kept = [dict(f) for f in meta.get("files") or [] if f.get("path") != entry.get("path")]
    meta["files"] = [*kept, dict(entry)][-MAX_FILES:]
    conn.execute(sa.update(objects).where(objects.c.id == report_id).values(meta=meta))
    return list(meta["files"])


# --- read -----------------------------------------------------------------------------------------------------------


def _row(conn: sa.Connection, report_id: int) -> Any:
    r = conn.execute(sa.select(objects).where(objects.c.id == report_id, objects.c.kind == KIND)).first()
    if r is None:
        raise not_found()
    return r


def _is_building(conn: sa.Connection, row: Any) -> bool:
    from bf_core.model.store_base import is_building

    return is_building(conn, row.meta)


def _writable(conn: sa.Connection, row: Any) -> None:
    if _is_building(conn, row):
        raise building()


def graph_of(conn: sa.Connection, ids: Sequence[int], store: BlobStore | None = None) -> dict[int, dict[str, Any]]:
    """The state of the reports in the graph: {id: {stale, stale_reason, source_lost, pinned}} (one batch)."""
    from bf_core import lineage

    nodes = [lineage.Node(NODE, int(i)) for i in ids]
    got = lineage.status(conn, nodes, store=store) if nodes else {}
    out: dict[int, dict[str, Any]] = {}
    for n in nodes:
        s = got[n]
        lost = s.state == lineage.LOST
        out[n.id] = {"stale": s.state in (lineage.STALE, lineage.LOST) or (s.state == lineage.PINNED and s.changed),
                     "stale_reason": s.reason if s.state != lineage.CURRENT else None, "source_lost": lost,
                     "pinned": s.state == lineage.PINNED}  # fmt: skip
    return out


def _public(row: Any, owned: Mapping[str, tuple[str, int]], building_now: bool,
            graph: Mapping[str, Any]) -> dict[str, Any]:  # fmt: skip
    meta = dict(row.meta or {})
    model = meta.get("model") or {}
    return {
        "id": int(row.id), "name": row.name, "version": int(row.version),
        "state": "building" if building_now else ("error" if meta.get("state") == "error" else "ready"),
        "model": {"id": model.get("id"), "name": model.get("name")}, "totals": meta.get("totals"),
        "graph": dict(graph), "files": list(meta.get("files") or []), "xlsx": XLSX in owned,
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
        "job_id": meta.get("job_id") if building_now else None, "rows": meta.get("rows"),
        "size": sum(s for _sha, s in owned.values()),
    }  # fmt: skip


def info(conn: sa.Connection, report_id: int, store: BlobStore | None = None) -> dict[str, Any]:
    """One report: the list entry plus the parameters, the inputs, the fields with their fingerprints, the warnings
    of the build and the error. The graph is checked with `store` (the makers of the fields) when it is given."""
    row = _row(conn, report_id)
    out = _public(row, _owned(conn, report_id), _is_building(conn, row), graph_of(conn, [report_id], store)[report_id])
    meta = row.meta or {}
    for k in ("algo", "params", "params_hash", "fields", "inputs", "warnings", "error", "versions", "curve", "uid"):
        out[k] = meta.get(k)
    out["model"] = dict(meta.get("model") or {})
    return out


def list_reports(conn: sa.Connection) -> list[dict[str, Any]]:
    """Every report by name: the list entries with the state in the graph (one batch)."""
    rows = conn.execute(
        sa.select(objects).where(objects.c.kind == KIND).order_by(objects.c.name_key, objects.c.id)
    ).all()
    graph = graph_of(conn, [int(r.id) for r in rows])
    owned: dict[int, dict[str, tuple[str, int]]] = {int(r.id): {} for r in rows}
    q = (sa.select(blob_owners.c.owner_id, blob_owners.c.role, blob_owners.c.sha256, blobs.c.size)
         .join(blobs, blobs.c.sha256 == blob_owners.c.sha256)
         .where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id.in_(list(owned))))  # fmt: skip
    for r in conn.execute(q) if owned else ():
        owned[int(r.owner_id)][str(r.role)] = (str(r.sha256), int(r.size))
    return [_public(r, owned[int(r.id)], _is_building(conn, r), graph[int(r.id)]) for r in rows]


def data_sha(conn: sa.Connection, report_id: int) -> str:
    found = _owned(conn, report_id).get(DATA)
    if found is None:
        _row(conn, report_id)
        raise broken()
    return found[0]


def _path(conn: sa.Connection, store: BlobStore, report_id: int, role: str) -> Path | None:
    found = _owned(conn, report_id).get(role)
    if found is None:
        _row(conn, report_id)  # 404 for a report that is not there; a file the report does not have: None
        return None
    path = store.path_of(found[0])
    if not path.is_file():
        raise broken()
    return path


def read_table(conn: sa.Connection, store: BlobStore, report_id: int) -> Any:
    """The whole table `data` (pyarrow). A report always has it; 409 `resource_report_broken` if the file is gone."""
    import pyarrow.parquet as pq

    path = _path(conn, store, report_id, DATA)
    if path is None:
        raise broken()
    return pq.read_table(path)


def read_rows(conn: sa.Connection, store: BlobStore, report_id: int, page: int = 1, size: int = 500) -> dict[str, Any]:
    """A page of the rows (`Row` of the contract): {rows, rows_total, page, size}; `size` ≤ 5000 (the caller checks)."""
    path = _path(conn, store, report_id, DATA)
    if path is None:
        raise broken()
    page, size = max(int(page), 1), max(int(size), 1)
    part = sd.read_page(path, (page - 1) * size, size)
    return {"rows": sd.table_rows(part), "rows_total": sd.num_rows(path), "page": page, "size": size}


def read_curve(conn: sa.Connection, store: BlobStore, report_id: int) -> dict[str, Any] | None:
    import pyarrow.parquet as pq

    path = _path(conn, store, report_id, CURVE)
    return None if path is None else sd.table_curve(pq.read_table(path))


def read_control(conn: sa.Connection, store: BlobStore, report_id: int) -> dict[str, Any] | None:
    path = _path(conn, store, report_id, CONTROL)
    return None if path is None else dict(json.loads(path.read_bytes()))


def xlsx_sha(conn: sa.Connection, report_id: int) -> str | None:
    found = _owned(conn, report_id).get(XLSX)
    return None if found is None else found[0]


def rebuild_params(conn: sa.Connection, report_id: int) -> dict[str, Any]:
    """What the job `resources.report` is given to rebuild the report in place («Перестроить», `lineage.rebuild`):
    the stored parameters, the report and its version. 409 `report_building`."""
    row = _row(conn, report_id)
    _writable(conn, row)
    return {"params": dict((row.meta or {}).get("params") or {}), "name": row.name, "save": True,
            "report_id": int(row.id), "expected_version": int(row.version)}  # fmt: skip


def fingerprint(data_sha_: str | None, algo: str, version: int) -> str:
    """The state of a report in the graph: the SHA-256 of its `data` and ALGO (ADR 0027 решение 21); a report without
    a file: its version."""
    if not data_sha_:
        return str(int(version))
    return "r:" + hashlib.sha256(f"{data_sha_}|{algo}".encode()).hexdigest()[:24]
