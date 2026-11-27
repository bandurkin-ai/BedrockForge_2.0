"""The job `resources.report` — a resource report of a block model, a preview or a saved report (подшаг 20.7; ТЗ 8.10;
ADR 0027 решения 14–16, 28; docs/progress/e20-api.md §3, §6).

    out = run_report(engine, folder, params, tick, job_id=None, chunk=None)       # the body of the job

`params` — what the route checked: `{params, name?, save?, report_id?, expected_version?}`. `params` is the body of the
report (e20-api §2), parsed here again (a stored canonical form parses to itself). Three things the job can be:

  * a preview (`save` false, no `report_id`): the same calculation, nothing is written, at most 20 000 rows in the
    answer (`truncated`); it works in a project «только просмотр»;
  * a new report (`save` true): the report is written by `store.create` — the object, the files and the rows of the
    graph (`lineage.record`) in ONE transaction, the last thing the job does;
  * «Перестроить» (`report_id`; `lineage.rebuild` → `adapt_resources.prepare_rebuild` → `store.rebuild_params`): the
    same parameters calculated from nothing and written in place by `store.replace` (version + 1); the report says
    «идёт построение» (`store.mark_building`) from the first transaction to the last. The same `data` bit for bit
    (8.0.2).

The stages (the text of a message starts with it): «Чтение» (the model, the rules) → «Расчёт» (the pass, the control)
→ «Запись» (only when it saves). A cancel (`JobCancelled` from `tick`, at any stage, in the pass too) leaves nothing: a
new report is not there, a rebuilt one is as it was (`store.settle`). An error — `JobError` {code, message, hint} in
Russian. A model — or a field, a filter, a wireframe of the rules — that changed while the job was calculating:
409 `model_changed` (the rows of the graph would name what was not read; the fingerprints of the inputs are taken
before the pass and compared in the transaction of the write, `store.input_prints`).

The answer is that of e20-api §3 (`view.report_json`) plus `events`: `[["resources.changed", {kind, id}]]` after a
write. `rows_total` is every row; `rows` — the preview (20 000) or the first page of a saved report (500). The
control and the result warnings are joined in `warnings`; `form` — by `params.template_id` (the template is a view:
it is not stored). The kernels, numpy and pyarrow are imported inside the functions (the process of the API
registers the handler and must not load numba).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.jobs.registry import handler

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

KIND = "resources.report"
TITLE = "Отчёт по ресурсам"
EVENT = "resources.changed"
RESOURCE = "resource_report"
S_READ, S_CALC, S_WRITE = "Чтение", "Расчёт", "Запись"
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def memory_mb(_params: dict[str, Any] | None = None) -> int:
    """A portion of 2²⁰ rows: the columns read, the volumes, the weights and the bins (`compute.memory_mb`)."""
    return 640


def _events(report_id: int | None) -> list[list[Any]]:
    return [] if report_id is None else [[EVENT, {"kind": RESOURCE, "id": int(report_id)}]]


# --- the state of a rebuilt report ------------------------------------------------------------------------------------


def _begin(engine: sa.Engine, report_id: int, expected: Any, job_id: int | None) -> None:
    """A rebuild starts: the report is checked (404, 409 `version_conflict`) and marked «идёт построение»
    (409 `report_building`)."""
    from . import store as st

    with dialect.write_tx(engine) as conn:
        row = st._row(conn, report_id)  # noqa: SLF001 - 404 `resource_report_not_found`
        if expected is not None and int(row.version) != int(expected):
            raise st.conflict(int(row.version))
        st.mark_building(conn, report_id, job_id)


def _settle(engine: sa.Engine, report_id: int) -> None:
    """The job ended without a result: the report is as it was (a report deleted meanwhile is nothing to settle)."""
    from . import store as st

    try:
        with dialect.write_tx(engine) as conn:
            st.settle(conn, report_id)
    except ApiError:
        pass


# --- the run ----------------------------------------------------------------------------------------------------------


def _scaled(tick: Tick, lo: float, span: float, text: str) -> Tick:
    """A tick of a part of the job: the share of the part → the share of the job, with the text of the stage."""

    def inner(p: float | None = None, _m: str | None = None) -> None:
        tick(None if p is None else lo + span * max(0.0, min(1.0, p)), text)

    return inner


def _versions() -> dict[str, str]:
    from importlib import metadata

    out: dict[str, str] = {}
    for name in ("numpy", "numba", "pyarrow"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return out


def _read_fields(model: Any, plan: Any, statuses: dict[str, str]) -> list[str]:
    """The fields the report reads (the inputs of its node of the graph, ADR 0027 решение 21): the elements, the field
    of the cut-off, the density, the keys, `FRAC`, the `STATUS` fields of the estimates and `DENS_SRC` when the model
    has them; the fields of the rules are added by `store.input_nodes`."""
    from . import control as ct

    names = list(plan.core.reads()) + list(statuses.values())
    src = ct._find(model, ct.DENS_SRC)  # noqa: SLF001
    if src is not None:
        names.append(str(src["name"]))
    return list(dict.fromkeys(names))


def _table(sums: Any, plan: Any) -> Any:
    """The table `data` of every row of the pass (the order of `result.iter_rows`) — the Parquet to be staged."""
    from . import result as rr
    from . import store_data as sd

    names = list(plan.names)
    cols: dict[str, list[Any]] = {n: [] for n, _ in sd.row_columns(names, plan.nkeys)}
    for row in rr.iter_rows(sums, plan):
        flat = rr.flat_row(row, plan.nkeys)
        for key, column in cols.items():
            column.append(flat[key])
    return sd.build_table(cols, names, plan.nkeys)


def _check_model(conn: sa.Connection, store: Any, model: Any) -> None:
    """In the transaction of the write: the model is what the pass read (its version and its files)."""
    from bf_core.blocks.model import Model

    now = Model.open(conn, store, model.id)
    if now.version != model.version or now.shas() != model.shas():
        raise ApiError(409, "model_changed", "Блочная модель изменилась, пока считался отчёт.",
                       "Повторите расчёт отчёта.")  # fmt: skip


def run_report(
    engine: sa.Engine, folder: Any, params: dict[str, Any], tick: Tick = _noop, *, job_id: int | None = None,
    chunk: int | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The body of `resources.report` (the handler, the tests and the bench in the process; the module text)."""
    from bf_core.jobs.context import JobCancelled

    report_id = params.get("report_id")
    report_id = None if report_id is None else int(report_id)
    if report_id is not None:
        _begin(engine, report_id, params.get("expected_version"), job_id)
    try:
        return _run(engine, Path(folder), params, tick, report_id, chunk)
    except JobCancelled as exc:
        if report_id is not None:
            _settle(engine, report_id)
            exc.events = _events(report_id)
        raise
    except BaseException:
        if report_id is not None:
            _settle(engine, report_id)
        raise


