"""The job `blocks.check` (подшаг 17.41; ТЗ 7.31, 8.7.2, Т8.7-5; docs/progress/e17-api.md §11): the checks of a stored
model are made again and written into `meta.checks`.

    out = run_check(engine, folder, {"model_id": id}, tick=tick)

`checks.check_cells` reads the model by the row groups of all its files (the FRAC of another file too) and gives the
stored form of `meta.checks`; it is written in one transaction. The version of the model does not change (the files
and the fields are the same), the row of `objects` keeps `updated_at`. The model changed while the check ran — 409
`version_conflict {version}`, nothing is written. A cancel (at any tick) writes nothing. The result: `{model_id,
checks, events}`; the queue is quick, the route is `POST /blocks/{id}/check` (`routes_export.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import schema as bs
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

KIND = "blocks.check"
TITLE = "Проверка блочной модели"
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def run_check(engine: sa.Engine, folder: Any, params: dict[str, Any], *, tick: Tick = _noop) -> dict[str, Any]:
    """The body of `blocks.check` (the handler and the tests). `params` — {model_id}; 404 `block_model_not_found`."""
    from . import checks
    from .fields_store import version_conflict
    from .model import ANY, Model
    from .store import not_found

    store = BlobStore(Path(folder))
    tick(0.0, "Чтение")
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, int(params["model_id"]), ANY)

    def progress(fraction: float | None, message: str | None = None) -> None:
        tick(0.02 + 0.93 * (fraction or 0.0), message)

    report = checks.check_cells(store, model.shas(), model.meta, tick=progress)
    tick(0.96, "Запись")
    with dialect.write_tx(engine) as conn:
        row = conn.execute(
            sa.select(objects.c.version, objects.c.meta)
            .where(objects.c.kind == bs.KIND, objects.c.id == model.id, objects.c.state == "ready")
            .with_for_update()
        ).first()
        if row is None:
            raise not_found()
        if int(row.version) != model.version:
            raise version_conflict(int(row.version))
        meta = {**dict(row.meta), "checks": report.checks}
        conn.execute(sa.update(objects).where(objects.c.id == model.id).values(meta=meta))
    tick(1.0, "Готово")
    return {"model_id": model.id, "checks": report.checks,
            "events": [["objects.changed", {"kind": bs.KIND, "id": model.id}]]}  # fmt: skip


def memory_mb(_params: dict[str, Any]) -> int:
    """Two row groups of the fields and the keys of the portion (2²⁰ rows each) and the working arrays of the checks."""
    return 768


@handler(KIND, TITLE, queue="quick", estimate_memory=memory_mb)
def check_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        return run_check(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None
