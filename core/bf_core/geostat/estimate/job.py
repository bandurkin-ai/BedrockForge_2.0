"""The job `estimate.run` — the rows of a plan of the estimation into the fields of a block model (подшаг 18.12; ТЗ 8.8;
ADR 0024 §7, решения 10–11; ADR 0025; docs/progress/e18-api.md §2.1–2.3, §6, §10).

    out = run_estimate(engine, folder, params, tick, threads=None, job_id=None, memory_mb=None)   # the body of the job

`params` — the body of `POST /estimate/run` checked by the route: `plan_id | estimation_ids`, `expected_version` (of the
block model), `replace`. The route may add `samples` (the largest number of the samples of a row) for `memory_mb`.

What it does (modules `job_plan.py`, `job_run.py`):
  * «Пробы» — the plan against the project (404, 409 `version_conflict`, `source_lost`, `field_exists` without
    `replace`, 422), then every row reads its samples (`samples.load`, the origin is the centre of their extent now)
    and compiles (`kernel.compile_row`);
  * «Поиск и кригинг: проход k» — the model by chunks of 2¹⁸ rows, pieces of ≤ 2¹⁶ blocks on `threads` threads, in
    order; a block outside the domain of its row or the restrictor of the target is STATUS 1 (Д-27: every other field
    is empty, never 0); rows of one prefix fill one set of fields, each its own domain; a repeat of some rows
    rewrites their domains only, the others are carried from the current fields (№ 77);
  * «Запись» — one file of fields (`FieldsWriter`), and in one transaction `commit_fields`, `meta.last_run` and the
    inputs of every row (`record_run`), `lineage.record` of every field. A cancel, a failure and a stale version leave
    no file, no field and no row changed.

The answer (`contract_answers.run_result`): {model_id, version, plan_id | None, fields [names], rows [{estimation_id,
prefix, label, counts {targets, by_status [4], by_pass [P], singular [P], samples {…}}}], merged [{prefix,
estimation_ids}] (prefixes of two rows or more), log [lines], stages {seconds}, events}. `targets` and `by_status`
count the rows of the model (a block outside the domain of the row is STATUS 1); `by_pass`, `singular` — the blocks
the kernel computed (`sub: parent`: parents).

The memory: `memory_mb(params)` is what the scheduler waits for; in the job the pool is cut to the budget (`limits`).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import schema as bs
from bf_core.blocks.fields_store import FieldsWriter, commit_fields
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore

from . import job_plan as jp
from . import store as sto
from . import store_base as sb

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext
    from bf_core.lineage.store import Node

    from . import job_run as jr

KIND = "estimate.run"
TITLE = "Оценка содержаний"
S_SAMPLES, S_SEARCH, S_WRITE = "Пробы", "Поиск и кригинг", "Запись"
Tick = Callable[[float | None, str | None], None]
AT_COMPILE, AT_RUN, AT_WRITE = 0.02, 0.12, 0.94  # the share of the progress a stage starts at


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


class _Clock:
    """Passes the ticks and counts the seconds of the stages (the message starts with the name of a stage)."""

    def __init__(self, tick: Tick) -> None:
        self.tick, self.t0 = tick, time.perf_counter()
        self.at, self.stage = self.t0, "samples"
        self.took: dict[str, float] = {}

    def __call__(self, progress: float | None = None, message: str | None = None) -> None:
        self.tick(progress, message)
        key = (
            None
            if message is None
            else ("write" if message.startswith(S_WRITE) else "search" if message.startswith(S_SEARCH) else None)
        )
        if key is not None and key != self.stage:
            now = time.perf_counter()
            self.took[self.stage] = self.took.get(self.stage, 0.0) + now - self.at
            self.at, self.stage = now, key

    def stages(self) -> dict[str, float]:
        now = time.perf_counter()
        took = {**self.took, self.stage: self.took.get(self.stage, 0.0) + now - self.at}
        return {**{k: round(v, 3) for k, v in took.items()}, "total": round(now - self.t0, 3)}


# --- the memory --------------------------------------------------------------------------------------------------

#: MB: the interpreter, the kernels, the model's reader; a sample of a row (arrays, the tree); a block of a piece (the
#: candidates of the tree and the selection of the kernel, per candidate) — measured by `bench` of 18.25
BASE_MB, SAMPLE_MB, CAND_B, PIECE_B = 350.0, 0.0006, 16.0, 520.0


def _k_c(plan: jp.Plan) -> int:
    k = 64
    for r in plan.rows:
        for p in r.spec.search.passes:
            k = max(k, 4 * int(p.n_max))
    return k


def limits(plan: jp.Plan, threads: int, budget_mb: float | None) -> tuple[int, int]:
    """(threads, blocks of a piece): a piece is cut when the candidates of the tree are many (`n_max` large), the
    threads — to the budget in MB (None — no budget)."""
    k_c = _k_c(plan)
    portion = int(min(jp.PORTION, max(4096, jp.PORTION * 96 // k_c)))
    per_thread = portion * (k_c * CAND_B + PIECE_B) / 2**20
    if budget_mb is not None:
        fixed = BASE_MB + SAMPLE_MB * sum(r.samples.n for r in plan.rows if r.samples is not None)
        fixed += jp.CHUNK_ROWS * (len(plan.read) + 4) * 8 / 2**20
        threads = min(threads, int((float(budget_mb) - fixed) // per_thread))
    return max(1, int(threads)), portion


def memory_mb(params: dict[str, Any]) -> int:
    """What the scheduler waits for (ADR 0002 §4): the samples of the rows (`samples` of the route, 300 000 when it
    does not say), the pieces of the threads, the chunk of the model."""
    from bf_core.composites import strips

    rows = max(1, len(params.get("estimation_ids") or []) or int(params.get("plan_rows") or 2))
    samples = int(params.get("samples") or 300_000)
    threads = int(params.get("threads") or strips.default_threads())
    per_thread = jp.PORTION * (96 * CAND_B + PIECE_B) / 2**20
    return int(BASE_MB + rows * samples * SAMPLE_MB + threads * per_thread + 120)


# --- the answer ----------------------------------------------------------------------------------------------------


def _blocks(n: int) -> str:
    tail = n % 100
    if tail % 10 == 1 and tail != 11:
        return f"{n} блок"
    if 2 <= tail % 10 <= 4 and not 12 <= tail <= 14:
        return f"{n} блока"
    return f"{n} блоков"


def _samples_counts(c: dict[str, Any]) -> dict[str, int]:
    """`counts.samples` of the contract (rows = missing + no_position + outside_domain + used)."""
    return {"rows": int(c.get("rows", 0)), "missing": int(c.get("missing", 0)),
            "no_position": int(c.get("no_position", 0)), "outside_domain": int(c.get("outside_domain", 0)),
            "used": int(c.get("selected", 0)), "merged": int(c.get("merged_rows", 0)),
            "topcut": int(c.get("capped", 0)), "soft": int(c.get("s", 0)),
            "no_weight": int(c.get("no_weight", 0))}  # fmt: skip


def _counts(plan: jp.Plan, r: jp.RowRun) -> dict[str, Any]:
    t = r.tally
    assert r.samples is not None
    return {"targets": plan.model.rows, "by_status": [int(x) for x in t.by_status],
            "by_pass": [int(x) for x in t.by_pass], "singular": [int(x) for x in t.singular],
            "samples": _samples_counts(r.samples.counts)}  # fmt: skip


def _log(plan: jp.Plan) -> list[str]:
    lines: list[str] = []
    many = len(plan.rows) > 1
    for r in plan.rows:
        assert r.samples is not None
        who = f"«{r.name}»: " if many else ""
        t = r.tally
        lines.append(f"{who}оценено {_blocks(int(t.by_status[0]))}; вне домена или ограничителя — "
                     f"{int(t.by_status[1])}, мало проб — {int(t.by_status[2])}, вырожденная система — "
                     f"{int(t.by_status[3])}.")  # fmt: skip
        lines += [who + note for note in r.samples.notes]
        last = len(t.singular)
        for k, n in enumerate(t.singular, 1):
            if n:
                tail = f"переход к проходу {k + 1}." if k < last else "оценки нет (STATUS 3)."
                lines.append(f"{who}Проход {k}: {_blocks(int(n))} — вырожденная система, {tail}")
        if t.neg_kv:
            lines.append(f"{who}У {_blocks(t.neg_kv)} дисперсия кригинга отрицательна (округление): записано 0.")
        lines += [who + w for w in r.restrictor.warnings]
    kept = [s.name for g in plan.groups if g.carry for s in g.siblings]
    if kept:
        who = ", ".join(f"«{n}»" for n in dict.fromkeys(kept) if n)
        tail = f" (строки {who})." if who else "."
        lines.append(f"Домены строк, не вошедших в запуск, сохранены: их значения перенесены из прежних полей{tail}")
    return lines


def _events(plan: jp.Plan) -> list[list[Any]]:
    return [["objects.changed", {"kind": bs.KIND, "id": plan.model.id}],
            *[["geostat.changed", {"kind": sb.KIND, "id": r.id}] for r in plan.rows]]  # fmt: skip


# --- the lineage ------------------------------------------------------------------------------------------------------


def _record_fields(conn: sa.Connection, plan: jp.Plan) -> None:
    """`lineage.record` of every field written: its inputs are the rows that fill it — those of the run and the
    siblings whose blocks it carries (ADR 0025 §4: in the transaction of the write)."""
    from bf_core import lineage

    for g in plan.groups:
        ids = [r.id for r in g.rows] + ([s.id for s in g.siblings] if g.carry else [])
        for c in g.columns:
            dst = lineage.Node("bm_field", plan.model.id, c["name"].casefold())
            lineage.record(conn, dst, [lineage.Node(sto.LINEAGE_KIND, i) for i in ids])


# --- the run ----------------------------------------------------------------------------------------------------------


def _compile(plan: jp.Plan, project: Any, clock: Tick) -> None:
    from . import kernel as kn
    from . import samples as sm

    grid = plan.model.grid
    many = len(plan.rows) > 1
    blockcov = None
    for i, r in enumerate(plan.rows):
        clock(AT_COMPILE + (AT_RUN - AT_COMPILE) * i / len(plan.rows), f"{S_SAMPLES}: {r.name}" if many else S_SAMPLES)
        try:
            r.samples = sm.load(project, r.spec, cancel=lambda: clock(None, None))
            r.rk = kn.compile_row(r.spec, r.samples, block_size=grid.regular(), rotation_deg=grid.rotation,
                                  blockcov=blockcov)  # fmt: skip
        except ApiError as e:
            raise jp._err(r, e, many) from e  # noqa: SLF001
        blockcov = r.rk.blockcov or blockcov
        r.tally.by_pass = np.zeros(r.rk.n_passes, np.int64)
        r.tally.singular = np.zeros(r.rk.n_passes, np.int64)


def _merge(plan: jp.Plan, out: jr.Out) -> int:
    """The tallies of a piece into the rows; the number of the last pass that worked (0 — none)."""
    last = 0
    for r, ro in zip(plan.rows, out.rows, strict=True):
        t = r.tally
        t.by_status += ro.by_status
        t.by_pass += ro.by_pass
        t.singular += ro.singular
        t.neg_kv += ro.neg_kv
        last = max(last, ro.last_pass)
    return last


def run_estimate(
    engine: sa.Engine, folder: Any, params: dict[str, Any], tick: Tick = _noop, *, threads: int | None = None,
    job_id: int | None = None, memory_mb: float | None = None, chunk: int = jp.CHUNK_ROWS,
    portion: int | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The body of `estimate.run` (the handler, the tests and the bench in the process; module docstring)."""
    from bf_core.composites import strips

    from . import job_run as jr
    from .spec import parse_run

    run = parse_run(params)
    folder = Path(folder)
    store = BlobStore(folder)
    clock = _Clock(tick)
    clock(0.01, S_SAMPLES)
    with dialect.read_tx(engine) as conn:
        plan = jp.build(conn, store, run)
    project = SimpleNamespace(engine=engine, uid=f"job:{folder}", folder=folder)
    _compile(plan, project, clock)
    n_threads, piece = limits(plan, strips.default_threads() if threads is None else int(threads), memory_mb)
    env = jr.Env(plan, piece if portion is None else int(portion))
    total, last = max(plan.model.rows, 1), 1
    with FieldsWriter(store, plan.model, run.expected_version) as w:  # type: ignore[arg-type]  # ModelView: settable
        for g in plan.groups:
            for c in g.columns:
                w.add(c["name"], c["dtype"], unit=c["unit"], role=c["role"], description=c["description"],
                      replace=run.replace)  # fmt: skip
        clock(AT_RUN, f"{S_SEARCH}: проход 1")
        for out in jr.ordered(jr.pieces(env, clock, chunk), env.compute, n_threads, clock):
            w.write(out.values)
            last = _merge(plan, out) or last
            share = (out.start + out.n) / total
            clock(AT_RUN + (AT_WRITE - AT_RUN) * share, f"{S_SEARCH}: проход {last}")
        clock(AT_WRITE, S_WRITE)
        staged = w.finish()
    now = dialect.utcnow().isoformat()
    source = {"op": "estimate", "algo": sb.ALGO, "versions": sto._versions(),  # noqa: SLF001
              "inputs": plan.files, "params": {"estimation_ids": [r.id for r in plan.rows]}}  # fmt: skip
    try:
        with dialect.write_tx(engine) as conn:
            version = commit_fields(conn, store, staged, source=source)
            for r in plan.rows:
                assert r.samples is not None and r.rk is not None
                counts = _counts(plan, r)
                means = None if r.rk.class_means is None else [float(x) for x in r.rk.class_means]
                last_run = {"at": now, "job": job_id, "version": version, "counts": counts,
                            "merged": counts["samples"]["merged"], "class_means": means}  # fmt: skip
                sto.record_run(conn, r.id, r.stored, last_run, r.inputs, [float(x) for x in r.samples.origin])
            _record_fields(conn, plan)
    finally:
        staged.discard()  # a write transaction that did not open leaves no file without an owner
    clock(1.0, None)
    merged = [{"prefix": g.prefix, "estimation_ids": [r.id for r in g.rows]} for g in plan.groups if len(g.rows) > 1]
    return {"model_id": plan.model.id, "version": version, "plan_id": plan.plan_id, "fields": plan.names,
            "rows": [{"estimation_id": r.id, "prefix": r.prefix, "label": r.spec.target.label,
                      "counts": _counts(plan, r)} for r in plan.rows],
            "merged": merged, "log": _log(plan), "stages": clock.stages(),
            "events": _events(plan)}  # fmt: skip


