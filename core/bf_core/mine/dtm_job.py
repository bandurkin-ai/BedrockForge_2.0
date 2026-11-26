"""The jobs `pit.surfaces` and `pit.solid` — the DTMs of the shells and the solid of a pit (подшаг 21.12; ТЗ 8.11.4
«Выходы»; ADR 0028 решения 25, 26, 28, 30; docs/progress/e21-api.md §9, §10). Both are quick.

    out = run_surfaces(engine, folder, scenario_id, shells=None, tick=None)    # `POST /pit/scenarios/{id}/surfaces`
    out = run_solid(engine, folder, scenario_id, shell, name=None, tick=None)  # `POST /pit/scenarios/{id}/solid`
    shells = check_surfaces(conn, scenario_id, shells)    # the synchronous checks of the routes (404, 409, 422)
    shell = check_solid(conn, scenario_id, shell)

`params` of the jobs: {scenario_id, shells?, expected_version} and {scenario_id, shell, name?, expected_version}
(`parse_surfaces`, `parse_solid`; 409 `version_conflict` when the scenario has another version than the route saw),
plus the sizes of the plan for `memory_mb` (`limits.hints`).

pit.surfaces. The shells chosen (numbers 1…N; none — all, № 124) → the DTMs: `sidecar:zgrid` of the scenario (the z of
the column of every shell: решение 25), the nodes — the centres of the columns in the axes of the project
(`surfaces.centres`), two triangles a cell, a node without z (outside the plan of the relief) is not built. Every DTM is
a wireframe with `wireframes.dtm`, named «‹сценарий› — RF 0,85», in the folder «Карьер/‹сценарий›»; the scenario is its
input in the graph (so it goes «устарел» after the scenario). A second run replaces the wireframes in place
(`dtm_write`).
Every shell is its own transaction: a cancel keeps the shells that are done (their `events` go with the cancel).

pit.solid. The solid of the pit of one shell (`solid.py`): the body between the relief (the top) and the z-grid of the
shell (the bottom) on one grid of nodes, `solids.to_manifold` + `check_status`; when that is not a closed manifold —
the difference of two `under_solid` (variant А). Neither — 422 `pit_solid_failed` with the reason. The volume of the
solid and Σ V of the mined parents of the shell (the field PIT of the scenario: parents with 1 ≤ PIT ≤ shell) are both
in the answer and `stats` of the wireframe; a difference is shown (`volume_diff_pct`), never an error.

Both write nothing until the geometry is staged; a scenario that is run again meanwhile (another `data`) — 409
`version_conflict` and nothing is written.

Stages: «Чтение», «Оболочки k/N» (surfaces) | «Тело», «Проверка», «Запись» (solid). Results:
  surfaces {scenario_id, folder, wireframes: [{shell, rf, wireframe_id, name, nodes, triangles, replaced}], skipped,
            warnings, events, stages}
  solid    {scenario_id, shell, rf, wireframe_id, name, folder, volume, volume_blocks, volume_diff_pct, parts, closed,
            method, replaced, warnings, events, stages}
`events` — `objects.changed {kind: "wireframe", id}`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks.model import Model
from bf_core.blocks.schema import GridV2, ijk_of
from bf_core.db import dialect
from bf_core.db.schema import objects
from bf_core.geo import solids as gs
from bf_core.jobs.context import JobContext, JobError
from bf_core.jobs.registry import handler
from bf_core.storage.blobs import BlobStore
from bf_core.wireframes import store as wf

from . import dtm_write as dw
from . import fields, limits, solid, spec

if TYPE_CHECKING:  # numba is imported inside the functions (the handler modules do not load it, test_structure)
    from bf_core.geo import dz_kernels
from . import store as st
from . import store_data as sd

KIND_SURFACES = "pit.surfaces"
KIND_SOLID = "pit.solid"
TITLE_SURFACES = "Поверхности оболочек карьера"
TITLE_SOLID = "Каркас карьера"
Tick = Callable[[float | None, str | None], None]


def _no_tick(_f: float | None = None, _m: str | None = None) -> None:
    pass


# --- the scenario ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """What the jobs need of a scenario with a result: its name, version, the parameters of the result, the shells
    (`meta.shells`: k, rf, …), the SHA-256 of `data` (the same file at the write: no re-run meanwhile)."""

    id: int
    name: str
    version: int
    params: Mapping[str, Any]
    shells: Sequence[Mapping[str, Any]]
    data_sha: str | None
    model_id: int | None
    inputs: Sequence[Mapping[str, Any]] = ()  # `meta.inputs` [{kind, id, fp, name}] — the fingerprints at the write

    def rf(self, shell: int) -> float | None:
        rf = self.shells[shell - 1].get("rf")
        return None if rf is None else float(rf)


def _scenario(conn: sa.Connection, scenario_id: int) -> Scenario:
    """The scenario with a result. 404 `pit_scenario_not_found`; 409 `pit_running`, `pit_no_result`."""
    info = st.info(conn, int(scenario_id))
    if info["state"] == "running":
        raise st.running()
    if not info["has_result"]:
        raise st.no_result()
    meta = info["meta"]
    params = meta.get("params") or {}
    return Scenario(
        int(info["id"]),
        str(info["name"]),
        int(info["version"]),
        params,
        list(meta.get("shells") or []),
        st.data_sha(conn, int(scenario_id)),
        params.get("model_id"),
        list(meta.get("inputs") or []),
    )


def _bad(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def check_surfaces(conn: sa.Connection, scenario_id: int, shells: Sequence[int] | None = None) -> list[int]:
    """The shells of a request: the numbers sorted, once each; none — all. 404, 409 `pit_running`, `pit_no_result`;
    422 `bad_param {shells.i}` for a number outside 1…N."""
    sc = _scenario(conn, scenario_id)
    total = len(sc.shells)
    if shells is None:
        return list(range(1, total + 1))
    for i, s in enumerate(shells):
        if isinstance(s, bool) or not isinstance(s, int) or not 1 <= s <= total:
            raise _bad(f"shells.{i}", f"Оболочки «{s}» в сценарии нет: их {total}.",
                       "Выберите номера от 1 до числа оболочек.")  # fmt: skip
    return sorted(set(int(s) for s in shells))


def check_solid(conn: sa.Connection, scenario_id: int, shell: Any) -> int:
    """The number of the shell of a request. 404, 409; 422 `bad_param {shell}`."""
    sc = _scenario(conn, scenario_id)
    total = len(sc.shells)
    if isinstance(shell, bool) or not isinstance(shell, int) or not 1 <= shell <= total:
        raise _bad("shell", f"Оболочки «{shell}» в сценарии нет: их {total}.", "Выберите номер от 1 до числа оболочек.")
    return int(shell)


def _zgrids(
    conn: sa.Connection, store: BlobStore, sc: Scenario, shells: Sequence[int]
) -> tuple[int, int, dict[int, np.ndarray]]:
    """(nx, ny, {shell: z float64 [ny·nx]}) of `sidecar:zgrid` — only the columns of the shells wanted are read.
    409 `pit_scenario_broken` — no file, a shell the file has not or not a full plan."""
    table = st.read_zgrid(conn, store, sc.id, shells)
    if table is None:
        raise st.broken()
    got = sd.table_zgrid(table)
    i, j, z = got["i"], got["j"], got["z"]
    nx, ny = (int(i.max()) + 1, int(j.max()) + 1) if len(i) else (0, 0)
    have = {int(k): a for a, k in enumerate(got["shells"])}
    if (nx * ny != len(i) or not np.array_equal(i + nx * j, np.arange(len(i)))
            or any(int(k) not in have for k in shells)):  # fmt: skip
        raise st.broken()
    return nx, ny, {int(k): z[have[int(k)]] for k in shells}


def _check_version(sc: Scenario, expected: Any) -> None:
    """The job was given the version the route saw: another version — 409 `version_conflict`."""
    if expected is not None and int(expected) != sc.version:
        raise spec.refuse("version_conflict", version=sc.version)


def _check_relief(conn: sa.Connection, sc: Scenario) -> None:
    """The z-grids of the shells were made over the relief of the run; a relief edited since then (its fingerprint is
    not the one `meta.inputs` has) gives a wrong top: 409 `pit_stale`. A relief that is gone is told by `_relief`."""
    from bf_core import lineage

    terrain = sc.params.get("terrain_id")
    if terrain is None:
        return
    seen = next((i for i in sc.inputs if i.get("kind") == "wireframe" and i.get("id") == int(terrain)), None)
    now = lineage.fingerprint(conn, lineage.Node("wireframe", int(terrain)))
    if seen is not None and now is not None and seen.get("fp") != now:
        raise spec.refuse("pit_stale")


def _check_same(conn: sa.Connection, sc: Scenario) -> None:
    """In the write transaction: the scenario was not run again meanwhile (the same `data`) and its relief is not
    edited (409 `version_conflict`, `pit_stale`)."""
    if st.data_sha(conn, sc.id) != sc.data_sha:
        raise spec.refuse("version_conflict", version=int(st.info(conn, sc.id)["version"]))
    _check_relief(conn, sc)


def _crs(conn: sa.Connection, model_id: int | None) -> str | None:
    if model_id is None:
        return None
    return conn.execute(sa.select(objects.c.crs).where(objects.c.id == int(model_id))).scalar()


def _watch(tick: Tick, start: float, stop: float, text: str) -> Callable[[float, str], None]:
    return lambda f, _m: tick(start + (stop - start) * min(max(float(f), 0.0), 1.0), text)


# --- pit.surfaces ----------------------------------------------------------------------------------------------------


def run_surfaces(engine: sa.Engine, folder: Any, scenario_id: int, shells: Sequence[int] | None = None, *,
                 tick: Tick = _no_tick, expected_version: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The body of `pit.surfaces` (the handler and the tests). Errors: `ApiError` (see the module)."""
    from bf_core.jobs.context import JobCancelled

    from . import surfaces

    t0 = time.perf_counter()
    store = BlobStore(Path(folder))
    tick(0.02, "Чтение")
    with dialect.read_tx(engine) as conn:
        want = check_surfaces(conn, scenario_id, shells)
        sc = _scenario(conn, scenario_id)
        _check_version(sc, expected_version)
        _check_relief(conn, sc)  # 409 `pit_stale`
        model = Model.open(conn, store, sc.model_id) if sc.model_id is not None else None
        if model is None:
            raise spec.refuse("block_model_not_found")
        limits.check_columns(model.grid.counts)  # 422 `pit_too_large`: before a z-grid is read
        limits.check_zgrid(int(model.grid.counts[0]) * int(model.grid.counts[1]), len(want))
        nx, ny, z = _zgrids(conn, store, sc, want)
        crs = _crs(conn, sc.model_id)
        grid = model.grid
    if grid.counts[0] != nx or grid.counts[1] != ny:
        raise spec.refuse("model_changed")
    xy = surfaces.centres(grid)
    place = dw.folder_of(sc.name)
    read_s = time.perf_counter() - t0
    out: list[dict[str, Any]] = []
    events: list[list[Any]] = []
    skipped: list[int] = []
    gone: list[dict[str, Any] | None] = []
    try:
        for a, shell in enumerate(want):
            frac = a / max(len(want), 1)
            tick(0.05 + 0.9 * frac, f"Оболочки {a + 1}/{len(want)}")
            xyz, tri = solid.surface_mesh(xy, z[shell], nx, ny)
            if len(tri) == 0:
                skipped.append(shell)  # the relief does not cover the plan: nothing to build (a gap, not a flat 0)
                continue
            rf = sc.rf(shell)
            name = f"{sc.name} — RF {dw.rf_text(rf)}" if rf is not None else f"{sc.name} — оболочка {shell}"
            staged = wf.stage_wireframe(
                store,
                xyz,
                tri,
                progress=_watch(
                    tick, 0.05 + 0.9 * frac, 0.05 + 0.9 * (a + 0.8) / len(want), f"Оболочки {a + 1}/{len(want)}"
                ),
            )
            try:
                with dialect.write_tx(engine) as conn:
                    _check_same(conn, sc)
                    found = dw.outputs_of(conn, sc.id, dw.OP_SURFACE)
                    put = dw.put(conn, store, staged, sc.id, dw.OP_SURFACE, shell, name=name, folder=place,
                                 source={"rf": rf, "version": sc.version, "scenario": sc.name}, found=found, dtm=True,
                                 crs=crs)  # fmt: skip
            finally:
                staged.discard()
            gone.append(put["gone"])
            events.append(["objects.changed", {"kind": "wireframe", "id": put["wireframe_id"]}])
            out.append({"shell": shell, "rf": rf, "wireframe_id": put["wireframe_id"], "name": put["name"],
                        "nodes": len(xyz), "triangles": len(tri), "replaced": put["replaced"]})  # fmt: skip
    except JobCancelled as exc:
        dw.drop_levels(Path(folder), gone)
        exc.events = events  # the shells done stay: the scheduler tells the tree
        raise
    dw.drop_levels(Path(folder), gone)
    if not out:
        raise ApiError(422, "pit_empty", "У выбранных оболочек нет поверхности: рельеф не покрывает план модели.",
                       "Проверьте каркас рельефа в параметрах сценария.")  # fmt: skip
    warnings = [f"Оболочка {s}: рельеф не покрывает план модели, ЦТМ не построена." for s in skipped]
    tick(1.0, "Готово")
    return {"scenario_id": sc.id, "folder": place, "wireframes": out, "skipped": skipped, "warnings": warnings,
            "events": events, "stages": {"read": read_s, "total": time.perf_counter() - t0}}  # fmt: skip


