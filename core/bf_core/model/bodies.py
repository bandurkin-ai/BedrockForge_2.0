"""The bodies of the domains of an implicit model and their chronology (подшаг 19.13; ТЗ 8.9.2 «хронология», 8.9.3;
ADR 0026 решения 12–15, 18; контракт docs/progress/e19-api.md §11).

    res = build_bodies(spec, points, boundary, *, clip=None, matrix=None, threads=1, tick=None, ...) -> BodiesResult
    res.outputs   list[Output] — for every group of type `domain` its surface and its body, «Прочее» last
    res.fits      {group id: Fit} — patches and solutions (what 19.12 stores in the cache), the field, the figures
    res.journal   the lines of the journal (Russian); res.stats — the time by stage, the balance of the volumes

The pipeline of a group is «mask of the data → solution → body → subtraction» (решение 15):
  1. `mask(group, points, younger)` keeps the data points the group may use (Э19: all of them — the place of the
     stack of Э19б, решения 15 / 19);
  2. `bodies_parts.fit_group`: the patches, η, `solve.solve_patches`, the field `evaluate.PatchField`;
  3. `geo.iso.isosurface` of f = 0 in the box of the boundary (+H beyond the patches and the model), `iso_post.
     postprocess` (V_min, pyfqmr, the checks of 8.9.3 п. 7: closed, manifold, no self-intersections, outward);
  4. `bodies_parts.Tiling`: the body D_i = {f_i < 0} ∩ boundary minus the bodies of the groups that stand before it
     (groups go from the youngest: «сверху молодая»; in Э19 every relation is «Согласно», решение 15) by
     `Manifold.batch_boolean` + `solids.canonical`; «Прочее» = the boundary minus all of them. The volumes add up to the
     volume of the boundary (Т8.9-5; `stats["balance"]` is the relative difference).
Only the groups of type `domain` are made here (shells — 19.14, veins — 19.15, expansion — `expand`); a model without
a domain has no bodies and no «Прочее».

The cut of 8.9.3 п. 8 (`post.clip`): the bodies are always cut by `boundary` (the sum of the volumes needs it);
`wireframe` / `dtm` cut the boundary further — `clip` is the closed solid of the wireframe or the open sheet of the DTM
(the same as in `iso_post.postprocess`).

Cancel: `tick(fraction, text)` is called in the calling thread and may raise (`JobCancelled`); nothing is written
anywhere (the caller of 19.12 writes the result), so a cancel leaves nothing. Errors are `ApiError` {code, message,
hint} in Russian: `rbf_singular` {group, patch, sources, points}, `no_contacts` {group}, `iso_check` {group},
`boundary_empty`, `wireframe_open`, and the errors of `octree` / `solids` with the group named.

manifold3d, pyfqmr and numba are imported by the first stage that needs them, not with this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

from .bodies_parts import EtaOf, Fit, MatricesOf, Solver, Tile, Tiling, fit_group
from .spec_parts import refuse

if TYPE_CHECKING:
    from bf_core.geo.iso_bricks import BrickCache

    from .data import Points
    from .spec import ModelSpec
    from .spec_parts import Group

F8 = NDArray[np.float64]
FieldFn = Callable[[F8], F8]
Tick = Callable[[float, str], None]

NEAR = 1.5  # 8.9.3 п. 2: for a domain the cells with min |f| < 1,5·H join the band
MIN_LEVELS = 1  # `iso_bricks.Grid`: 1–5 (the spec allows 0 — taken as 1, noted in the journal)


@dataclass(frozen=True)
class Output:
    """A surface or a body made for a group; 19.12 writes it as a wireframe, a child of the model (решение 24)."""

    kind: str  # "surface" — the contact f = 0, "body" — a closed solid
    group: int | None  # id of the group; None — «Прочее»
    wing: int | None  # Э19б (fault wings); None here
    threshold: float | None  # shells (19.14); None here
    name: str
    vertices: F8  # float64 [v, 3], project coordinates, canonical order
    triangles: NDArray[np.int32]  # [t, 3]
    volume: float | None  # m³ (a body), None for a surface
    area: float  # m²
    stats: dict[str, Any] = field(default_factory=dict)
    journal: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Solved:
    """A group after its solution: what the groups after it (older) may look at (`mask`, Э19б)."""

    group: Group
    wing: int | None
    fit: Fit
    fn: FieldFn
    points: Points


@dataclass
class BodiesResult:
    outputs: list[Output] = field(default_factory=list)
    fits: dict[int, Fit] = field(default_factory=dict)
    journal: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def mask(group: Group, points: Points, younger: Sequence[Solved]) -> NDArray[np.bool_]:
    """Which data points of `group` take part in its solution. Э19: all (the relation of every group is
    «Согласно»); Э19б (решение 19): `erode` / `onlap` / `intrusion` drop the points inside the bodies of `younger`."""
    return np.ones(points.n, dtype=bool)


def _n(x: float) -> str:
    """A number for the journal: 6 digits, a decimal comma."""
    return f"{x:.6g}".replace(".", ",")


def _sub(tick: Tick | None, a: float, b: float) -> Tick | None:
    """`tick` of a stage that takes the share [a, b] of the whole."""
    if tick is None:
        return None
    outer = tick
    return lambda share, text: outer(a + (b - a) * min(max(float(share), 0.0), 1.0), text)


def _solids_error(err: Exception, group: Group | None = None) -> ApiError:
    """`SolidError` / `IsoError` → ApiError with the group named."""
    code = str(getattr(err, "code", "solid_failed"))
    message = str(getattr(err, "message", str(err)))
    hint = str(getattr(err, "hint", ""))
    extra: dict[str, Any] = {} if group is None else {"group": group.id}
    if group is not None and not message.startswith("Группа"):
        message = f"Группа «{group.name}»: {message}"
    return ApiError(422, code, message, hint, extra)


def _boundary(boundary: tuple[Any, Any]) -> tuple[F8, F8, F8, Any]:
    """The checked boundary: (lo, hi, vertices, triangles). 422 `boundary_empty`, `wireframe_open`."""
    from bf_core.geo import solids

    try:
        v, t = solids.mesh_arrays(boundary, "Граница модели")
    except solids.SolidError as err:
        raise refuse("boundary_empty", f"Граница модели не задана: {err.message}", param="boundary") from None
    if len(t) == 0:
        raise refuse("boundary_empty", param="boundary")
    lo, hi = v.min(axis=0), v.max(axis=0)
    if not bool(np.all(hi > lo)):
        raise refuse("boundary_empty", "Граница модели пуста: у неё нет объёма.", param="boundary")
    ok, reason = solids.is_closed_solid(v, t)
    if not ok:
        raise refuse("wireframe_open", f"Граница модели не замкнута: {reason}.", param="boundary")
    return lo, hi, v, t


def _clip_solid(spec: ModelSpec, boundary: tuple[Any, Any], clip: Any, journal: list[str]) -> tuple[Any, Any]:
    """The solid every body is cut by: the boundary, further cut by the wireframe or the DTM of `post.clip`."""
    from bf_core.geo import clip_sheet, solids

    kind = spec.post.clip
    if kind not in ("wireframe", "dtm"):
        return boundary
    if clip is None:
        raise ApiError(422, "bad_param", "Для обрезки тел не передан каркас или ЦТМ.",
                       "Укажите каркас или ЦТМ обрезки в постобработке модели.", {"param": "post.clip_id"})  # fmt: skip
    bv, bt = solids.mesh_arrays(boundary, "Граница модели")
    if kind == "wireframe":
        solid = solids.mesh_arrays(clip, "Каркас обрезки")
    else:
        sv, st = solids.mesh_arrays(clip, "ЦТМ")
        if len(st) == 0:
            raise solids.empty_error("ЦТМ")
        depth = float(sv[:, 2].max() - min(float(sv[:, 2].min()), float(bv[:, 2].min())))
        solid = clip_sheet.sweep_solid((sv, st), (0.0, 0.0, -1.0), depth * 1.1 + 1.0, name="ЦТМ")
    v, t, report = solids.boolean_many([(bv, bt), solid], "intersection", names=["Граница модели", "Обрезка тел"])
    if report["empty"] or report["flat"]:
        raise refuse("boundary_empty", "После обрезки границы модели ничего не осталось.", param="post.clip_id")
    what = "каркасом" if kind == "wireframe" else "ЦТМ"
    journal.append(f"Обрезка границы {what}: объём {_n(report['volume'])} м³.")
    return solids.canonical(v, t)


def _body_failure(group: Group, why: str) -> ApiError:
    return refuse("iso_check", f"Поверхность группы «{group.name}» не прошла проверки: {why}.",
                  "Увеличьте шаг h или сглаживание η, проверьте данные на противоречия и повторите: каркас не записан.",
                  group=group.id)  # fmt: skip


def _domain_surface(
    spec: ModelSpec, fit: Fit, lo: F8, hi: F8, h: float, levels: int, threads: int, tick: Tick | None,
    brick_cache: BrickCache | None, journal: list[str], timing: dict[str, float],
) -> tuple[Any, Any] | None:  # fmt: skip
    """The closed surface f = 0 of a group, post-processed and checked (None — the group has no body in the box)."""
    from bf_core.geo.iso import isosurface
    from bf_core.geo.iso_post import IsoError, postprocess

    group, pf = fit.group, fit.field

    def fn(p: F8) -> F8:
        return pf(p, threads=threads)

    started = time.perf_counter()
    try:
        mesh = isosurface(fn, lo, hi, h, levels=levels, near=NEAR, threads=threads, tick=_sub(tick, 0.0, 0.75),
                          cache=brick_cache)  # fmt: skip
    except IsoError as err:
        raise _solids_error(err, group) from None
    timing["iso"] = timing.get("iso", 0.0) + time.perf_counter() - started
    if mesh.empty:
        journal.append(f"Группа «{group.name}»: в границе модели поверхности f = 0 нет — тела нет.")
        return None
    started = time.perf_counter()
    try:
        result = postprocess(mesh.vertices, mesh.triangles, h=h, post=replace(spec.post, clip="none"),
                             threads=threads, tick=_sub(tick, 0.75, 1.0))  # fmt: skip
    except IsoError as err:
        if err.code == "iso_check":
            why = err.message.removeprefix("Поверхность не прошла проверку: ").rstrip(".")
            raise _body_failure(group, why) from None
        raise _solids_error(err, group) from None
    timing["post"] = timing.get("post", 0.0) + time.perf_counter() - started
    journal.extend(f"Группа «{group.name}»: {line}" for line in result.journal)
    if result.empty:
        journal.append(f"Группа «{group.name}»: после постобработки поверхность пуста — тела нет.")
        return None
    return result.vertices, result.triangles


def build_bodies(
    spec: ModelSpec,
    points: Points,
    boundary: tuple[Any, Any],
    *,
    clip: Any = None,
    matrix: F8 | None = None,
    matrices_of: MatricesOf | None = None,
    eta_of: EtaOf | None = None,
    solver: Solver | None = None,
    brick_cache: Callable[[Group], BrickCache | None] | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> BodiesResult:
    """The surfaces and bodies of the domains of `spec` (see the module text). `points` — the data of all groups
    (`data.load`); `boundary` — the closed solid of the model (a box: `bodies_parts.box_solid`; a wireframe; a wireframe
    ∩ the body under the DTM, вопрос № 100); `spec.h` is the step (None — by the extent, 8.9.4); `matrix` — the global
    anisotropy A (19.9, None — identity), `matrices_of` — A_j of the patches of a structural trend (`fit_group`);
    `eta_of` — η of the automatic smoothing (19.10); `solver` — the solver of the patches (`solve.solve_patches` by
    default; 19.12 puts the cache of patches there); `brick_cache(group)` — the store of the nodes of the bricks of a
    group. The result is the same bits for any `threads` and any order of the holes that gave the points."""
    from bf_core.geo import solids

    lo, hi, bv, bt = _boundary(boundary)
    if tick is not None:  # the fraction never goes back (the sub-ranges of the stages add rounding noise)
        raw, top = tick, [0.0]

        def tick(share: float, text: str) -> None:
            top[0] = max(top[0], min(float(share), 1.0))
            raw(top[0], text)

    extent6 = (*lo, *hi)
    if spec.h is None:
        spec = spec.resolved(extent6)
    assert spec.h is not None
    h = float(spec.h)
    result = BodiesResult()
    journal = result.journal
    groups = [g for g in spec.groups if g.type == "domain"]
    if not groups:
        return result
    levels = max(int(spec.levels), MIN_LEVELS)
    if spec.levels < MIN_LEVELS:
        journal.append("Число уровней L = 0 взято как 1: наименьший кирпич — 2 × 2 × 2 ячейки.")
    timing: dict[str, float] = {}
    started_all = time.perf_counter()
    if tick is not None:
        tick(0.0, "Тела: подготовка границы")
    cutter = _clip_solid(spec, (bv, bt), clip, journal)
    try:
        tiling = Tiling(cutter)
    except solids.SolidError as err:
        raise _solids_error(err) from None
    n = len(groups)
    span = 0.96 / n
    solved: list[Solved] = []
    for k, group in enumerate(groups):
        if group.id is None:
            raise ApiError(422, "bad_param", f"У группы «{group.name}» нет номера.", "Сохраните модель и повторите.",
                           {"param": "groups"})  # fmt: skip
        a = k * span
        data = points.of_group(group.id)
        keep = mask(group, data, solved)
        if not bool(keep.all()):
            data = data.take(keep)
        fit = fit_group(spec, group, data, extent=(lo, hi), matrix=matrix, matrices_of=matrices_of, eta_of=eta_of,
                        solver=solver, threads=threads, tick=tick, span=(a, a + 0.3 * span))  # fmt: skip
        for key, value in fit.stats["time"].items():
            timing[key] = timing.get(key, 0.0) + value
        result.fits[int(group.id)] = fit
        journal.append(
            f"Группа «{group.name}»: точек {data.n}, участков {fit.stats['patches']}, η = {_n(fit.eta)}, "
            f"наибольшая обусловленность {_n(fit.stats['cond_max'])}, "
            f"наибольшая невязка в данных {_n(fit.stats['max_residual'])}."
        )
        solved.append(Solved(group, None, fit, fit.field, data))
        surface = _domain_surface(spec, fit, lo, hi, h, levels, threads, _sub(tick, a + 0.3 * span, a + 0.9 * span),
                                  None if brick_cache is None else brick_cache(group), journal, timing)  # fmt: skip
        if tick is not None:
            tick(a + 0.9 * span, f"Тела: «{group.name}», вычитание")
        tile = Tile(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), 0.0, 0.0)
        if surface is not None:
            started = time.perf_counter()
            try:
                tile = tiling.add(surface[0], surface[1], group.name)
            except solids.SolidError as err:
                raise _solids_error(err, group) from None
            timing["tiling"] = timing.get("tiling", 0.0) + time.perf_counter() - started
        _emit(result, group, tile, fit, journal)
        if tick is not None:
            tick(a + span, f"Тела: «{group.name}» готово")
    started = time.perf_counter()
    try:
        rest = tiling.rest()
    except solids.SolidError as err:
        raise _solids_error(err) from None
    timing["tiling"] = timing.get("tiling", 0.0) + time.perf_counter() - started
    if not rest.empty:
        result.outputs.append(Output("body", None, None, None, "Прочее", rest.vertices, rest.triangles, rest.volume,
                                     rest.area, {"triangles": len(rest.triangles), "parts": rest.parts}))  # fmt: skip
        journal.append(f"«Прочее»: объём {_n(rest.volume)} м³.")
    balance = abs(tiling.total - tiling.volume) / tiling.volume if tiling.volume else 0.0
    timing["total"] = time.perf_counter() - started_all
    result.stats = {"time": timing, "balance": balance, "boundary_volume": tiling.volume, "groups": n,
                    "levels": levels, "h": h}  # fmt: skip
    if balance > 1e-9:
        journal.append(f"Сумма объёмов тел отличается от объёма границы на {_n(balance)} (доля) — больше 1e-9.")
    if tick is not None:
        tick(1.0, "Тела: готово")
    return result


def _emit(result: BodiesResult, group: Group, tile: Tile, fit: Fit, journal: list[str]) -> None:
    """The surface and the body of a group into the outputs; a group with no body leaves a line of the journal."""
    if tile.empty:
        journal.append(f"Группа «{group.name}»: тело пусто — всё под телами более молодых групп или вне границы.")
        return
    stats = {"fit": dict(fit.stats), "triangles": len(tile.triangles), "parts": tile.parts}
    if tile.sheet is not None:
        sv, st = tile.sheet
        result.outputs.append(Output("surface", int(group.id or 0), None, None, f"{group.name}: контакт", sv, st, None,
                                     tile.sheet_area, {**stats, "triangles": len(st)}))  # fmt: skip
    result.outputs.append(Output("body", int(group.id or 0), None, None, group.name, tile.vertices, tile.triangles,
                                 tile.volume, tile.area, stats))  # fmt: skip
    journal.append(f"Группа «{group.name}»: тело {_n(tile.volume)} м³, контакт {_n(tile.sheet_area)} м².")


__all__ = ["BodiesResult", "Output", "Solved", "build_bodies", "mask"]
