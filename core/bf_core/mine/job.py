"""The jobs `pit.check` and `pit.run` of the pit optimisation (подшаг 21.11; ТЗ 8.11; ADR 0028 decisions 17, 20–23, 28,
30; docs/progress/e21-api.md §4, §6). Образец — `resources/job.py` (Э20).

    out = run_check(engine, folder, {"params": …}, tick)                         # the body of `pit.check`
    out = run_scenario(engine, folder, params, tick, job_id=None, chunk=None)    # the body of `pit.run`

`pit.check` (quick): the template of the angles on the grid of the model and the control of the angle (stages «Шаблон»,
«Контроль угла»): K, the offsets of every domain, the error of the angle, φ_эфф(az). A model and its grid are needed;
the prices and the rocks are not. Nothing is written.

`pit.run` (heavy) — `params` = `{scenario_id, params, name?, expected_version?, solver?, rebuild?}` (the route
`POST /scenarios/{id}/run`, or «Перестроить» of the graph: `store.rebuild_params`, the parameters of the result). The
stages (a message starts with its name): «Чтение» (the model, the parameters against it, the types of rocks the data
meets, the readiness) → «Экономика» (one pass: the table of the nodes) → «Шаблон» → «Граф» (the cones, the dense index)
→ «RF_max» (the solution on the whole graph) → «Оболочки k/N» → «Отчёт» (pit-by-pit, the worst case) → «Запись». The
scenario says «идёт расчёт» (`store.mark_running`) from the first transaction to the last. The write is ONE transaction
(the last thing the job does): the files, `meta`, the fields PIT and ORE of the model, the rows of the graph
(`lineage.record`); a cancel (`JobCancelled` from `tick`, at any stage) or an error before it leaves nothing — the
files made go (`store.stage(...).discard()`, the staged fields), the scenario is as it was (`store.settle`). A model —
or a field, a filter, a surface it read — that changed while the job was calculating: 409 `model_changed`.

The result of the run is that of e21-api §6 plus `events`: `pit.changed {kind: "pit_scenario", id}` and
`objects.changed {kind: "block_model", id}` after a write. The same parameters give the same `data` and the same PIT
bit for bit (8.0.2, decision 16): «Перестроить» is a calculation from nothing. `value` of a shell is q·Σv′ of its
closure (the error is `objective_error`, decision 8). The kernels, numpy and pyarrow are imported inside the functions
(the process of the API registers the handlers and must not load numba).
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

from . import spec as sp_mod

if TYPE_CHECKING:
    from bf_core.jobs.context import JobContext

KIND_RUN = "pit.run"
KIND_CHECK = "pit.check"
TITLE_RUN = "Оптимизация карьера"
TITLE_CHECK = "Проверка шаблона карьера"
EVENT = "pit.changed"
OBJECT = "pit_scenario"
S_READ, S_ECON, S_TPL, S_GRAPH = "Чтение", "Экономика", "Шаблон", "Граф"
S_RFMAX, S_SHELLS, S_REPORT, S_WRITE = "RF_max", "Оболочки", "Отчёт", "Запись"
S_ANGLE = "Контроль угла"
Tick = Callable[[float | None, str | None], None]


def _noop(_p: float | None = None, _m: str | None = None) -> None:
    pass


def memory_mb(_params: dict[str, Any] | None = None) -> int:
    """The pass of the economics (800 MB of a portion) and the solver on the graph of ≈ 1 million nodes."""
    return 1500


def _events(scenario_id: int | None, model_id: int | None = None) -> list[list[Any]]:
    out: list[list[Any]] = []
    if scenario_id is not None:
        out.append([EVENT, {"kind": OBJECT, "id": int(scenario_id)}])
    if model_id is not None:
        out.append(["objects.changed", {"kind": "block_model", "id": int(model_id)}])
    return out


def _scaled(tick: Tick, lo: float, span: float, text: str) -> Tick:
    """A tick of a part of the job: the share of the part → the share of the job, with the text of the stage."""

    def inner(p: float | None = None, _m: str | None = None) -> None:
        tick(None if p is None else lo + span * max(0.0, min(1.0, p)), text)

    return inner


def _versions() -> dict[str, str]:
    from importlib import metadata

    out: dict[str, str] = {}
    for name in ("numpy", "numba", "pyarrow", "ortools"):
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            pass
    return out


# --- pit.check -----------------------------------------------------------------------------------------------


def run_check(engine: sa.Engine, folder: Any, params: dict[str, Any], tick: Tick = _noop) -> dict[str, Any]:
    """The body of `pit.check`: the template and the control of the angle (e21-api §4). 404 `block_model_not_found`;
    422 `bad_param {model_id}`, `variable_grid`."""
    from bf_core.blocks.model import Model
    from bf_core.storage.blobs import BlobStore

    from . import plan

    t0 = time.perf_counter()
    sp = sp_mod.parse_params(params.get("params"))
    if sp.model_id is None:
        raise ApiError(422, "bad_param", "Не выбрана блочная модель.", "Выберите модель, на сетке которой проверять "
                       "шаблон.", {"param": "model_id"})  # fmt: skip
    tick(0.0, f"{S_TPL}: сетка модели")
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, BlobStore(Path(folder)), sp.model_id)
    plan.check_grid(model)
    tpl = plan.build_tpl(model, sp, _scaled(tick, 0.05, 0.75, f"{S_TPL}: смещения"))
    tick(0.85, f"{S_ANGLE}: φ_эфф по азимутам")
    doc = plan.template_doc(tpl)
    tick(1.0, f"{S_ANGLE}: готово")
    return {"model": plan.model_json(model), **doc, "seconds": round(time.perf_counter() - t0, 4)}


# --- the state of the scenario --------------------------------------------------------------------------------------


def _begin(engine: sa.Engine, scenario_id: int, expected: Any, job_id: int | None) -> None:
    """A run starts: the scenario is checked (404, 409 `version_conflict`) and marked «идёт расчёт» (409
    `pit_running`)."""
    from . import store as st

    with dialect.write_tx(engine) as conn:
        row = st._row(conn, scenario_id)  # noqa: SLF001 - 404 `pit_scenario_not_found`
        if expected is not None and int(row.version) != int(expected):
            raise st.conflict(int(row.version))
        st.mark_running(conn, scenario_id, job_id)


def _settle(engine: sa.Engine, scenario_id: int, error: dict[str, Any] | None = None) -> None:
    """The run ended without a result: the scenario is as it was (one deleted meanwhile is nothing to settle)."""
    from . import store as st

    try:
        with dialect.write_tx(engine) as conn:
            st.settle(conn, scenario_id, error)
    except ApiError:
        pass


def _error_of(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, ApiError):
        return {"code": exc.code, "message": exc.message, "hint": exc.hint}
    return {"code": "pit_failed", "message": "Расчёт карьера завершился ошибкой.",
            "hint": "Повторите расчёт; если ошибка остаётся, сообщите разработчикам: приложите журнал."}  # fmt: skip


def _check_model(conn: sa.Connection, store: Any, model: Any) -> None:
    """In the transaction of the write: the model is what the pass read (its version and its files)."""
    from bf_core.blocks.model import Model

    now = Model.open(conn, store, model.id)
    if now.version != model.version or now.shas() != model.shas():
        raise sp_mod.refuse("model_changed")


# --- pit.run -----------------------------------------------------------------------------------------------


def run_scenario(
    engine: sa.Engine, folder: Any, params: dict[str, Any], tick: Tick = _noop, *, job_id: int | None = None,
    chunk: int | None = None, threads: int | None = None,
) -> dict[str, Any]:  # fmt: skip
    """The body of `pit.run` (the handler, the tests and the bench in the process; the module text)."""
    from bf_core.jobs.context import JobCancelled

    sid = int(params["scenario_id"])
    _begin(engine, sid, params.get("expected_version"), job_id)
    try:
        return _run(engine, Path(folder), params, tick, sid, job_id, chunk, threads)
    except JobCancelled as exc:
        _settle(engine, sid)
        exc.events = _events(sid)
        raise
    except BaseException as exc:
        _settle(engine, sid, _error_of(exc))
        raise


def _run(engine: sa.Engine, folder: Path, p: dict[str, Any], tick: Tick, sid: int, job_id: int | None,
         chunk: int | None, threads: int | None) -> dict[str, Any]:  # fmt: skip
    from bf_core.blocks import calc_job
    from bf_core.blocks.filters import schema as fs
    from bf_core.blocks.model import CHUNK, Model
    from bf_core.composites import strips
    from bf_core.jobs.context import JobCancelled
    from bf_core.storage.blobs import BlobStore

    from . import econ_table, fields, plan, report, shells, surfaces
    from . import store as st
    from . import store_data as sd
    from . import store_graph as sg

    chunk = int(chunk or CHUNK)
    threads = int(threads or strips.default_threads())
    store = BlobStore(folder)
    sec: dict[str, float] = {}
    t0 = time.perf_counter()
    tick(0.0, f"{S_READ}: модель и параметры сценария")
    sp0 = sp_mod.parse_params(p.get("params"))
    if sp0.model_id is None:
        sp_mod.require_ready(sp0)  # 422 `pit_not_ready`: the model is the first thing it names
    assert sp0.model_id is not None
    solver = str(p.get("solver") or sp0.solver)
    with dialect.read_tx(engine) as conn:
        model = Model.open(conn, store, sp0.model_id)
        plan.check_grid(model)  # 422 `variable_grid`
        sp = plan.resolve(model, sp0)
        fields.check_names(model.meta, sp.prefix, sid)  # 409 `pit_fields_taken`
        rr = plan.restrictor_of(conn, store, model, sp)
        rm = plan.rock_map(conn, store, model, sp)
        fill = sp.terrain_id is not None
        if sp.rocks.by is None:
            met = [rm.names[0]] if model.rows else []
        else:
            probe = plan.make_inputs(model, sp, rm, plan.rocks_of(sp, rm.names), fill_empty=fill)
            met = plan.met_rock_types(model, probe, rm, rr.restrictor, chunk=chunk,
                                      tick=_scaled(tick, 0.0, 0.03, f"{S_READ}: типы пород"))  # fmt: skip
        ready = sp_mod.validate(sp, plan.context(model, met))
        sp = sp_mod.require_ready(sp, plan.context(model, met))  # 422 `pit_not_ready {missing, param}`
        inp = plan.make_inputs(model, sp, rm, plan.rocks_of(sp, rm.names), fill_empty=fill)
        canon = sp.canonical()
        frac = [] if inp.frac is None else [inp.frac]
        names = list(dict.fromkeys([*sg.read_fields(canon), *frac]))
        prints = sg.input_prints(conn, canon, names)
        used = names + (fs.fields_of(fs.normalize_rules(rr.tree)) if rr.tree is not None else [])
        input_shas = calc_job.inputs_of(conn, model, used, rr.tree, None)
        sec["read"] = round(time.perf_counter() - t0, 4)

        t1 = time.perf_counter()
        tick(0.03, f"{S_ECON}: проход по блокам")
        tab = econ_table.build_table(model, inp, rr.restrictor, chunk=chunk, threads=threads,
                                     tick=_scaled(tick, 0.03, 0.22, f"{S_ECON}: проход по блокам"))  # fmt: skip
        sec["econ"] = round(time.perf_counter() - t1, 4)

        t1 = time.perf_counter()
        tick(0.25, f"{S_TPL}: углы бортов")
        tpl = plan.build_tpl(model, sp, _scaled(tick, 0.25, 0.02, f"{S_TPL}: углы бортов"))
        doc = plan.template_doc(tpl)
        sec["template"] = round(time.perf_counter() - t1, 4)

        t1 = time.perf_counter()
        tick(0.27, f"{S_GRAPH}: узлы и дуги")
        graph = plan.make_graph(model, tab, tpl, sp.slopes.domain_field, chunk)
        sec["graph"] = round(time.perf_counter() - t1, 4)

        def on_shell(done: int, total: int, text: str) -> None:
            label = f"{S_RFMAX}: решение на полном графе" if done == 0 else text
            tick(0.30 + 0.52 * (done / total if total else 0.0), label)

        t1 = time.perf_counter()
        res = shells.shells(graph, tab, sp.rf, solver=solver, tick=on_shell, threads=threads)
        spent = time.perf_counter() - t1
        first = res.stats["solves"][0] if res.stats.get("solves") else {"seconds": 0.0, "nodes": graph.n}
        rf_max = float(res.stats["seconds"].get("cones", 0.0)) + float(first["seconds"])
        sec["rf_max"], sec["shells"] = round(rf_max, 4), round(max(spent - rf_max, 0.0), 4)

        t1 = time.perf_counter()
        tick(0.82, f"{S_REPORT}: pit-by-pit")
        rep = report.build_report(model, inp, rr.restrictor, tab, res.pit, res.rf, chunk=chunk, threads=threads,
                                  tick=_scaled(tick, 0.82, 0.1, f"{S_REPORT}: pit-by-pit"))  # fmt: skip
        sec["report"] = round(time.perf_counter() - t1, 4)

        tick(0.93, f"{S_WRITE}: поля PIT и ORE")
        smap = fields.ShellMap.from_graph(graph, res.pit)
        staged_fields = fields.stage_fields(store, model, model.version, sp.prefix, sid, smap, tab.node_of_row,
                                            tab.ore_row, chunk=chunk)  # fmt: skip
    staged = None
    try:
        zg = surfaces.z_grids(model.grid, smap, rr.relief, n_shells=res.n_shells)
        nx = int(model.grid.counts[0])
        cols = list(range(nx * int(model.grid.counts[1])))
        data = sd.data_table(rep.data, rep.elements)
        staged = st.stage(store, data, sd.benches_table(rep.benches),
                          sd.zgrid_table([c % nx for c in cols], [c // nx for c in cols], zg.z), doc,
                          plan.control_doc(tab, model, closed=True, nested=True))  # fmt: skip
        pit_name, ore_name = fields.field_names(sp.prefix)
        meta_shells = [{"k": k + 1, "rf": float(res.rf[k]), "blocks": int(res.blocks[k]),
                        "ore_t": float(rep.data["cum_ore_t"][k]), "waste_t": float(rep.data["cum_waste_t"][k]),
                        "value": float(res.objective[k]) * float(res.q)} for k in range(res.n_shells)]  # fmt: skip
        totals = rep.totals()
        warnings = plan.merge_warnings(
            doc["warnings"],
            ready["warnings"],
            plan.warning_dicts(rr.restrictor.warnings, "restrict", "Проверьте ограничитель сценария."),
            plan.warning_dicts(tab.control.warnings(), "control", "Подробности — в «Контроле» результата."),
        )
        sec["total"] = round(time.perf_counter() - t0, 4)
        grid_meta = dict(model.meta.get("grid") or {})
        new_version: list[int] = []

        def on_write(conn: sa.Connection, ident: int) -> None:
            """In the transaction of the write: the fields PIT and ORE into the model, their rows of the graph (the
            scenario is the input of both: «устарел» goes through it)."""
            from bf_core import lineage

            try:
                new_version.append(fields.commit_scenario_fields(conn, store, staged_fields, scenario_id=ident,
                                                                 inputs=input_shas, versions=_versions(),
                                                                 params=canon))  # fmt: skip
            except ApiError as exc:
                if exc.code == "version_conflict":
                    raise sp_mod.refuse("model_changed") from None
                raise
            for name in (pit_name, ore_name):
                lineage.record(conn, lineage.Node("bm_field", model.id, name.casefold()),
                               [lineage.Node(st.NODE, ident)])  # fmt: skip

        built = st.Built(canon, {"id": model.id, "name": model.name, "grid": grid_meta}, names, staged, meta_shells,
                         totals, None, float(res.q), float(res.error),
                         {"fields": [pit_name, ore_name], "wireframes": []}, res.solver, dict(sec), _versions(),
                         warnings, job_id, prints, on_write)  # fmt: skip
        tick(0.97, f"{S_WRITE}: запись в проект")
        t3 = time.perf_counter()
        with dialect.write_tx(engine) as conn:
            _check_model(conn, store, model)
            done = st.write_result(conn, store, sid, built, p.get("expected_version"))
            info = st.info(conn, sid)
        sec["write"] = round(time.perf_counter() - t3, 4)
    finally:
        staged_fields.discard()
        if staged is not None:
            staged.discard()
    try:
        tick(1.0, None)
    except JobCancelled:
        pass  # the write is done: a cancel now takes nothing back (`run_scenario` would put the old state over it)
    sec["total"] = round(time.perf_counter() - t0, 4)
    meta = info["meta"]
    mjson = {**plan.model_json(model), "version": new_version[0] if new_version else model.version,
             "cells_sha": (meta.get("model") or {}).get("cells_sha")}  # fmt: skip
    out: dict[str, Any] = {
        "scenario_id": sid, "name": done["name"], "version": int(done["version"]), "algo": sp_mod.ALGO,
        "params_hash": st.hash_of(canon), "solver": res.solver, "params": canon, "model": mjson,
        "template": st.template_summary(doc), "q": float(res.q), "objective_error": float(res.error),
        "nodes": {"model": int(model.rows), "air": int(tab.control.to_dict()["air"]), "graph": int(graph.n),
                  "cones": int(first["nodes"])},  # fmt: skip
        "shells": meta_shells, "totals": totals, "outputs": {"fields": [pit_name, ore_name], "wireframes": []},
        "control": meta.get("control"), "warnings": warnings, "seconds": sec,
        "events": _events(sid, model.id),
    }  # fmt: skip
    return sd.clean_json(out)  # type: ignore[no-any-return]


# --- the handlers -----------------------------------------------------------------------------------------------


def _job_error(exc: ApiError, events: list[list[Any]] | None = None) -> Any:
    from bf_core.jobs.context import JobError

    return JobError(exc.code, exc.message, exc.hint, retry=False, events=events)


@handler(KIND_RUN, TITLE_RUN, queue="heavy", max_attempts=1, estimate_memory=memory_mb)
def run_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    sid = ctx.params.get("scenario_id")
    try:
        return run_scenario(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick, job_id=ctx.id)
    except ApiError as exc:
        raise _job_error(exc, _events(None if sid is None else int(sid))) from None


@handler(KIND_CHECK, TITLE_CHECK, queue="quick", max_attempts=1)
def check_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_check(ctx.engine(), ctx.project_folder, ctx.params, ctx.tick)
    except ApiError as exc:
        raise _job_error(exc) from None


__all__ = ["KIND_CHECK", "KIND_RUN", "check_job", "run_check", "run_job", "run_scenario"]
