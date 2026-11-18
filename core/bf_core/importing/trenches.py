"""Job `import.trenches` (ТЗ 7.35 Д-16; ADR 0011 §1, §3, §5): trenches along polylines → holes of a drillhole set.

A trench — one polyline of a STR, a DXF or a table of vertices (`trench_lines.py`: reading, names, geometry) —
becomes a hole of the set: the collar is the first vertex, TD the 3D length, the type attribute «Канава» (under
the «Тип» / TYPE attribute the collars of the set already have, else TYPE) and one survey station per vertex with
its coordinates and its depth — the 3D length from the first vertex (`dh_surveys.x, y, z`; no azimuth or dip). The
desurvey takes such stations as they are (`drillholes/stations.py`), so «от/до» of samples run along the polyline.

Parameters (`ctx.params`):
  set_id | set_name, crs — the set to add to, or a new one (as `import.drillholes`);
  lines — the file of polylines and how to name and direct the trenches (`trench_lines.py`: path, layers,
    name_from, name_field, reverse …);
  samples — optionally a table «канава, от, до, …», given as a file of `import.drillholes` (path, options,
    mapping, components, attributes, force, table_name) of kind "assay" (default) or "interval". A set has one
    assay table: when the set already has one, `replace: true` is needed — it replaces the samples of all holes.
Result: set_id, new_set, trenches, vertices, length, skipped (lines that gave no trench), samples (rows),
files [{file, …counts}] — the same after a retry that finds the import already written.

Nothing is written before both files are read and checked; then everything goes in one final transaction, so a
cancelled or failed job leaves nothing (a new set and the table of samples are «building» until then). Holes of
the set that are not in the file stay as they are; a hole of the same name gets the new collar and stations (its
old stations go, its samples stay). When that hole was a drillhole — it had a collar or readings and was not a
trench — its collar and readings are replaced: such holes are named in a warning of the operation log and in
the result (`converted` — how many, `converted_names` — the first 100). The source files are kept in the
project, the history in `import_log` (target «trenches»), warnings of the readers and what was left out — in
the operation log.
"""

from __future__ import annotations

import json
from itertools import repeat
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_holes, dh_surveys, dh_tables, import_log, objects
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler

from .trench_lines import Tick, Trenches, build, listed, n_text, preview, read_polylines

__all__ = ["import_trenches", "preview"]

TRENCH = "Канава"
TYPE_KEYS = ("type", "hole_type", "тип")  # the collar attributes `drillholes.router.hole_type` reads
BATCH = 50_000
CONVERTED_KEPT = 100  # names of drillholes that became trenches kept in the result and in `import_log`


def _memory(params: dict[str, Any]) -> int:
    """Vertices twice as float64, the rows for the database and the read table of samples: at least 512 MB."""
    total = 0
    for f in (params.get("lines"), params.get("samples")):
        try:
            total += Path((f or {})["path"]).stat().st_size
        except (KeyError, OSError):
            pass
    return max(512, int(total * 6 / 2**20))


def _result(set_id: int, new_set: bool, files: list[dict[str, Any]]) -> dict[str, Any]:
    head = files[0] if files else {}
    out = {"set_id": set_id, "new_set": new_set, "files": files}
    out.update({k: head.get(k, 0) for k in ("trenches", "vertices", "length", "skipped", "converted")})
    out["converted_names"] = list(head.get("converted_names") or [])
    out["samples"] = sum(int(f.get("rows", 0)) for f in files[1:])
    return out


