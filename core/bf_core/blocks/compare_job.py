"""The job `estimate.compare` — two estimates of a block model against each other (подшаг 20.10; ТЗ 07-35 Д-37; ADR 0027
решения 25, 28; `docs/progress/e20-api.md` §6, §9.2). The plan and the numbers — `compare.py`.

    out = run_compare(engine, folder, params, tick=tick)    # the body of the handler and of the tests

`params` — the body of `POST /blocks/{id}/compare` {field_a, field_b, domain?, out?} and `model_id`; `expected_version`
(optional: the version of the model the window saw; the route puts the current one) — another version at the start or
at the commit is 409 `version_conflict`. The job writes the field Δ = A − B (f8; NaN where A or B has a gap), the name
`out` or `<A>_<B>_D`; the field of an earlier comparison of the same name is rewritten, any other field of that name —
409 `field_exists`. `source` of the field: {op: "compare", algo, versions, inputs, params {a, b, domain, out}, at};
`inputs` — the SHA-256 of the files of A and B; the graph (`lineage.record` in the transaction of the write): the inputs
of Δ are the fields A and B, so a rewritten estimate makes Δ «устарел». The file is committed whole in the last stage
(«Запись»), or nothing is left: a cancel, a failure and a stale version leave neither the file nor a row.

Stages: «Чтение», «Расчёт», «Запись». Result (e20-api §9.2):
  {model_id, field, a, b, field_written: true, version, rows, stats, histogram, domains, status, warnings,
   stages {read, calc, write, total}, events}
`events` — `objects.changed {kind: "block_model", id}`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import calc_job
from . import compare as cmp
from .fields_store import FieldsWriter, commit_fields, version_conflict
from .jobs import Clock
from .model import CHUNK, Model

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext
    from bf_core.resources.spec import FieldCompare

KIND = "estimate.compare"
TITLE = "Сравнение двух оценок"
S_READ, S_CALC, S_WRITE = "Чтение", "Расчёт", "Запись"
KEYS = {S_READ: "read", S_CALC: "calc", S_WRITE: "write"}
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def parse(model_id: int, body: Any) -> tuple[FieldCompare, int | None]:
    """The body of the request: the spec (422 `bad_param`) and `expected_version` (None — the current)."""
    from bf_core.resources.spec import parse_field_compare

    spec = parse_field_compare(model_id, body)
    version = body.get("expected_version")
    if version is not None and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
        raise cmp.bad("expected_version", "Версия модели — целое число от 1.", "Обновите свойства модели и повторите.")
    return spec, version


def check(model: Model, spec: FieldCompare, expected: int | None) -> cmp.Plan:
    """What the route checks before the job starts (409 `version_conflict`, 404, 422, 409 `field_exists`)."""
    if expected is not None and model.version != expected:
        raise version_conflict(model.version)
    return cmp.plan_of(model, spec)


def source_of(plan: cmp.Plan, inputs: list[str]) -> dict[str, Any]:
    return {"op": cmp.OP, "algo": cmp.ALGO, "versions": calc_job._versions(), "inputs": inputs,  # noqa: SLF001
            "params": {"a": plan.a, "b": plan.b, "domain": plan.domain, "out": plan.out}}  # fmt: skip


def run_compare(
    engine: sa.Engine, folder: Any, params: Mapping[str, Any], *, tick: Tick = _noop, chunk: int = CHUNK
) -> dict[str, Any]:  # fmt: skip
    """The body of `estimate.compare` (the handler and the tests)."""
    from bf_core import lineage

    spec, expected = parse(int(params["model_id"]), params)
    store = BlobStore(Path(folder))
    clock = Clock(tick, KEYS)
    clock(0.02, S_READ)
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, spec.model_id)
        version = model.version if expected is None else expected
        plan = check(model, spec, version)
        inputs = calc_job.inputs_of(conn, model, [plan.a, plan.b], None, plan.out)
    clock(0.05, S_CALC)
    tally = cmp.Tally(plan, model)
    with FieldsWriter(store, model, version) as w:  # type: ignore[arg-type]  # ModelView: settable attrs
        w.add(plan.out, "f8", unit=plan.unit, description=cmp.DESCRIPTION, replace=plan.replace)
        for ch in model.iter(plan.read, chunk):
            clock(0.05 + 0.85 * (ch.start + ch.n) / max(model.rows, 1), None)
            w.write({plan.out: tally.add(ch)})
        clock(0.92, S_WRITE)
        staged = w.finish()
    nodes = [lineage.Node("bm_field", model.id, n.casefold()) for n in dict.fromkeys([plan.a, plan.b])]
    try:
        with dialect.write_tx(engine) as conn:
            new = commit_fields(conn, store, staged, source=source_of(plan, inputs))
            lineage.record(conn, lineage.Node("bm_field", model.id, plan.out.casefold()), nodes)
    finally:
        staged.discard()  # a write transaction that did not open leaves no file without an owner
    out = tally.result()
    warnings = list(plan.warnings)
    if out["stats"]["n"] == 0:
        warnings.append("Нет блоков, где есть значения обоих полей: разность пуста.")
    return {"model_id": model.id, "field": plan.out, "a": plan.a, "b": plan.b, "field_written": True, "version": new,
            "rows": model.rows, **out, "warnings": warnings, "stages": clock.stages(),
            "events": [["objects.changed", {"kind": "block_model", "id": model.id}]]}  # fmt: skip


def memory_mb(params: dict[str, Any]) -> int:
    """A portion of 2²⁰ rows (three or six fields, the volumes, the bins) and the Δ of every block (8 bytes) for the
    percentiles and the histogram."""
    return 640 + int(params.get("rows") or 0) * 8 // 2**20


@handler(KIND, TITLE, queue="quick", estimate_memory=memory_mb)
def compare_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        return run_compare(ctx.engine(), ctx.project_folder, ctx.params, tick=ctx.tick)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None
