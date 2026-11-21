"""QAQC reports (ADR 0020 §7; подшаг 14.13): `objects(kind="qaqc_report", parent_id=NULL)`, ≤ 200 in a project,
independent of the data object (no `object_refs`): `meta` keeps `data_uid`, the name of the data and the SHA-256 of
the records. Files of the report (owner `object`, schema.BLOB_ROLES unchanged): `data` — the records file of the
data (the same blob: the report is reproducible after the data are replaced or deleted), `sidecar:inputs` — JSON
of the snapshot, `sidecar:summary` — JSON of the results by groups (no tables by samples), `sidecar:img001…` — PNG
of the charts, `sidecar:xlsx` — the book (`report_xlsx`, job `qaqc.report_file`).

    inputs = make_inputs(conn, data_id, filter, elements, charts)   # the snapshot: config, crm, filter, elements…
    report_id = create(conn, store, name, inputs)                    # `building` until `qaqc.report` ends
    summary = compute(recs, inputs, tick)                             # what the job writes
    snapshot(project, report_id) → Snapshot(recs, config, crm, filter)   # calculations by `report_id`
    info(conn, report_id, summary=False), list_reports(conn), charts_of(summary, mode), stale_of(now_of(conn), meta)

`inputs`: {v, algo: "qaqc-1", data_uid, data_id, data_name, data_sha256, config (without version), crm (rows),
filter, elements, charts (`all` | `flagged` | `none`), versions {numpy, scipy, pyarrow}, created_at}.
`inputs_sha256` — SHA-256 of the canonical JSON of what the result depends on (config, crm, filter, elements,
charts, algo) together with `data_sha256`; the time and the versions are not in it. `summary`: {v, elements: {E:
{section | "section:kind": answer without `series`, `samples`, `points`, `ranked`, `batches`, `th.groups`}}};
`canonical_summary` — keys sorted, numbers by `repr`, without `counts.rows` of every answer (Т8.4-8).
«Устарел» (`stale`), computed on reading: `source_deleted` — no data with that uid; `data` — their records
changed; `config` — the settings or the reference changed; else null. Errors: 404 `qaqc_report_not_found`, 409
`name_taken`, `version_conflict`, `report_building`, `source_deleted`; 422 `too_many_reports`, `bad_param {param}`.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, blobs, objects
from bf_core.storage.blobs import BlobStore

KIND = "qaqc_report"
OWNER = "object"
ALGO = "qaqc-1"
MAX_REPORTS = 200
MAX_CHARTS = 200
MAX_NAME = 200
RECORDS, INPUTS, SUMMARY, XLSX = "data", "sidecar:inputs", "sidecar:summary", "sidecar:xlsx"
IMAGE_PREFIX = "sidecar:img"
JSON_MEDIA = "application/json"
CHART_MODES = ("all", "flagged", "none")
STRIP = ("series", "samples", "points", "ranked", "batches")
#: the sections of a report and the parameter sets they run with (a section of `calc.SECTIONS` not named here —
#: once, without parameters; `batches` — its totals are in `summary`, its table is the XLSX's)
RUNS: dict[str, list[dict[str, Any]]] = {
    "batches": [],
    "duplicates": [{"kind": k} for k in ("DUP_F", "DUP_C", "DUP_P")],
}
_lock = threading.Lock()
_inputs: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()


def not_found() -> ApiError:
    return ApiError(404, "qaqc_report_not_found", "Отчёт QAQC не найден.", "Обновите раздел «Отчёты».")


def bad(message: str, hint: str, param: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, extra={"param": param})


# --- inputs ---------------------------------------------------------------------------------------------------------


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def inputs_sha256(inputs: Mapping[str, Any]) -> str:
    what = {k: inputs.get(k) for k in ("algo", "config", "crm", "filter", "elements", "charts")}
    return hashlib.sha256(_canonical(what) + b"\n" + str(inputs["data_sha256"]).encode()).hexdigest()


def _versions() -> dict[str, str]:
    import numpy
    import pyarrow
    import scipy

    return {"numpy": numpy.__version__, "scipy": scipy.__version__, "pyarrow": pyarrow.__version__}


def current_settings(conn: sa.Connection) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from .config import get_config, get_crm

    config = {k: v for k, v in get_config(conn).items() if k != "version"}
    return config, list(get_crm(conn)["rows"])


def make_inputs(conn: sa.Connection, data_id: Any, flt: Any, elements: Any, charts: Any) -> dict[str, Any]:
    """The snapshot of a report of a data object now: 404 / 422 for what is not there or not valid."""
    from . import service as sv
    from .filters import parse_filter
    from .limits import QaqcError

    if isinstance(data_id, bool) or not isinstance(data_id, int):
        raise bad("Не выбраны данные QAQC.", "Выберите данные для отчёта.", "data_id")
    data = sv.info(conn, data_id)
    row = conn.execute(sa.select(objects.c.meta).where(objects.c.id == data_id)).scalar_one()
    names = [e["name"] for e in data["elements"]]
    if elements is None:
        chosen = names
    elif not isinstance(elements, list) or not elements or not all(isinstance(e, str) for e in elements):
        raise bad("Неверный список элементов отчёта.", "Отметьте хотя бы один элемент.", "elements")
    else:
        by_key = {n.casefold(): n for n in names}
        missing = [e for e in elements if e.casefold() not in by_key]
        if missing:
            raise ApiError(422, "field_not_found", f"В данных QAQC нет элемента «{missing[0]}».",
                           f"Элементы данных: {', '.join(names)}.", extra={"param": "elements"})  # fmt: skip
        chosen = list(dict.fromkeys(by_key[e.casefold()] for e in elements))
    mode = "flagged" if charts is None else charts
    if mode not in CHART_MODES:
        raise bad("Неверный состав графиков отчёта.", "Выберите: все, с нарушениями или без графиков.", "charts")
    try:
        parse_filter(flt)
    except QaqcError as e:
        raise ApiError(422, e.code, e.message, e.hint, extra={"param": e.param}) from e
    config, crm = current_settings(conn)
    return {
        "v": 1, "algo": ALGO, "data_uid": (row or {}).get("uid"), "data_id": data_id, "data_name": data["name"],
        "data_sha256": sv.sha_of(conn, data_id), "config": config, "crm": crm, "filter": flt, "elements": chosen,
        "charts": mode, "versions": _versions(), "created_at": dialect.utcnow().isoformat(),
    }  # fmt: skip


# --- the summary ------------------------------------------------------------------------------------------------------


def compute(recs: Any, inputs: Mapping[str, Any], tick: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """The results of every section of every element of the report (no tables by samples)."""
    from . import calc

    out: dict[str, Any] = {}
    tables: list[list[dict[str, Any]]] = []
    elements = list(inputs["elements"])
    for i, element in enumerate(elements):
        if tick is not None:
            tick(i / max(len(elements), 1), f"Элемент {element}")
        recs.load(calc.columns_of(recs.schema, element))
        ctx = calc.context(recs, inputs["config"], inputs["crm"], element, inputs.get("filter"))
        part: dict[str, Any] = {}
        for section in calc.SECTIONS:
            for params in RUNS.get(section, [{}]):
                key = section if not params.get("kind") else f"{section}:{params['kind']}"
                answer = calc.run(ctx, section, **params)
                strip = () if section == "summary" else STRIP  # the summary's `batches` is a line of totals
                part[key] = {k: v for k, v in answer.items() if k not in strip}
                if isinstance(part[key].get("th"), dict):  # the groups of Thompson–Howarth are points too
                    part[key]["th"] = {k: v for k, v in part[key]["th"].items() if k != "groups"}
        tables.append(calc.run(ctx, "batches")["batches"])
        out[element] = part
    return {"v": 1, "elements": out, "batches_any": any_line(any_status(tables))}


def any_status(tables: Sequence[Sequence[Mapping[str, Any]]]) -> dict[tuple[Any, Any], int]:
    """The status of every batch (LAB, BATCH) by any element (ADR 0020 §4, вопрос № 41): a failure if one
    element fails it; else «норма» if any element assessed it; else −1 «не оценена»."""
    out: dict[tuple[Any, Any], int] = {}
    for rows in tables:
        for b in rows:
            key, status = (b["lab"], b["batch"]), int(b["status"])
            was = out.get(key, -1)
            out[key] = 2 if 2 in (was, status) else (0 if 0 in (was, status) or 1 in (was, status) else -1)
    return out


def any_line(status: Mapping[tuple[Any, Any], int]) -> dict[str, int]:
    values = list(status.values())
    return {"total": len(values), "failed": values.count(2), "not_assessed": values.count(-1)}


def canonical_summary(summary: Mapping[str, Any]) -> bytes:
    """The summary as compared in Т8.4-8: every answer without `counts.rows`, keys sorted, numbers by `repr`."""
    body = {
        e: {k: {**a, "counts": {c: n for c, n in a.get("counts", {}).items() if c != "rows"}} for k, a in part.items()}
        for e, part in summary["elements"].items()
    }
    return _canonical({"v": summary.get("v", 1), "elements": body, "batches_any": summary.get("batches_any")})


# --- charts -----------------------------------------------------------------------------------------------------------


def charts_of(summary: Mapping[str, Any] | None, mode: str) -> dict[str, Any]:
    """The charts the interface draws for the report: [{key, section, element, kind?, group?, title}] ≤ 200, in a
    fixed order (element, section, group); `flagged` — groups with warnings or failures and every summary chart."""
    if summary is None or mode == "none":
        return {"charts": [], "total": 0, "omitted": 0}
    every: list[dict[str, Any]] = []
    for element, part in summary["elements"].items():
        for g in (part.get("standards") or {}).get("groups", []):
            hot = (g.get("warn") or 0) + (g.get("fail") or 0) > 0
            every.append(
                {
                    "section": "standards",
                    "element": element,
                    "group": {"crm": g["crm"], "lab": g["lab"]},
                    "title": f"Стандарт {g['crm']} — {g['lab'] or 'без лаборатории'}, {element}",
                    "hot": hot,
                }
            )
        for g in (part.get("blanks") or {}).get("groups", []):
            hot = (g.get("fail") or 0) > 0
            every.append(
                {
                    "section": "blanks",
                    "element": element,
                    "group": {"lab": g["lab"]},
                    "title": f"Бланки — {g['lab'] or 'без лаборатории'}, {element}",
                    "hot": hot,
                }
            )
        for key in part:
            if key.startswith("duplicates:"):
                kind = key.split(":", 1)[1]
                views = (("scatter", "рассеяние"), ("ranked", "ранжированный HARD"), ("th", "Томпсон–Ховарт"))
                for view, title in views:
                    every.append(
                        {
                            "section": "duplicates",
                            "element": element,
                            "kind": kind,
                            "view": view,
                            "title": f"Дубликаты {kind} — {title}, {element}",
                            "hot": True,
                        }
                    )
        for g in (part.get("external") or {}).get("groups", []):
            every.append(
                {
                    "section": "external",
                    "element": element,
                    "group": {"lab_main": g.get("lab_main"), "lab_check": g.get("lab_check")},
                    "title": f"Внешний контроль {g.get('lab_main')} — {g.get('lab_check')}, {element}",
                    "hot": True,
                }
            )
    chosen = every if mode == "all" else [c for c in every if c["hot"]]
    shown = chosen[:MAX_CHARTS]
    charts = [{"key": f"img{i + 1:03d}", **{k: v for k, v in c.items() if k != "hot"}} for i, c in enumerate(shown)]
    return {"charts": charts, "total": len(chosen), "omitted": len(chosen) - len(shown)}  # fmt: skip


# --- objects ----------------------------------------------------------------------------------------------------------


def _clean(name: Any) -> str:
    text = " ".join(str(name or "").split())[:MAX_NAME]
    if not text:
        raise ApiError(422, "name_empty", "Название не может быть пустым.", "Введите название отчёта.",
                       extra={"param": "name"})  # fmt: skip
    return text


def _taken(conn: sa.Connection, name: str, but: int | None = None) -> bool:
    q = sa.select(objects.c.id).where(objects.c.kind == KIND, objects.c.name_key == dialect.name_key(name))
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def free_name(conn: sa.Connection, name: str) -> str:
    name = _clean(name)
    n = 1
    candidate = name
    while _taken(conn, candidate):
        n += 1
        tail = f" ({n})"
        candidate = name[: MAX_NAME - len(tail)].rstrip() + tail
    return candidate


def _put_json(conn: sa.Connection, store: BlobStore, report_id: int, role: str, value: Any) -> str:
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id,
                                              blob_owners.c.role == role))  # fmt: skip
    return store.put_bytes(conn, _canonical(value), media_type=JSON_MEDIA, owner_kind=OWNER, owner_id=report_id,
                           role=role)  # fmt: skip


def _share_records(conn: sa.Connection, report_id: int, sha: str) -> None:
    from bf_core.storage.blobs import add_owner

    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id,
                                              blob_owners.c.role == RECORDS))  # fmt: skip
    add_owner(conn, sha, OWNER, report_id, RECORDS)


def create(conn: sa.Connection, store: BlobStore, name: Any, inputs: dict[str, Any], author: str | None = None) -> int:
    """The report object (`building`), its records (shared) and inputs; the job fills the summary."""
    count = conn.execute(sa.select(sa.func.count()).select_from(objects).where(objects.c.kind == KIND)).scalar_one()
    if int(count) >= MAX_REPORTS:
        raise ApiError(422, "too_many_reports", f"В проекте уже {MAX_REPORTS} отчётов QAQC.",
                       "Удалите ненужные отчёты.")  # fmt: skip
    final = free_name(conn, name or f"Отчёт QAQC — {inputs['data_name']}")
    now = dialect.utcnow()
    meta = {"v": 1, "uid": uuid.uuid4().hex, **_meta_of(inputs)}
    res = conn.execute(
        sa.insert(objects).values(
            kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="building",
            version=1, meta=meta, author=author, created_at=now, updated_at=now,
        )
    )  # fmt: skip
    report_id = int(res.inserted_primary_key[0])  # type: ignore[index]
    _share_records(conn, report_id, inputs["data_sha256"])
    _put_json(conn, store, report_id, INPUTS, inputs)
    return report_id


def settings_sha(config: Any, crm: Any) -> dict[str, str]:
    """SHA-256 of the canonical settings and reference: what «устарел» (`config`) compares."""
    return {"config_sha256": hashlib.sha256(_canonical(config)).hexdigest(),
            "crm_sha256": hashlib.sha256(_canonical(crm)).hexdigest()}  # fmt: skip


def _meta_of(inputs: Mapping[str, Any]) -> dict[str, Any]:
    return {**settings_sha(inputs["config"], inputs["crm"]), "data_uid": inputs["data_uid"],
            "data_name": inputs["data_name"], "data_sha256": inputs["data_sha256"],
            "inputs_sha256": inputs_sha256(inputs), "elements": list(inputs["elements"]), "filter": inputs["filter"],
            "charts": inputs["charts"], "built_at": inputs["created_at"]}  # fmt: skip


def drop_building(conn: sa.Connection, report_id: int) -> None:
    """A new report whose job could not be started: the object and its files go."""
    building = (objects.c.id == report_id) & (objects.c.state == "building")
    if conn.execute(sa.select(objects.c.id).where(building)).first() is not None:
        conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER,
                                                  blob_owners.c.owner_id == report_id))  # fmt: skip
        conn.execute(sa.delete(objects).where(building))


def set_job(conn: sa.Connection, report_id: int, job_id: int) -> None:
    building = (objects.c.id == report_id) & (objects.c.state == "building")
    conn.execute(sa.update(objects).where(building).values(job_id=job_id))


def finish(conn: sa.Connection, store: BlobStore, report_id: int, summary: dict[str, Any],
           inputs: dict[str, Any] | None = None) -> int:  # fmt: skip
    """The summary written; a rebuild also replaces the inputs and the records, drops the old charts and files,
    version + 1. → the version."""
    row = _row(conn, report_id, building=True)
    meta = dict(row.meta or {})
    values: dict[str, Any] = {"state": "ready", "job_id": None, "updated_at": dialect.utcnow()}
    if inputs is not None:
        conn.execute(sa.delete(blob_owners).where(
            blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id,
            blob_owners.c.role.like(f"{IMAGE_PREFIX}%") | (blob_owners.c.role == XLSX)))  # fmt: skip
        _share_records(conn, report_id, inputs["data_sha256"])
        _put_json(conn, store, report_id, INPUTS, inputs)
        meta.update(_meta_of(inputs))
        values["version"] = int(row.version) + 1
    _put_json(conn, store, report_id, SUMMARY, summary)
    meta["summary_size"] = len(_canonical(summary))
    values["meta"] = meta
    conn.execute(sa.update(objects).where(objects.c.id == report_id).values(**values))
    with _lock:
        _inputs.clear()
    return int(values.get("version", row.version))


def _row(conn: sa.Connection, report_id: int, building: bool = True) -> Any:
    q = sa.select(objects).where(objects.c.id == report_id, objects.c.kind == KIND)
    if not building:
        q = q.where(objects.c.state == "ready")
    r = conn.execute(q).first()
    if r is None:
        raise not_found()
    return r


def _owned(conn: sa.Connection, report_id: int) -> dict[str, tuple[str, int]]:
    rows = conn.execute(
        sa.select(blob_owners.c.role, blob_owners.c.sha256, blobs.c.size)
        .join(blobs, blobs.c.sha256 == blob_owners.c.sha256)
        .where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id)
    ).all()
    return {str(r.role): (str(r.sha256), int(r.size)) for r in rows}


def read_json(conn: sa.Connection, store: BlobStore, report_id: int, role: str) -> Any:
    found = _owned(conn, report_id).get(role)
    if found is None:
        return None
    path = store.path_of(found[0])
    if not path.is_file():
        raise ApiError(409, "qaqc_report_broken", "Файл отчёта QAQC не найден в проекте.", "Перестройте отчёт.")
    return json.loads(path.read_bytes())


@dataclass
class Now:
    """What «устарел» compares with, read once for a list: the hashes of the settings, the records of the data."""

    settings: dict[str, str]
    data: dict[Any, str | None]  # uid of a data object → SHA-256 of its records


def now_of(conn: sa.Connection) -> Now:
    from . import service as sv

    config, crm = current_settings(conn)
    rows = conn.execute(
        sa.select(objects.c.meta, blob_owners.c.sha256)
        .select_from(objects.outerjoin(blob_owners, (blob_owners.c.owner_kind == OWNER) & (blob_owners.c.owner_id
                                                     == objects.c.id) & (blob_owners.c.role == sv.ROLE)))
        .where(objects.c.kind == sv.KIND, objects.c.state == "ready")
    ).all()  # fmt: skip
    return Now(settings_sha(config, crm), {(m or {}).get("uid"): sha for m, sha in rows})


def stale_of(now: Now, meta: Mapping[str, Any]) -> str | None:
    """`source_deleted`, `data`, `config` or None (see the module docstring)."""
    uid = meta.get("data_uid")
    if uid not in now.data:
        return "source_deleted"
    if now.data[uid] != meta.get("data_sha256"):
        return "data"
    if any(meta.get(k) != v for k, v in now.settings.items()):
        return "config"
    return None


def find_data(conn: sa.Connection, uid: Any) -> int | None:
    from . import service as sv

    for d in conn.execute(sa.select(objects.c.id, objects.c.meta).where(objects.c.kind == sv.KIND,
                                                                        objects.c.state == "ready")):  # fmt: skip
        if (d.meta or {}).get("uid") == uid:
            return int(d.id)
    return None


def _public(conn: sa.Connection, store: BlobStore, row: Any, with_summary: bool, now: Now) -> dict[str, Any]:
    meta = dict(row.meta or {})
    owned = _owned(conn, int(row.id))
    out: dict[str, Any] = {
        "id": int(row.id), "name": row.name, "version": int(row.version), "state": row.state,
        "stale": stale_of(now, meta) if row.state == "ready" else None, "data_name": meta.get("data_name"),
        "data_sha256": meta.get("data_sha256"), "inputs_sha256": meta.get("inputs_sha256"),
        "elements": meta.get("elements", []), "filter": meta.get("filter"), "charts": meta.get("charts"),
        "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
        "built_at": meta.get("built_at"), "job_id": row.job_id,
        "files": [{"kind": role.split(":", 1)[1], "size": size} for role, (_s, size) in sorted(owned.items())
                  if role == XLSX],
        "images": sum(1 for role in owned if role.startswith(IMAGE_PREFIX)),
        "size": sum(size for _s, size in owned.values()), "summary_size": meta.get("summary_size"),
    }  # fmt: skip
    if with_summary:
        out["summary"] = read_json(conn, store, int(row.id), SUMMARY)
    return out


def info(conn: sa.Connection, store: BlobStore, report_id: int, summary: bool = False) -> dict[str, Any]:
    return _public(conn, store, _row(conn, report_id), summary, now_of(conn))


def list_reports(conn: sa.Connection, store: BlobStore) -> list[dict[str, Any]]:
    rows = conn.execute(
        sa.select(objects).where(objects.c.kind == KIND).order_by(objects.c.name_key, objects.c.id)
    ).all()
    now = now_of(conn)
    return [_public(conn, store, r, False, now) for r in rows]


def rename(conn: sa.Connection, store: BlobStore, report_id: int, name: Any, expected: Any) -> dict[str, Any]:
    current = info(conn, store, report_id)
    if current["version"] != expected:
        raise ApiError(409, "version_conflict", "Отчёт изменили, пока вы его правили.", "Проверьте и повторите.",
                       extra={"current": current})  # fmt: skip
    clean = _clean(name)
    if _taken(conn, clean, but=report_id):
        raise ApiError(409, "name_taken", f"Отчёт «{clean}» уже есть.", "Выберите другое название.",
                       extra={"param": "name"})  # fmt: skip
    conn.execute(
        sa.update(objects)
        .where(objects.c.id == report_id)
        .values(name=clean, name_key=dialect.name_key(clean), version=int(expected) + 1, updated_at=dialect.utcnow())
    )
    return info(conn, store, report_id)


def delete(conn: sa.Connection, report_id: int) -> None:
    _row(conn, report_id)
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id))
    conn.execute(sa.delete(objects).where(objects.c.id == report_id))


def put_image(conn: sa.Connection, store: BlobStore, report_id: int, key: str, data: bytes) -> int:
    """A PNG of a chart of the list (`charts_of`): replaced when it is there. → the number of images."""
    row = _row(conn, report_id, building=False)
    summary = read_json(conn, store, report_id, SUMMARY)
    listed = {c["key"] for c in charts_of(summary, (row.meta or {}).get("charts", "flagged"))["charts"]}
    if key not in listed:
        number = int(key[3:]) if key.startswith("img") and key[3:].isdigit() else 0
        if number > MAX_CHARTS:
            raise ApiError(422, "too_many_images", f"В отчёте не больше {MAX_CHARTS} рисунков.",
                           "Лишние графики в отчёт не попадут.", extra={"param": "key"})  # fmt: skip
        raise ApiError(404, "chart_not_found", f"Графика «{key}» нет в перечне отчёта.",
                       "Запросите перечень графиков заново.", extra={"param": "key"})  # fmt: skip
    role = f"{IMAGE_PREFIX}{key[3:]}"
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id,
                                              blob_owners.c.role == role))  # fmt: skip
    store.put_bytes(conn, data, media_type="image/png", owner_kind=OWNER, owner_id=report_id, role=role)
    return sum(1 for r in _owned(conn, report_id) if r.startswith(IMAGE_PREFIX))


# --- calculations by report_id ------------------------------------------------------------------------------------


@dataclass
class Snapshot:
    recs: Any
    config: dict[str, Any]
    crm: list[dict[str, Any]]
    filter: Any
    inputs: dict[str, Any]


def snapshot(project: Any, report_id: int) -> Snapshot:
    """The records, the settings, the reference and the filter of a ready report (its inputs, cached)."""
    from . import records as r

    store = BlobStore(project.folder)
    with dialect.read_tx(project.engine) as conn:
        row = _row(conn, report_id, building=False)
        sha = _owned(conn, report_id).get(RECORDS, (None, 0))[0]
        key = (project.uid, report_id, int(row.version))
        with _lock:
            inputs = _inputs.get(key)
        if inputs is None:
            inputs = read_json(conn, store, report_id, INPUTS)
            with _lock:
                _inputs[key] = inputs
                while len(_inputs) > 16:
                    _inputs.popitem(last=False)
    path = store.path_of(sha) if sha else None
    if path is None or not path.is_file():
        raise ApiError(409, "qaqc_report_broken", "Файл записей отчёта QAQC не найден в проекте.",
                       "Перестройте отчёт.")  # fmt: skip
    recs = r.open_file(path, sha=sha, scope=project.uid)
    return Snapshot(recs, inputs["config"], inputs["crm"], inputs.get("filter"), inputs)


# --- the pictures and the book --------------------------------------------------------------------------------------


def images(conn: sa.Connection, store: BlobStore, report_id: int) -> dict[str, bytes]:
    """The PNG of the charts by their keys (`img001` …)."""
    out = {}
    for role, (sha, _size) in _owned(conn, report_id).items():
        if role.startswith(IMAGE_PREFIX):
            path = store.path_of(sha)
            if path.is_file():
                out[f"img{role[len(IMAGE_PREFIX) :]}"] = path.read_bytes()
    return out


def file_sha(conn: sa.Connection, report_id: int) -> str | None:
    found = _owned(conn, report_id).get(XLSX)
    return None if found is None else found[0]


def put_file(conn: sa.Connection, store: BlobStore, report_id: int, staged: tuple[Any, str, int]) -> str:
    """The book of the report, replacing the previous one (inside the write transaction)."""
    conn.execute(sa.delete(blob_owners).where(blob_owners.c.owner_kind == OWNER, blob_owners.c.owner_id == report_id,
                                              blob_owners.c.role == XLSX))  # fmt: skip
    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return store.commit_staged(conn, staged, media_type=media, owner_kind=OWNER, owner_id=report_id, role=XLSX)