def _check_target(conn: sa.Connection, p: dict[str, Any], samples: dict[str, Any] | None) -> str | None:
    """Errors known before any file is read; returns the name of a new set (None: an existing one)."""
    from bf_core.drillholes.store import SET_KIND

    from .router import set_name

    if p.get("set_id"):
        set_id = int(p["set_id"])
        ready = sa.and_(objects.c.id == set_id, objects.c.kind == SET_KIND, objects.c.state == "ready")
        if conn.execute(sa.select(objects.c.id).where(ready)).first() is None:
            raise JobError("set_not_found", "Набор скважин не найден.", "Выберите набор заново.")
        if samples and (samples.get("kind") or "assay") == "assay" and not samples.get("replace"):
            assay = sa.and_(dh_tables.c.set_id == set_id, dh_tables.c.kind == "assay", dh_tables.c.state == "ready")
            name = conn.execute(sa.select(dh_tables.c.name).where(assay)).scalar()
            if name is not None:
                raise JobError(
                    "assay_exists",
                    f"В наборе уже есть таблица проб «{name}»: пробы канав заменят её целиком, по всем скважинам.",
                    "Загрузите канавы в отдельный набор, или пробы — интервальной таблицей со своим названием, "
                    "или подтвердите замену таблицы проб.",
                )
        return None
    name = set_name(p.get("set_name"))
    if not name:
        raise JobError("name_empty", "Название набора не может быть пустым.", "Введите название набора.")
    same = sa.and_(objects.c.kind == SET_KIND, objects.c.name_key == dialect.name_key(name))
    if conn.execute(sa.select(objects.c.id).where(same, objects.c.state.in_(("ready", "building")))).first():
        raise _name_taken(name)
    return name


def _name_taken(name: str) -> JobError:
    return JobError("name_taken", f"Набор «{name}» уже есть.", "Загрузите в него или выберите другое название.")


def _read_samples(ctx: JobContext, spec: dict[str, Any], tick: Tick) -> tuple[Any, dict[str, Any]]:
    """The table of samples, read and checked as a file of `import.drillholes` (ТЗ 7.7)."""
    from bf_core.formats.errors import FormatError
    from bf_core.formats.registry import read_table

    from .jobs import options_of
    from .service import normalize

    label = Path(spec["path"]).name
    kind = spec.get("kind") or "assay"
    if kind not in ("assay", "interval"):
        raise JobError("bad_kind", "Пробы канав — таблица проб или интервальная таблица.", "Выберите тип таблицы.")
    try:
        table = read_table(spec["path"], options_of(spec.get("options")), lambda f, m: tick(f, f"{label}: {m or ''}"))
    except FormatError as exc:
        raise JobError(exc.code, f"«{label}»: {exc.message}", exc.hint) from exc
    except OSError as exc:
        raise JobError(
            "file_unreadable", f"«{label}»: файл не читается ({exc.strerror or exc}).", "Проверьте путь и доступ."
        ) from exc
    for w in table.warnings:
        ctx.log(f"{label}: {w}", "warn")
    norm, issues = normalize(
        table, kind, spec.get("mapping") or {}, spec.get("components") or [], spec.get("attributes") or [],
        force=bool(spec.get("force")), table_name=spec.get("table_name"),
    )  # fmt: skip
    for i in issues:
        text = f"{label}: {i.message}" + (f" ({i.count})" if i.count else "")
        ctx.log(text, "warn" if i.severity == "warning" else "error")
    if norm is None or norm.table is None:
        first = next(i for i in issues if i.severity == "error")
        raise JobError(
            "import_check_failed", f"«{label}»: {first.message}", "Исправьте назначение колонок в мастере импорта."
        )
    return norm.table, {"rows_read": norm.rows, **norm.counts}


def _type_key(conn: sa.Connection, set_id: int) -> str:
    """The collar attribute that holds the type in this set («Тип», TYPE, HOLE_TYPE), else TYPE."""
    has = sa.and_(dh_holes.c.set_id == set_id, dh_holes.c.attrs.is_not(None))
    for attrs in conn.execute(sa.select(dh_holes.c.attrs).where(has).limit(200)).scalars():
        for key in attrs or {}:
            if str(key).casefold() in TYPE_KEYS:
                return str(key)
    return "TYPE"