# --- pit.solid -------------------------------------------------------------------------------------------------------


def _relief(conn: sa.Connection, store: BlobStore, terrain_id: Any) -> dz_kernels.SurfaceIndex | None:
    """The index of the relief of the scenario; None — no relief (the top of the grid)."""
    if terrain_id is None:
        return None
    try:
        xyz, tri = wf.read_wireframe(conn, store, int(terrain_id))
    except ApiError as exc:
        if exc.code == "wireframe_not_found":
            raise spec.refuse(
                "wireframe_not_found",
                "Каркас рельефа сценария не найден.",
                "Задайте рельеф в параметрах сценария и запустите расчёт заново.",
            ) from exc
        raise
    from bf_core.geo import dz_kernels

    return dz_kernels.surface_index(xyz, tri)


def mined_volume(model: Model, pit_field: str, shell: int, scenario_id: int, chunk: int = 1 << 20) -> float | None:
    """Σ V of the mined parents of the shell — parents with 1 ≤ PIT ≤ `shell` (every parent once, whatever its
    sub-blocks), by the sizes of the grid. None — the model has no field of that name, or the field was made by another
    scenario (its parents are not those of this one)."""
    found = next((f for f in model.fields if str(f["name"]).casefold() == pit_field.casefold()), None)
    if found is None or not fields.is_own(found, scenario_id):
        return None
    grid: GridV2 = model.grid
    parents: list[np.ndarray] = []
    for ch in model.iter([pit_field], chunk):
        pit = np.asarray(ch.cols[next(iter(ch.cols))])
        take = (pit >= 1) & (pit <= shell)
        if take.any():
            parents.append(np.unique(ch.p[take]))
    if not parents:
        return 0.0
    p = np.unique(np.concatenate(parents))
    i, j, k = ijk_of(p, grid.counts)
    wx, wy, wz = (np.diff(np.asarray(grid.edges(a), dtype=np.float64)) for a in range(3))
    return float(np.sum(wx[i] * wy[j] * wz[k]))


