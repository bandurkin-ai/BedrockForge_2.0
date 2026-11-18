"""Job `import.drillholes` (ТЗ 7.7): files of one wizard → one set, all or nothing.

Order: collars, surveys, then interval tables (built aside in portions). One final transaction replaces the
collars and surveys, swaps every built table in, records the import history and makes a new set visible.
The source files go into the project storage last (ТЗ 6.7 «исходный файл — последним шагом»).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import import_log, objects
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler

ORDER = {"collar": 0, "survey": 1, "assay": 2, "interval": 3, "event": 4}


def _memory(params: dict[str, Any]) -> int:
    """Number columns as float64 plus text: about 3× the file size in memory, at least 512 MB."""
    total = 0
    for f in params.get("files", []):
        try:
            total += Path(f["path"]).stat().st_size
        except OSError:
            pass
    return max(512, int(total * 3 / 2**20))


def options_of(raw: dict[str, Any] | None) -> Any:
    from bf_core.formats.table import ReadOptions

    raw = raw or {}
    return ReadOptions(
        encoding=raw.get("encoding") or None,
        delimiter=raw.get("delimiter") or None,
        header_row=int(raw.get("header_row", 1)),
        sheet=raw.get("sheet") or None,
        text_columns=tuple(raw.get("text_columns") or ()),
    )


@handler("import.drillholes", "Импорт скважин", queue="heavy", estimate_memory=_memory)
def import_drillholes(ctx: JobContext) -> dict[str, Any]:
    from bf_core.drillholes import store as dh
    from bf_core.formats.errors import FormatError
    from bf_core.formats.registry import read_table

    from .router import set_name
    from .service import duplicate_targets, normalize

    p = ctx.params
    files = sorted(p["files"], key=lambda f: ORDER.get(f["kind"], 9))
    engine = ctx.engine()
    n_files = len(files)

    done = _committed(engine, ctx.id, files, new_set=not p.get("set_id"))
    if done is not None:  # a retry after the final transaction: the data is in, only the copies may be missing
        ctx.log("Данные уже записаны предыдущей попыткой: сохраняются исходные файлы", "info")
        result, pending = done
        _store_sources(ctx, engine, pending)
        return result
    clash = duplicate_targets(files)
    if clash:
        raise JobError("files_clash", clash, "Уберите лишний файл или дайте таблице другое название.")

    with dialect.write_tx(engine) as conn:
        if p.get("set_id"):
            set_id = int(p["set_id"])
            exists = conn.execute(
                sa.select(objects.c.id).where(objects.c.id == set_id, objects.c.kind == dh.SET_KIND)
            ).first()
            if exists is None:
                raise JobError("set_not_found", "Набор скважин не найден.", "Выберите набор заново.")
            new_set = False
        else:
            name = set_name(p.get("set_name")) or "Скважины"
            try:
                set_id = dh.create_set(conn, name, job_id=ctx.id, crs=p.get("crs"))
            except dh.NameTaken:
                raise JobError(
                    "name_taken", f"Набор «{name}» уже есть.", "Загрузите в него или выберите другое название."
                ) from None
            new_set = True

    collars: list[Any] = []
    collar_file: dict[str, str] = {}  # hole → file: one hole in two collar files is ambiguous
    surveys: list[Any] | None = None
    survey_file: dict[str, str] = {}
    built: list[int] = []
    history: list[dict[str, Any]] = []
    for k, f in enumerate(files):
        share = 1.0 / max(n_files, 1)
        base = k * share
        label = Path(f["path"]).name

        def progress(
            frac: float | None, msg: str | None, base: float = base, share: float = share, label: str = label
        ) -> None:
            ctx.tick(None if frac is None else base + share * 0.5 * frac, f"{label}: {msg}" if msg else label)

        try:
            table = read_table(f["path"], options_of(f.get("options")), progress)
        except FormatError as exc:
            raise JobError(exc.code, f"«{label}»: {exc.message}", exc.hint) from exc
        except OSError as exc:
            raise JobError(
                "file_unreadable", f"«{label}»: файл не читается ({exc.strerror or exc}).", "Проверьте путь и доступ."
            ) from exc
        for w in table.warnings:
            ctx.log(f"{label}: {w}", "warn")
        norm, issues = normalize(
            table,
            f["kind"],
            f.get("mapping") or {},
            f.get("components") or [],
            f.get("attributes") or [],
            force=bool(f.get("force")),
            table_name=f.get("table_name"),
        )
        for i in issues:
            ctx.log(
                f"{label}: {i.message}" + (f" ({i.count})" if i.count else ""),
                "warn" if i.severity == "warning" else "error",
            )
        if norm is None:
            first = next(i for i in issues if i.severity == "error")
            raise JobError(
                "import_check_failed", f"«{label}»: {first.message}", "Исправьте назначение колонок в мастере импорта."
            )
        del table  # the read table is no longer needed: keep the memory for the next file
        written: dict[str, Any] = {"rows_read": norm.rows, **norm.counts}
        if norm.kind == "collar":
            for c in norm.collars:
                _claim(collar_file, c.name, label, "устьев")
            collars.extend(norm.collars)
            written["collars"] = len(norm.collars)
        elif norm.kind == "survey":
            mine = {r[0] for r in norm.surveys}
            for hole in mine:
                _claim(survey_file, hole, label, "инклинометрии")
            with dialect.read_tx(engine) as conn:  # before the tables are built; checked again when written
                trenches = dh.trench_surveys(conn, set_id, mine)
            if trenches:
                raise _trench_surveys(trenches)
            surveys = (surveys or []) + norm.surveys
            written["surveys"] = len(norm.surveys)
        else:
            assert norm.table is not None

            def write_tick(frac: float | None, msg: str | None, base: float = base, share: float = share) -> None:
                ctx.tick(None if frac is None else base + share * (0.5 + 0.5 * frac), msg)

            out = dh.write_table(engine, set_id, norm.table, job_id=ctx.id, swap=False, tick=write_tick)
            built.append(out["table_id"])
            written["rows"] = out["rows"]
        history.append(
            {"file": f, "label": label, "counts": written, "format": Path(f["path"]).suffix.lower().lstrip(".")}
        )

    ctx.tick(0.97, "Запись в набор")
    result = {"set_id": set_id, "new_set": new_set, "files": [{"file": h["label"], **h["counts"]} for h in history]}
    with dialect.write_tx(engine) as conn:  # the final transaction: all or nothing
        if collars:
            dh.replace_collars(conn, set_id, collars)
        if surveys is not None:
            try:
                dh.replace_surveys_in(conn, set_id, surveys, lambda _f, _m: None)
            except dh.TrenchSurveys as exc:
                raise _trench_surveys(exc.names) from None
        for table_id in built:
            dh.swap_in(conn, set_id, table_id)
        dh.prune_empty_holes(conn, set_id)
        logs = []
        for h in history:
            res = conn.execute(
                sa.insert(import_log).values(
                    object_id=set_id,
                    job_id=ctx.id,  # links the history to the job log; a retry finds the import done by it
                    file_name=h["label"],
                    format=h["format"],
                    target=h["file"]["kind"],
                    mapping={
                        k: h["file"].get(k)
                        for k in ("options", "mapping", "components", "attributes", "force", "table_name")
                    },
                    counts=h["counts"],
                    created_at=dialect.utcnow(),
                )
            )
            key = res.inserted_primary_key
            assert key is not None
            logs.append((int(key[0]), h["file"]["path"], h["label"]))
        if new_set:
            conn.execute(sa.update(objects).where(objects.c.id == set_id).values(state="ready", job_id=None))
        elif p.get("crs"):
            conn.execute(sa.update(objects).where(objects.c.id == set_id).values(crs=p["crs"]))
        # a new version: snapshots of collars and surveys and the trajectories are built again (ТЗ 7.10, 7.11)
        conn.execute(
            sa.update(objects)
            .where(objects.c.id == set_id)
            .values(updated_at=dialect.utcnow(), version=objects.c.version + 1)
        )

    # Committed: from here on nothing may fail the job or ask to cancel it (the data is in the project).
    _store_sources(ctx, engine, logs)
    _drop_stale_snapshots(ctx, engine)
    return result


def _trench_surveys(names: list[str]) -> JobError:
    """«Заменить» of the surveys never touches the stations of trenches (the vertices of their polylines), and
    angles for a trench would give it a second geometry: the import is refused, nothing is written."""
    shown = ", ".join(f"«{n}»" for n in names[:10]) + (f" и ещё {len(names) - 10}" if len(names) > 10 else "")
    return JobError(
        "survey_for_trench",
        f"Инклинометрия не загружена: в файле есть замеры канав ({shown}), а их ход задан вершинами линий.",
        "Уберите строки канав из файла инклинометрии. Геометрию канавы меняет «Импорт канав» из файла линий.",
    )


def _claim(owner: dict[str, str], hole: str, label: str, what: str) -> None:
    other = owner.setdefault(hole, label)
    if other != label:
        raise JobError(
            "hole_in_two_files",
            f"Скважина «{hole}» есть в двух файлах {what}: «{other}» и «{label}».",
            "Оставьте скважину в одном файле или загрузите файлы по очереди.",
        )


def _committed(
    engine: sa.Engine, job_id: int, files: list[dict[str, Any]], new_set: bool
) -> tuple[dict[str, Any], list[tuple[int, str, str]]] | None:
    """When an earlier attempt of this job got past the final transaction: its result, and the source files
    still to copy (log id, path, name). The history rows are in the order of `files`."""
    with dialect.read_tx(engine) as conn:
        rows = conn.execute(
            sa.select(
                import_log.c.id,
                import_log.c.object_id,
                import_log.c.file_name,
                import_log.c.counts,
                import_log.c.sha256,
            )
            .where(import_log.c.job_id == job_id)
            .order_by(import_log.c.id)
        ).all()
    if not rows:
        return None
    result = {
        "set_id": rows[0].object_id,
        "new_set": new_set,
        "files": [{"file": r.file_name, **(r.counts or {})} for r in rows],
    }
    pending = [(r.id, f["path"], r.file_name) for r, f in zip(rows, files, strict=False) if r.sha256 is None]
    return result, pending


def _store_sources(ctx: JobContext, engine: sa.Engine, logs: list[tuple[int, str, str]]) -> None:
    """Source files into the project storage — the last step (ТЗ 6.7): a failed copy is a warning, never a
    failure of the import, and no cancel is honoured here."""
    from bf_core.storage.blobs import BlobStore

    if not ctx.project_folder:
        return
    store = BlobStore(ctx.project_folder)
    for log_id, path, label in logs:
        try:
            staged = store.stage_file(Path(path))
            with dialect.write_tx(engine) as conn:
                sha = store.commit_staged(
                    conn,
                    staged,
                    media_type="application/octet-stream",
                    owner_kind="import",
                    owner_id=log_id,
                    role="source",
                )
                conn.execute(sa.update(import_log).where(import_log.c.id == log_id).values(sha256=sha))
        except Exception as exc:  # noqa: BLE001 - the import is done; the copy is best effort
            ctx.log(f"{label}: исходный файл не сохранён в проекте ({exc})", "warn")


def _drop_stale_snapshots(ctx: JobContext, engine: sa.Engine) -> None:
    """Parquet snapshots of tables this import replaced (cache/tables/<id>-…): they are never read again."""
    from bf_core.db.schema import dh_tables

    if not ctx.project_folder:
        return
    folder = ctx.project_folder / "cache" / "tables"
    if not folder.is_dir():
        return
    with dialect.read_tx(engine) as conn:
        alive = {str(i) for i in conn.execute(sa.select(dh_tables.c.id)).scalars()}
    for f in folder.glob("*.parquet"):
        if f.name.split("-", 1)[0] not in alive:
            f.unlink(missing_ok=True)
