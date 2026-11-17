"""Jobs of the audit (ТЗ 7.12): `audit.run` — the checks over a set; `audit.export` — the Excel report."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler


def _memory(params: dict[str, Any]) -> int:
    """Samples as columns (≈ 0,2 KB a row with the sample number) plus the sort orders."""
    return max(512, int(int(params.get("rows") or 0) * 250 / 2**20) + 256)


@handler("audit.run", "Аудит данных скважин", queue="heavy", estimate_memory=_memory)
def audit_run(ctx: JobContext) -> dict[str, Any]:
    from .service import run

    p = ctx.params
    out = run(
        ctx.engine(),
        int(p["set_id"]),
        table=p.get("table"),
        config=p.get("config") or {},
        job_id=ctx.id,
        tick=lambda f, m=None: ctx.tick(f, m),
    )
    ctx.tick(1.0, "Готово")
    return out


@handler("audit.export", "Отчёт аудита Excel", queue="quick")
def audit_export(ctx: JobContext) -> dict[str, Any]:
    import os

    from bf_core.db import dialect

    from .report import write_report
    from .service import latest

    p = ctx.params
    target = Path(p["path"])
    if target.suffix.lower() != ".xlsx":
        target = target.with_name(target.name + ".xlsx")
    engine = ctx.engine()
    with dialect.read_tx(engine) as conn:
        res = latest(conn, int(p["set_id"]))
        if res is None:
            raise JobError("no_audit", "Аудит набора ещё не запускался.", "Запустите аудит.")
        part = target.with_name(target.name + ".part")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            write_report(conn, int(p["set_id"]), res, part, tick=lambda f, m=None: ctx.tick(f, m))
            os.replace(part, target)
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise JobError(
                "file_unwritable",
                f"Файл не записан: {exc.strerror or exc}.",
                "Выберите другую папку или закройте файл в Excel.",
            ) from exc
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    return {"path": str(target)}