def run_solid(engine: sa.Engine, folder: Any, scenario_id: int, shell: Any, name: str | None = None, *,
              tick: Tick = _no_tick, force_difference: bool = False,
              expected_version: int | None = None) -> dict[str, Any]:  # fmt: skip
    """The body of `pit.solid` (the handler and the tests). `force_difference` — only the fallback (the tests)."""
    from . import surfaces

    t0 = time.perf_counter()
    store = BlobStore(Path(folder))
    tick(0.02, "Чтение")
    with dialect.read_tx(engine) as conn:
        shell = check_solid(conn, scenario_id, shell)
        sc = _scenario(conn, scenario_id)
        _check_version(sc, expected_version)
        _check_relief(conn, sc)  # 409 `pit_stale`: the z-grid of the shell was made over another relief
        if sc.model_id is None:
            raise spec.refuse("block_model_not_found")
        model = Model.open(conn, store, sc.model_id)
        limits.check_columns(model.grid.counts)  # 422 `pit_too_large`
        nx, ny, z = _zgrids(conn, store, sc, [shell])
        relief = _relief(conn, store, sc.params.get("terrain_id"))
        crs = _crs(conn, sc.model_id)
    grid = model.grid
    if grid.counts[0] != nx or grid.counts[1] != ny:
        raise spec.refuse("model_changed")
    xy = surfaces.centres(grid)
    top = surfaces.base_surface(grid, relief)
    bottom = z[shell]
    t_read = time.perf_counter()
    tick(0.15, "Тело")
    warnings: list[str] = []
    try:
        if force_difference:
            built = solid.difference(xy, top, bottom, nx, ny, _watch(tick, 0.15, 0.6, "Тело"))
        else:
            built = solid.build(xy, top, bottom, nx, ny, _watch(tick, 0.15, 0.6, "Тело"))
    except gs.SolidError as exc:
        if exc.code == "empty_mesh":
            raise ApiError(422, "pit_solid_failed", "У оболочки нет объёма: вынутых блоков под рельефом нет.",
                           "Выберите оболочку побольше.") from exc  # fmt: skip
        raise spec.refuse("pit_solid_failed", f"Не удалось построить замкнутый каркас карьера: {exc.message}",
                          "Выберите соседнюю оболочку или постройте поверхности оболочек и возьмите их разность "
                          "(Каркасы → Булевы операции).") from exc  # fmt: skip
    t_solid = time.perf_counter()
    if bool(np.isnan(top).any()):
        warnings.append("Часть плана модели вне рельефа: там карьер не построен.")
    prefix = str(sc.params.get("prefix") or "")
    pit_field = fields.field_names(prefix)[0]
    blocks = mined_volume(model, pit_field, shell, sc.id)
    if blocks is None:
        warnings.append(f"В модели нет поля «{pit_field}» этого сценария: объём вынутых блоков для сверки не найден.")
    diff = None if not blocks else round(abs(built.volume - blocks) / blocks * 100.0, 2)
    tick(0.65, "Проверка")
    staged = wf.stage_wireframe(store, built.xyz, built.tri.astype(np.int32),
                                progress=_watch(tick, 0.65, 0.9, "Проверка"))  # fmt: skip
    try:
        staged.stats.update({"volume_blocks": blocks, "volume_diff_pct": diff, "parts": built.parts,
                             "method": built.method, "shell": shell})  # fmt: skip
        rf = sc.rf(shell)
        final = name or (f"{sc.name} — карьер RF {dw.rf_text(rf, 2)}" if rf is not None
                         else f"{sc.name} — карьер, оболочка {shell}")  # fmt: skip
        tick(0.92, "Запись")
        with dialect.write_tx(engine) as conn:
            _check_same(conn, sc)
            found = dw.outputs_of(conn, sc.id, dw.OP_SOLID)
            put = dw.put(conn, store, staged, sc.id, dw.OP_SOLID, shell, name=final, folder=dw.folder_of(sc.name),
                         source={"rf": rf, "version": sc.version, "scenario": sc.name, "method": built.method,
                                 "volume": built.volume}, found=found, crs=crs)  # fmt: skip
    finally:
        staged.discard()
    dw.drop_levels(Path(folder), [put["gone"]])
    tick(1.0, "Готово")
    t_end = time.perf_counter()
    return {"scenario_id": sc.id, "shell": shell, "rf": rf, "wireframe_id": put["wireframe_id"],
            "name": put["name"], "folder": dw.folder_of(sc.name), "volume": built.volume, "volume_blocks": blocks,
            "volume_diff_pct": diff, "parts": built.parts, "closed": True, "method": built.method,
            "replaced": put["replaced"], "warnings": warnings,
            "events": [["objects.changed", {"kind": "wireframe", "id": put["wireframe_id"]}]],
            "stages": {"read": t_read - t0, "solid": t_solid - t_read, "write": t_end - t_solid, "total": t_end - t0},
            }  # fmt: skip


