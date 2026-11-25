"""The shells of grades of an implicit model (подшаг 19.14; ТЗ 8.9.2 «оболочки содержаний», 8.9.3, Т8.9-6; ADR 0026
решения 6, 16; контракт docs/progress/e19-api.md §4, §11).

    res = build_shells(spec, group, points, boundary, *, clip=None, matrix=None, eta_of=None, solver=None,
                       brick_cache=None, threads=1, tick=None, surfaces=False) -> ShellsResult
    res.outputs   list[Output] — for every threshold t_k of the group the body «<Группа> ≥ <t_k:g>» (kind "body",
                  `group`, `threshold` = t_k); with `surfaces=True` before it its isosurface (kind "surface")
    res.fits      {group id: Fit} — the fit of the group (numeric: the only one; indicator: that of the first
                  threshold that had data above it); res.fits_by_threshold {(group id, k): Fit} — k is the index of
                  the threshold; one fit per threshold that had data above it (numeric: the same object for all)
    res.journal   the lines of the journal (Russian); res.stats — the time by stage, the volumes of the shells

Called by the job (19.12) for a group of type `shell`, like `bodies.build_bodies` for the domains; the result is the
same `BodiesResult` (a subclass). `points` are the data of the model (`data.load`): the grades of the group are
`points.of_group(group.id)`, `f` is T(g) of a numeric shell (linear, log, nscore) and the grade g of an indicator one,
`levels` the levels of the thresholds in that space. `boundary` is the closed solid of the model (a box:
`bodies_parts.box_solid`), `spec.h` the step (None — by the extent).

The pipeline of a threshold t_k (thresholds ascend, so the shell k is inside the shell k − 1):
  1. the field. Numeric: ONE RBF of T(g) for the whole group (`bodies_parts.fit_group`, s = 0,05 of the grades by
     default), f_k = T(t_k) − RBF. Indicator: an RBF of [g ≥ t_k] − 0,5 of its own for every threshold, f_k = (p − 0,5)
     − RBF, p = 0,5 by default (0,3–0,7). Inside is f < 0, as for the domains; a positive factor 1/σ makes it of the
     order of 1 (`shells_parts`);
  2. `geo.iso.isosurface` of f_k = 0 in the box of the boundary (+H beyond: closed), `iso_post.postprocess`;
  3. `shells_parts.Nesting`: the body of the shell k = S_k ∩ boundary ∩ the body of the shell k − 1 (`Manifold`
     booleans, `solids.canonical`): the shells are nested exactly, Т8.9-6; the volumes decrease.
A threshold with no data above it (numeric: no T(g) ≥ T(t); indicator: no g ≥ t), or whose surface is empty, or whose
shell is empty after the cut, gives no body and a line of the journal, not an error; the thresholds above it are
empty too and are not computed. The refusals are `ApiError` {code, message, hint} in Russian: `no_contacts` {group}
(the group has no data at all), `rbf_singular` {group, patch, sources, points}, `iso_check` {group}, `boundary_empty`,
`wireframe_open`, `bad_param`, and the errors of `octree` / `solids` with the group named. `too_many_points` is raised
by `data.load`.

`brick_cache(group, k)` — the store of the nodes of the bricks of the threshold k (its values depend on the threshold,
so every threshold has its own store; the type is not that of `build_bodies`). `tick(fraction, text)` is called in the
calling thread and may raise (`JobCancelled`): nothing is written anywhere. The result is the same bits for any
`threads` and any order of the holes that gave the points.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

from .bodies import BodiesResult, Output, _boundary, _clip_solid, _n, _solids_error, _sub
from .bodies_parts import EtaOf, Fit, Solver, Tile, fit_group
from .data import indicator
from .shells_parts import Nesting, ThresholdField, shell_levels, shell_surface, unit_of, with_values
from .spec_parts import refuse

if TYPE_CHECKING:
    from bf_core.geo.iso_bricks import BrickCache

    from .data import Points
    from .spec import ModelSpec
    from .spec_parts import Group

F8 = NDArray[np.float64]
Tick = Callable[[float, str], None]

MIN_LEVELS = 1  # `iso_bricks.Grid`: 1–5 (the spec allows 0 — taken as 1, noted in the journal)


@dataclass
class ShellsResult(BodiesResult):
    """`BodiesResult` of one group of shells: the fits of every threshold are in `fits_by_threshold`."""

    fits_by_threshold: dict[tuple[int, int], Fit] = field(default_factory=dict)


def _empty_tile() -> Tile:
    return Tile(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32), 0.0, 0.0)


def label_of(group: Group, threshold: float) -> str:
    """«Группа 2 ≥ 0.5»: the name of the shell of a threshold (the format `:g` of `drillholes.from_model`)."""
    return f"{group.name} ≥ {threshold:g}"


def build_shells(
    spec: ModelSpec,
    group: Group,
    points: Points,
    boundary: tuple[Any, Any],
    *,
    clip: Any = None,
    matrix: F8 | None = None,
    eta_of: EtaOf | None = None,
    solver: Solver | None = None,
    brick_cache: Callable[[Group, int], BrickCache | None] | None = None,
    threads: int = 1,
    tick: Tick | None = None,
    surfaces: bool = False,
) -> ShellsResult:
    """The bodies of the shells of `group` (see the module text); `matrix` — the global anisotropy A (19.9, None —
    identity), `eta_of` — η of the automatic smoothing (19.10), `solver` — the solver of the patches (19.12 puts the
    cache of patches there), `clip` — the wireframe or the DTM of `post.clip`."""
    from bf_core.geo import solids

    if group.type != "shell":
        raise ApiError(422, "bad_param", f"Группа «{group.name}» — не оболочка содержаний.",
                       "Оболочки строятся только для групп вида «Оболочка».", {"param": "groups"})  # fmt: skip
    if group.id is None:
        raise ApiError(422, "bad_param", f"У группы «{group.name}» нет номера.", "Сохраните модель и повторите.",
                       {"param": "groups"})  # fmt: skip
    gid = int(group.id)
    lo, hi, bv, bt = _boundary(boundary)
    if tick is not None:  # the fraction never goes back (the sub-ranges of the stages add rounding noise)
        raw, top = tick, [0.0]

        def tick(share: float, text: str) -> None:
            top[0] = max(top[0], min(float(share), 1.0))
            raw(top[0], text)

    if spec.h is None:
        spec = spec.resolved((*lo, *hi))
    assert spec.h is not None
    h = float(spec.h)
    result = ShellsResult()
    journal = result.journal
    levels = max(int(spec.levels), MIN_LEVELS)
    if spec.levels < MIN_LEVELS:
        journal.append("Число уровней L = 0 взято как 1: наименьший кирпич — 2 × 2 × 2 ячейки.")
    timing: dict[str, float] = {}
    started_all = time.perf_counter()
    if tick is not None:
        tick(0.0, "Оболочки: подготовка границы")
    data = points.of_group(gid)
    if data.n == 0:
        raise refuse("no_contacts", f"В данных группы «{group.name}» нет содержаний: оболочки не определены.",
                     "Проверьте источник содержаний группы (композиты или таблицу проб) и поле содержания.",
                     group=gid)  # fmt: skip
    journal.extend(data.notes)
    thresholds = [float(t) for t in group.params["thresholds"]]
    level_of = shell_levels(group, data)
    is_indicator = group.params["mode"] == "indicator"
    p_level = float(group.params["p"] if group.params.get("p") is not None else 0.5) - 0.5
    cutter = _clip_solid(spec, (bv, bt), clip, journal)
    try:
        nesting = Nesting(cutter)
    except solids.SolidError as err:
        raise _solids_error(err, group) from None
    count = len(thresholds)
    span = 0.96 / count
    numeric_fit: Fit | None = None
    numeric_unit = 1.0
    figures: list[dict[str, Any]] = []
    for k, (t, level) in enumerate(zip(thresholds, level_of, strict=True)):
        a = k * span
        label = label_of(group, t)
        if nesting.dead:  # a lower shell is empty: this one is inside it
            journal.append(f"Оболочка «{label}»: оболочка порога ниже пуста — тела нет.")
            figures.append({"threshold": t, "volume": 0.0, "area": 0.0})
            continue
        above = int(np.count_nonzero(data.f >= level)) if np.isfinite(level) else 0
        if above == 0:
            nesting.dead = True
            journal.append(f"Оболочка «{label}»: точек выше порога нет — тела нет.")
            figures.append({"threshold": t, "volume": 0.0, "area": 0.0})
            continue
        if tick is not None:
            tick(a, f"Оболочки: «{label}»")
        fit_span = (a, a + 0.35 * span)
        fresh: Fit | None = None
        if is_indicator:
            values = indicator(data.f, level)
            fresh = fit = fit_group(spec, group, with_values(data, values), extent=(lo, hi), matrix=matrix,
                                    eta_of=eta_of, solver=solver, threads=threads, tick=tick, span=fit_span,
                                    need_contacts=False)  # fmt: skip
            unit, shift = unit_of(values), p_level
        else:
            if numeric_fit is None:
                fresh = numeric_fit = fit_group(spec, group, data, extent=(lo, hi), matrix=matrix, eta_of=eta_of,
                                                solver=solver, threads=threads, tick=tick, span=fit_span,
                                                need_contacts=False)  # fmt: skip
                numeric_unit = unit_of(data.f)
            fit, unit, shift = numeric_fit, numeric_unit, level
        result.fits_by_threshold[(gid, k)] = fit
        result.fits.setdefault(gid, fit)
        if fresh is not None:  # the one RBF of a numeric shell is paid for once
            for name, value in fresh.stats["time"].items():
                timing[name] = timing.get(name, 0.0) + value
        journal.append(
            f"Оболочка «{label}»: точек {data.n}, выше порога {above}, участков {fit.stats['patches']}, "
            f"η = {_n(fit.eta)}, наибольшая обусловленность {_n(fit.stats['cond_max'])}."
        )
        fn = ThresholdField(fit.field, shift, unit, threads)
        cache = None if brick_cache is None else brick_cache(group, k)
        surface = shell_surface(spec, group, label, fn, lo, hi, h, levels, threads,
                                _sub(tick, a + 0.35 * span, a + 0.9 * span), cache, journal, timing, _sub)  # fmt: skip
        if tick is not None:
            tick(a + 0.9 * span, f"Оболочки: «{label}», пересечение")
        tile = _empty_tile()
        if surface is not None:
            started = time.perf_counter()
            try:
                tile = nesting.add(surface[0], surface[1], label)
            except solids.SolidError as err:
                raise _solids_error(err, group) from None
            timing["tiling"] = timing.get("tiling", 0.0) + time.perf_counter() - started
        else:
            nesting.dead = True
        _emit(result, group, t, label, tile, fit, surfaces)
        figures.append({"threshold": t, "volume": tile.volume, "area": tile.area})
        if tick is not None:
            tick(a + span, f"Оболочки: «{label}» готово")
    timing["total"] = time.perf_counter() - started_all
    volumes = [f["volume"] for f in figures]
    nested = all(volumes[i] <= volumes[i - 1] * (1.0 + 1e-9) for i in range(1, len(volumes)))
    result.stats = {"time": timing, "thresholds": figures, "nested": nested, "boundary_volume": nesting.volume,
                    "levels": levels, "h": h, "mode": "indicator" if is_indicator else "numeric"}  # fmt: skip
    if tick is not None:
        tick(1.0, "Оболочки: готово")
    return result


def _emit(result: ShellsResult, group: Group, t: float, label: str, tile: Tile, fit: Fit, surfaces: bool) -> None:
    """The body (and, asked, the isosurface) of a threshold into the outputs; an empty shell leaves a line."""
    journal = result.journal
    if tile.empty:
        journal.append(f"Оболочка «{label}»: тело пусто — выше порога нет объёма внутри границы и оболочки ниже.")
        return
    gid = int(group.id or 0)
    stats = {"fit": dict(fit.stats), "triangles": len(tile.triangles), "parts": tile.parts, "threshold": t}
    if surfaces and tile.sheet is not None:
        sv, st = tile.sheet
        result.outputs.append(Output("surface", gid, None, t, f"{label}: изоповерхность", sv, st, None,
                                     tile.sheet_area, {**stats, "triangles": len(st)}))  # fmt: skip
    result.outputs.append(Output("body", gid, None, t, label, tile.vertices, tile.triangles, tile.volume, tile.area,
                                 stats))  # fmt: skip
    journal.append(f"Оболочка «{label}»: тело {_n(tile.volume)} м³, поверхность {_n(tile.area)} м².")


__all__ = ["ShellsResult", "build_shells", "label_of"]
