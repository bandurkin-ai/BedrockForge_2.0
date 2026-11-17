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
    from bf_core.storage.blobs import BlobStore

    from .service import normalize

    p = ctx.params
    files = sorted(p["files"], key=lambda f: ORDER.get(f["kind"], 9))
    engine = ctx.engine()
    store = BlobStore(ctx.project_folder) if ctx.project_folder else None
    n_files = len(files)

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
            name = str(p.get("set_name") or "Скважины").strip()
            set_id = dh.create_set(conn, name, job_id=ctx.id, crs=p.get("crs"))
            new_set = True

    collars: list[Any] = []
    surveys: list[Any] | None = None
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
            collars = norm.collars
            written["collars"] = len(collars)
        elif norm.kind == "survey":
            surveys = norm.surveys
            written["surveys"] = len(surveys)
        else:
            assert norm.table is not None

            def write_tick(frac: float | None, msg: str | None, base: float = base, share: float = share) -> None:
                ctx.tick(None if frac is None else base + share * (0.5 + 0.5 * frac), msg)

            done = dh.write_table(engine, set_id, norm.table, job_id=ctx.id, swap=False, tick=write_tick)
            built.append(done["table_id"])
            written["rows"] = done["rows"]
        history.append(
            {"file": f, "label": label, "counts": written, "format": Path(f["path"]).suffix.lower().lstrip(".")}
        )

    ctx.tick(0.97, "Запись в набор")
    with dialect.write_tx(engine) as conn:  # the final transaction: all or nothing
        if collars:
            dh.replace_collars(conn, set_id, collars)
        if surveys is not None:
            dh.replace_surveys_in(conn, set_id, surveys, lambda _f, _m: None)
        for table_id in built:
            dh.swap_in(conn, set_id, table_id)
        log_ids = []
        for h in history:
            res = conn.execute(
                sa.insert(import_log).values(
                    object_id=set_id,
                    job_id=None,
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
            log_ids.append(int(key[0]))
        if new_set:
            conn.execute(sa.update(objects).where(objects.c.id == set_id).values(state="ready", job_id=None))
        elif p.get("crs"):
            conn.execute(sa.update(objects).where(objects.c.id == set_id).values(crs=p["crs"]))
        conn.execute(sa.update(objects).where(objects.c.id == set_id).values(updated_at=dialect.utcnow()))

    # Source files into the storage — the last step: the import is done even if a copy fails.
    if store is not None:
        for h, log_id in zip(history, log_ids, strict=True):
            try:
                staged = store.stage_file(Path(h["file"]["path"]))
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
            except OSError as exc:
                ctx.log(f"{h['label']}: исходный файл не сохранён в проекте ({exc.strerror or exc})", "warn")
    ctx.tick(1.0, "Готово")
    return {"set_id": set_id, "new_set": new_set, "files": [{"file": h["label"], **h["counts"]} for h in history]}