# --- the handlers ----------------------------------------------------------------------------------------------------


def _memory_mb(params: dict[str, Any]) -> int:
    """1024 MB + the shells and the plan from the sizes the route put into the params (`limits.view_memory_mb`)."""
    return limits.view_memory_mb(params)


def _failure(exc: BaseException) -> JobError:
    if isinstance(exc, ApiError):
        return JobError(exc.code, exc.message, exc.hint, retry=False)
    code, message, hint = limits.OUT_OF_MEMORY  # one text for every pit job
    return JobError(code, message, hint, retry=False)


@handler(KIND_SURFACES, TITLE_SURFACES, queue="quick", max_attempts=1, estimate_memory=_memory_mb)
def surfaces_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    shells = ctx.params.get("shells")
    try:
        return run_surfaces(ctx.engine(), ctx.project_folder, int(ctx.params["scenario_id"]),
                            None if shells is None else [int(s) for s in shells], tick=ctx.tick,
                            expected_version=ctx.params.get("expected_version"))  # fmt: skip
    except (ApiError, MemoryError) as exc:
        raise _failure(exc) from exc


@handler(KIND_SOLID, TITLE_SOLID, queue="quick", max_attempts=1, estimate_memory=_memory_mb)
def solid_job(ctx: JobContext) -> dict[str, Any]:
    assert ctx.project_folder is not None
    try:
        return run_solid(ctx.engine(), ctx.project_folder, int(ctx.params["scenario_id"]), ctx.params.get("shell"),
                         ctx.params.get("name"), tick=ctx.tick,
                         expected_version=ctx.params.get("expected_version"))  # fmt: skip
    except (ApiError, MemoryError) as exc:
        raise _failure(exc) from exc