def write_trenches(conn: sa.Connection, set_id: int, t: Trenches, tick: Tick) -> dict[str, Any]:
    """Collars and stations of the trenches, in the caller's transaction; other holes of the set are not touched.
    Returns {added, updated, converted}: `converted` — the names of the holes that were drillholes (a collar or
    readings, no vertices with coordinates, not typed «Канава») and are trenches now."""
    import numpy as np

    from bf_core.drillholes.store import hole_ids

    known = hole_ids(conn, set_id)
    key = _type_key(conn, set_id)
    first, length = t.xyz[t.offsets[:-1]].tolist(), t.lengths().tolist()
    fresh = json.dumps({key: TRENCH}, ensure_ascii=False)
    old = [known[name] for name in t.names if name in known]
    attrs_of: dict[int, Any] = {}
    drilled: set[int] = set()  # a collar or readings of an inclinometer, and no station with coordinates
    for a in range(0, len(old), 10_000):  # ids in portions: under the SQLite parameter limit
        part = old[a : a + 10_000]
        sv = dh_surveys.c
        counts = conn.execute(  # per hole: its survey rows and those of them that are vertices (x is set)
            sa.select(sv.hole_id, sa.func.count(), sa.func.count(sv.x)).where(sv.hole_id.in_(part)).group_by(sv.hole_id)
        )
        surveyed = {int(r[0]): (int(r[1]), int(r[2])) for r in counts}
        rows = conn.execute(
            sa.select(dh_holes.c.id, dh_holes.c.attrs, dh_holes.c.has_collar).where(dh_holes.c.id.in_(part))
        )
        for r in rows:
            attrs_of[int(r.id)] = r.attrs
            readings, vertices = surveyed.get(int(r.id), (0, 0))
            if vertices == 0 and (r.has_collar or readings):
                drilled.add(int(r.id))
        conn.execute(sa.delete(dh_surveys).where(dh_surveys.c.hole_id.in_(part)))
    added, updated, converted = [], [], []
    for name, (x, y, z), td in zip(t.names, first, length, strict=True):
        hole = known.get(name)
        if hole is None:
            added.append((set_id, name, x, y, z, td, True, fresh))
            continue
        attrs = dict(attrs_of.get(hole) or {})
        if "канав" not in str(attrs.get(key, "")).casefold():
            attrs[key] = TRENCH
            if hole in drilled:
                converted.append(name)
        updated.append((x, y, z, td, None, None, True, json.dumps(attrs, ensure_ascii=False), hole))
    tick(0.05, f"Канавы: устья — {n_text(len(t.names))}")
    dialect.bulk_insert(conn, dh_holes, ("set_id", "name", "x", "y", "z", "td", "has_collar", "attrs"), added)
    dialect.bulk_update(conn, dh_holes, ("x", "y", "z", "td", "azimuth", "dip", "has_collar", "attrs"), "id", updated)
    if added:
        known = hole_ids(conn, set_id)
    hole_of = np.repeat(np.array([known[name] for name in t.names], dtype=np.int64), np.diff(t.offsets))
    total = len(hole_of)
    for a in range(0, total, BATCH):
        tick(0.1 + 0.9 * a / total, f"Канавы: вершины — {n_text(a)} из {n_text(total)}")
        part = hole_of[a : a + BATCH].tolist()
        b = a + len(part)
        stations = zip(repeat(set_id, len(part)), part, t.depth[a:b].tolist(), *t.xyz[a:b].T.tolist(), strict=True)
        dialect.bulk_insert(conn, dh_surveys, ("set_id", "hole_id", "depth", "x", "y", "z"), list(stations))
    return {"added": len(added), "updated": len(updated), "converted": converted}