def _run(engine: sa.Engine, folder: Path, p: dict[str, Any], tick: Tick, report_id: int | None,
         chunk: int | None) -> dict[str, Any]:  # fmt: skip
    from bf_core.blocks import calc_job, restrict
    from bf_core.blocks.model import CHUNK, Model
    from bf_core.jobs.context import JobCancelled
    from bf_core.storage.blobs import BlobStore

    from . import compute as cp
    from . import control as ct
    from . import result as rr
    from . import store as st
    from . import store_data as sd
    from . import templates, view
    from .spec import parse_params

    chunk = int(chunk or CHUNK)
    save = bool(p.get("save")) or report_id is not None
    req = parse_params(p["params"])
    store = BlobStore(folder)
    seconds: dict[str, float] = {}
    t0 = time.perf_counter()
    tick(0.0, f"{S_READ}: модель и правила отчёта")
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, req.model_id)
        spec = req.resolved(model.fields, rr.z_range_of(model))
        plan = rr.core_from_spec(model, spec)
        tree = calc_job.rules_tree(conn, spec.filter_id, spec.restrict)
        restrictor = restrict.prepare(model, tree, conn=conn, store=store)
        template, notes = None, []
        if spec.template_id is not None and report_id is None:
            template = templates.get(conn, spec.template_id)  # 404 `template_not_found`
        elif spec.template_id is not None:  # «Перестроить»: a template deleted since does not keep the report stale
            template = templates.saved_with(conn, spec.template_id)
            notes = [] if template is not None else [templates.gone(spec.template_id)]
        statuses = ct.status_fields(model, plan)
        canon = spec.canonical()
        prints = None  # what the report is made of, as the graph sees it now; the write compares (`store._record`)
        if save:
            prints = st.input_prints(conn, model.id, _read_fields(model, plan, statuses), canon.get("filter_id"),
                                     canon.get("restrict"))  # fmt: skip
        seconds["read"] = round(time.perf_counter() - t0, 4)
        tick(0.04, f"{S_CALC}: проход по блокам")
        t1 = time.perf_counter()
        calc = _scaled(tick, 0.04, 0.8, f"{S_CALC}: проход по блокам")
        sums = cp.run(model, plan.core, restrictor, chunk=chunk, tick=calc)
        seconds["kernel"] = round(time.perf_counter() - t1, 4)
        t2 = time.perf_counter()
        control = ct.build(model, plan, sums, restrictor=restrictor, rules=tree, conn=conn, store=store, chunk=chunk,
                           tick=_scaled(tick, 0.84, 0.08, f"{S_CALC}: контроль"),
                           cancel=lambda: tick(None, None))  # fmt: skip
        seconds["control"] = round(time.perf_counter() - t2, 4)
    tick(0.92, f"{S_CALC}: строки отчёта")
    res = rr.build(sums, plan, max_rows=view.FIRST_PAGE if save else view.PREVIEW_ROWS)
    roles = rr.roles_of(spec, model.fields)
    form = None if template is None else view.form_sums(sums, plan, template, roles)
    excluded = {k: int(v) for k, v in res.excluded.items()}
    ctl = view.plain(control)
    warnings = view.merge_warnings(
        [w for w in res.warnings if not (save and w.get("code") == "rows_truncated")], ctl.get("warnings") or []
    )
    mjson = view.model_json(model)
    common: dict[str, Any] = {"model": mjson, "rows": res.rows, "rows_total": res.rows_total,
                              "totals": res.totals, "curve": view.plain(res.curve), "control": ctl,
                              "excluded": excluded, "form": form, "warnings": [*warnings, *notes]}  # fmt: skip
    if not save:
        tick(1.0, f"{S_CALC}: готово")
        seconds["total"] = round(time.perf_counter() - t0, 4)
        return view.report_json(spec, report_id=None, name=str(p.get("name") or st.DEFAULT_NAME), version=0,
                                saved=False, truncated=res.truncated, seconds=seconds, **common)  # fmt: skip
    # --- the write: the files first (outside the transaction), then one transaction ------------------------------
    tick(0.94, f"{S_WRITE}: файлы отчёта")
    table = _table(sums, plan)
    curve_table = None if res.curve is None else sd.curve_table(res.curve, list(plan.names))
    staged = st.stage(store, table, curve_table, {**ctl, "excluded": excluded})
    built = st.Built(canon, {"id": model.id, "name": model.name, "version": model.version, "grid": mjson["grid"]},
                     _read_fields(model, plan, statuses), res.totals, staged, _versions(), warnings,
                     prints)  # fmt: skip
    try:
        tick(0.97, f"{S_WRITE}: запись в проект")
        t3 = time.perf_counter()
        with dialect.write_tx(engine) as conn:
            _check_model(conn, store, model)
            if report_id is None:
                from bf_core.drillholes.edit import author

                done = st.create(conn, store, p.get("name"), built, author=author())
            else:
                done = st.replace(conn, store, report_id, built, p.get("expected_version"))
            info = st.info(conn, int(done["id"]))
        seconds["write"] = round(time.perf_counter() - t3, 4)
    finally:
        staged.discard()
    try:
        tick(1.0, None)
    except JobCancelled:
        pass  # the write is done: a cancel now takes nothing back (`run_report` would put the old state over it)
    seconds["total"] = round(time.perf_counter() - t0, 4)
    out = view.report_json(spec, report_id=int(done["id"]), name=str(done["name"]), version=int(done["version"]),
                           saved=True, truncated=False, files=info["files"], seconds=seconds,
                           **{**common, "model": info["model"]})  # fmt: skip
    out["events"] = _events(int(done["id"]))
    return out


# --- the handler ------------------------------------------------------------------------------------------------------


@handler(KIND, TITLE, queue="quick", max_attempts=1, estimate_memory=memory_mb)
def report_job(ctx: JobContext) -> dict[str, Any]:
    from bf_core.jobs.context import JobError

    assert ctx.project_folder is not None
    rid = ctx.params.get("report_id")
    try:
        return run_report(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick, job_id=ctx.id)
    except ApiError as exc:
        raise JobError(exc.code, exc.message, exc.hint, retry=False, events=_events(rid)) from None