# --- «Перестроить» ------------------------------------------------------------------------------------------------


def rebuild_row(svc: Any, cap: Any, node: Node, _body: dict[str, Any]) -> None:
    """The maker of the node `object:estimation` for `lineage.rebuild`: the job of this row, replacing its fields
    (the blocks of the other rows of its prefix are carried, № 77)."""
    project = svc.require_current(writable=True)
    with dialect.read_tx(project.engine) as conn:
        row = sb.stored(conn, sb.KIND, node.id)
        model_id = (row.meta or {}).get("model_id")
        version = conn.execute(sa.select(objects.c.version).where(
            objects.c.kind == bs.KIND, objects.c.id == model_id)).scalar()  # fmt: skip
    if version is None:
        raise ApiError(409, "source_lost", f"Блочной модели строки «{row.name}» нет в проекте.",
                       "Выберите существующую модель в параметрах оценки.")  # fmt: skip
    cap.submit(KIND, {"estimation_ids": [node.id], "expected_version": int(version), "replace": True},
               title=f"Оценка: {row.name}")  # fmt: skip


def register_rebuild() -> None:
    """`lineage.rebuild.register` for the row (the owner of itself) and for the fields of an estimation (their owner
    is the row); the other fields of a model keep their makers. Safe to repeat."""
    from bf_core.lineage import rebuild as rb

    rb.register("object:estimation", lambda _conn, node: node, rebuild_row)
    cur = rb.MAKERS.get("bm_field")
    if cur is None or getattr(cur.owner, "estimate_aware", False):
        return
    prev = cur.owner

    def owner(conn: sa.Connection, node: Node) -> Node | None:
        return sto.field_owner(conn, node) or prev(conn, node)

    owner.estimate_aware = True  # type: ignore[attr-defined]
    rb.register("bm_field", owner, cur.prepare)


register_rebuild()


# --- the handler ----------------------------------------------------------------------------------------------------


@handler(KIND, TITLE, queue="heavy", estimate_memory=memory_mb)
def estimate_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    try:
        return run_estimate(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick, job_id=ctx.id,
                            memory_mb=ctx.job.get("memory_mb"))  # fmt: skip
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False) from None