@handler("import.trenches", "Импорт канав", queue="heavy", estimate_memory=_memory)
def import_trenches(ctx: JobContext) -> dict[str, Any]:
    from bf_core.drillholes import store as dh

    from .jobs import _committed, _drop_stale_snapshots, _store_sources

    p = ctx.params
    lines: dict[str, Any] = {"name_from": "string", **(p.get("lines") or {})}
    samples: dict[str, Any] | None = p.get("samples") or None
    if not lines.get("path"):
        raise JobError("no_file", "Не выбран файл с линиями канав.", "Выберите файл STR, DXF или таблицу вершин.")
    engine = ctx.engine()
    done = _committed(engine, ctx.id, [lines, samples] if samples else [lines], new_set=not p.get("set_id"))
    if done is not None:  # a retry after the final transaction: the data is in, only the copies may be missing
        ctx.log("Данные уже записаны предыдущей попыткой: сохраняются исходные файлы", "info")
        result, pending = done
        _store_sources(ctx, engine, pending)
        return _result(result["set_id"], result["new_set"], result["files"])
    with dialect.read_tx(engine) as conn:
        new_name = _check_target(conn, p, samples)

    share = 0.5 if samples else 0.8  # of the progress: reading the lines
    polylines = read_polylines(lines, lambda f, m: ctx.tick(None if f is None else share * f, m))
    ctx.tick(share, "Канавы: длины и глубины вершин")
    t = build(polylines, lines.get("reverse") or False)
    label = Path(lines.get("filename") or lines["path"]).name
    for w in (*polylines.warnings, *t.warnings):
        ctx.log(f"{label}: {w}", "warn")
    if not t.names:
        no_z = any("Z" in w for w in t.warnings)
        raise JobError(
            "no_trenches",
            f"«{label}»: нет ни одной канавы" + (" — у линий нет координаты Z." if no_z else "."),
            "Канава — линия из двух и более вершин с координатами X, Y, Z. Проверьте файл и выбор слоёв и имени.",
        )
    length = float(t.lengths().sum())
    head = {"format": polylines.format, "trenches": len(t.names), "vertices": len(t.xyz), "length": length,
            "skipped": t.skipped}  # fmt: skip
    metres = f"{length:,.1f}".replace(",", " ").replace(".", ",")
    ctx.log(f"{label}: Канав: {n_text(len(t.names))}, вершин: {n_text(len(t.xyz))}, общая длина {metres} м", "info")
    del polylines
    table = None
    counts: dict[str, Any] = {}
    if samples:
        table, counts = _read_samples(ctx, samples, lambda f, m: ctx.tick(None if f is None else 0.5 + 0.2 * f, m))
        strangers = sorted(set(table.holes) - set(t.names))
        if strangers:
            ctx.log(f"Проб по канавам, которых нет в файле линий: {listed(strangers)}", "warn")

    with dialect.write_tx(engine) as conn:
        if new_name is None:
            set_id = int(p["set_id"])
        else:
            try:
                set_id = dh.create_set(conn, new_name, job_id=ctx.id, crs=p.get("crs"))
            except dh.NameTaken:
                raise _name_taken(new_name) from None
    table_id = None
    if table is not None:
        out = dh.write_table(engine, set_id, table, job_id=ctx.id, swap=False,
                             tick=lambda f, m: ctx.tick(None if f is None else 0.7 + 0.1 * f, m))  # fmt: skip
        table_id, counts["rows"] = out["table_id"], out["rows"]

    ctx.tick(0.8, "Запись в набор")
    logged = [(lines, label, head["format"], "trenches", head)]
    if samples:
        name = Path(samples["path"]).name
        logged.append((samples, name, Path(name).suffix.lower().lstrip("."), samples.get("kind") or "assay", counts))
    with dialect.write_tx(engine) as conn:  # the final transaction: all or nothing
        written = write_trenches(conn, set_id, t, lambda f, m: ctx.tick(None if f is None else 0.8 + 0.19 * f, m))
        converted: list[str] = written.pop("converted")
        head.update(written, converted=len(converted), converted_names=converted[:CONVERTED_KEPT])
        if table_id is not None:
            dh.swap_in(conn, set_id, table_id)
            dh.prune_empty_holes(conn, set_id)
        logs = []
        for spec, name, fmt, target, numbers in logged:
            res = conn.execute(
                sa.insert(import_log).values(
                    object_id=set_id, job_id=ctx.id, file_name=name, format=fmt, target=target,
                    mapping={k: v for k, v in spec.items() if k != "path"}, counts=numbers,
                    created_at=dialect.utcnow(),
                )  # fmt: skip
            )
            key = res.inserted_primary_key
            assert key is not None
            logs.append((int(key[0]), spec["path"], name))
        values: dict[str, Any] = {"updated_at": dialect.utcnow(), "version": objects.c.version + 1}
        if new_name is not None:
            values.update(state="ready", job_id=None)
        elif p.get("crs"):
            values["crs"] = p["crs"]
        conn.execute(sa.update(objects).where(objects.c.id == set_id).values(**values))

    # Committed: from here on nothing may fail the job or ask to cancel it (the data is in the project).
    if converted:
        ctx.log(
            f"{label}: скважины с теми же именами стали канавами — их устья и инклинометрия заменены "
            f"линиями канав, пробы остались: {listed(converted)}",
            "warn",
        )
    _store_sources(ctx, engine, logs)
    _drop_stale_snapshots(ctx, engine)
    return _result(set_id, new_name is not None, [{"file": name, **numbers} for _s, name, _f, _t, numbers in logged])
