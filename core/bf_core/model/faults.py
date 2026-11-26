"""The faults of an implicit model: the wings of the groups they cut (подшаг 19.33; ТЗ Д-24 «Разлом»; ADR 0026
решения 15, 20; контракт docs/progress/e19-api.md §4, §10).

    res = build_with_faults(spec, points, boundary, *, relations=None, clip=None, matrix=None, matrices_of=None,
                            eta_of=None, solver=None, brick_cache=None, threads=1, tick=None) -> FaultsResult

The same arguments and the same `BodiesResult` as `stack.build_stack` (a model without a fault that cuts a domain gives
its bits: the call is delegated), plus `FaultsResult.faults` {fault id: Fit}, `.fields` {group id: FaultedField} and
`.wing_fits`. The order of the work is the one of the ТЗ: the faults first, then the domains by the stack of relations.

A fault is a group of type `fault` (its data are the data of a domain: contacts, ±D points, lines) with its own RBF f_F.
Its wings are the body {f_F < 0} of 8.9.3 (wing 0) and the rest of the boundary (wing 1): two closed solids that do not
meet by construction (`WingTiling.split`). A domain whose `faults` hold it is cut by them: its data are divided by the
sign of f_F (a datum with |f_F| ≤ 1e-9·h — or where f_F is not defined — is on both wings) and every wing has its own
field, a PoU of its own points (`bodies_parts.fit_group`, the same patches and solver). The body of the wing k is
{g_k < 0} ∩ the region of the wing, minus the bodies of the groups before it in the region: the contact is the surface
of the wing field, and the boundary of the wing is the surface of the fault. Several faults: the regions are the
combinations of the signs, in the order of the faults of the model; a group cut by one fault of two is divided by that
one only. The wing code of an output is Σ sign_k · 2^k over the faults of the group (one fault: 0 for f_F < 0, 1 else).

A finite fault (`radius` R_F): g_k = w·f_k + (1 − w)·f_0, w = W(r / R_F) = (1 − t)⁴₊(4t + 1), r the distance to the
nearest datum of the fault on its surface (the points with f = 0), f_0 the field of the data that are not divided. At
the data of a wing g_k = f_k = f_0 (both interpolate them), and beyond R_F the wing fields are f_0 itself, bit for bit
(Т8.9-12): the surface goes through the fault unbroken. With several finite faults the blend is multilinear (the module
`faults_parts`). The offset of the wings — «результат разности полей» (Д-24) — is measured and returned, never set:
`stats["by_group"][i]["offset"] = {mean, max}` in metres along the normal of the contact.

The outputs. For every fault: its `surface` (the sheet of the fault inside the boundary) and the two `body` regions
(`wing` 0 / 1, `stats["region"] = True`: they are the space the wings take, not rock — do not add them to the bodies of
the groups). For every domain: per wing its `surface` and `body` (`wing` = the code); a domain that no fault cuts has
the plain outputs (`wing` None). «Прочее» per region of the faults used. The volumes of the domain bodies and of
«Прочее» add up to the volume of the boundary (`stats["balance"]`, 1e-9), and so do the two wings of a fault
(`stats["faults"][id]["balance"]`).

Errors {code, message, hint} in Russian: `no_contacts` {group, wing, fault} for a wing with no change of sign among its
data (a fault next to a contact), and for a fault without contacts; `fault_outside` {fault} — the surface of the fault
does not cut the boundary, a wing is empty; the errors of `bodies` (`rbf_singular` with the wing…). A cancel (`tick`
raising) leaves nothing: the caller writes the result. The same bytes for any `threads` and any order of the holes.

manifold3d, pyfqmr, scipy and numba are imported by the stages that need them, not with this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

from .bodies import (
    BodiesResult,
    Output,
    Solved,
    _boundary,
    _clip_solid,
    _domain_surface,
    _n,
    _solids_error,
    _sub,
)
from .bodies import mask as no_mask
from .bodies_parts import Fit, Tile, fit_group
from .faults_parts import (
    TOL_REL,
    FaultedField,
    FaultFit,
    Key,
    RKey,
    WingField,
    WingTiling,
    offsets,
    sample_rows,
    wing_code,
    wing_signs,
)
from .spec_parts import refuse
from .stack import StackPlan, build_stack, domains_of

if TYPE_CHECKING:
    from bf_core.geo.iso_bricks import BrickCache

    from .bodies_parts import EtaOf, MatricesOf, Solver
    from .data import Points
    from .spec import ModelSpec
    from .spec_parts import Group

F8 = NDArray[np.float64]
Tick = Callable[[float, str], None]
D_MAX_DEFAULT = 30.0
FAULTS_MAX = 8  # the wings of a group are the combinations of the signs: 2^k regions


@dataclass
class FaultsResult(BodiesResult):
    """`BodiesResult` of a model with faults; `fits` of a cut group is a composite (the first fit of its wings and the
    figures of all of them, `field` — the `FaultedField`)."""

    faults: dict[int, Fit] = dc_field(default_factory=dict)  # f_F of the faults used, by fault id
    fields: dict[int, FaultedField] = dc_field(default_factory=dict)  # the field of every group cut by faults
    wing_fits: dict[tuple[int, int, Key], Fit] = dc_field(default_factory=dict)  # (group, mask of finite faults, signs)


def _bad(code: str, message: str, hint: str, **extra: Any) -> ApiError:
    return ApiError(422, code, message, hint, extra or None)


def used_faults(spec: ModelSpec, groups: tuple[Group, ...]) -> list[Group]:
    """The groups of type `fault` that cut a domain of `groups`, in the order of the list of the model. 422 `bad_param`
    for a fault that is not in the model."""
    known = {g.id: g for g in spec.groups if g.type == "fault"}
    for g in groups:
        for fid in g.faults:
            if fid not in known:
                raise _bad("bad_param", f"Группа «{g.name}» рассечена несуществующим разломом.",
                           "Обновите вкладку модели и выберите разломы заново.", param="faults",
                           group=g.id)  # fmt: skip
    wanted = {fid for g in groups for fid in g.faults}
    used = [g for g in spec.groups if g.type == "fault" and g.id in wanted]
    if len(used) > FAULTS_MAX:
        raise _bad("bad_param", f"Группы рассечены {len(used)} разломами: больше {FAULTS_MAX}.",
                   "Оставьте у групп не больше восьми разломов: крыльев становится вдвое больше с каждым.",
                   param="faults")  # fmt: skip
    return used


class _Build:
    """The context of one run (the arguments of `build_with_faults` and what the stages share)."""

    def __init__(
        self, spec: ModelSpec, lo: F8, hi: F8, tiling: WingTiling, result: FaultsResult, points: Points,
        matrix: F8 | None, matrices_of: MatricesOf | None, eta_of: EtaOf | None, solver: Solver | None,
        threads: int, tick: Tick | None,
    ) -> None:  # fmt: skip
        assert spec.h is not None
        self.spec, self.lo, self.hi, self.tiling, self.res, self.points = spec, lo, hi, tiling, result, points
        self.matrix, self.matrices_of, self.eta_of, self.solver = matrix, matrices_of, eta_of, solver
        self.threads, self.tick = threads, tick
        self.h = float(spec.h)
        self.levels = max(int(spec.levels), 1)
        self.tol = TOL_REL * self.h
        self.journal = result.journal
        self.timing: dict[str, float] = {}
        self.fault_fits: dict[int, FaultFit] = {}
        self.fault_order: list[int] = []
        self.sheets: dict[int, F8] = {}

    def fit(self, group: Group, data: Points, span: tuple[float, float]) -> Fit:
        fit = fit_group(self.spec, group, data, extent=(self.lo, self.hi), matrix=self.matrix,
                        matrices_of=self.matrices_of if group.type == "domain" else None, eta_of=self.eta_of,
                        solver=self.solver, threads=self.threads, tick=self.tick, span=span)  # fmt: skip
        for key, value in fit.stats["time"].items():
            self.timing[key] = self.timing.get(key, 0.0) + value
        return fit

    def surface(self, fit: Fit, box: tuple[F8, F8], cache: BrickCache | None, span: tuple[float, float]) -> Any:
        return _domain_surface(self.spec, fit, box[0], box[1], self.h, self.levels, self.threads,
                               _sub(self.tick, *span), cache, self.journal, self.timing)  # fmt: skip

    # --- the faults ---------------------------------------------------------------------------------------------

    def fault(self, group: Group, span: tuple[float, float]) -> None:
        fid = int(group.id or 0)
        data = self.points.of_group(fid)
        if data.n == 0 or not (bool((data.f < 0.0).any()) and bool((data.f > 0.0).any())):
            raise refuse("no_contacts", f"В данных разлома «{group.name}» нет контактов: поверхность разлома не "
                         "определена.", "Проверьте коды разлома или добавьте ручные точки и линии по обе стороны его "
                         "поверхности.", group=fid, fault=fid)  # fmt: skip
        a, b = span
        fit = self.fit(group, data, (a, a + 0.5 * (b - a)))
        self.res.faults[fid] = fit
        self.journal.append(
            f"Разлом «{group.name}»: точек {data.n}, участков {fit.stats['patches']}, η = {_n(fit.eta)}, "
            f"наибольшая невязка в данных {_n(fit.stats['max_residual'])}, радиус влияния "
            + ("бесконечный." if group.params.get("radius") is None else f"{_n(float(group.params['radius']))} м.")
        )
        surface = self.surface(fit, (self.lo, self.hi), None, (a + 0.5 * (b - a), b))
        outside = _bad("fault_outside", f"Поверхность разлома «{group.name}» не рассекает границу модели: одно из "
                       "крыльев пусто.", "Проверьте данные разлома или уберите разлом у групп.", fault=fid)  # fmt: skip
        if surface is None:
            raise outside
        try:
            wing0, wing1 = self.tiling.split(fid, surface[0], surface[1], group.name)
        except Exception as err:
            if getattr(err, "code", None) is None:
                raise
            raise _solids_error(err, group) from None
        if wing0.empty or wing1.empty:
            raise outside
        contacts = data.xyz[data.f == 0.0]
        self.fault_fits[fid] = FaultFit(group, fit, contacts if len(contacts) else data.xyz)
        self.fault_order.append(fid)
        if wing0.sheet is not None:
            sv, st = wing0.sheet
            self.sheets[fid] = sv[sample_rows(len(sv))]
            info = {"fit": dict(fit.stats), "triangles": len(st)}
            self.res.outputs.append(Output("surface", fid, None, None, f"{group.name}: контакт", sv, st, None,
                                           wing0.sheet_area, info))
        for code, tile in ((0, wing0), (1, wing1)):
            self.res.outputs.append(Output("body", fid, code, None, f"{group.name}: крыло {code}", tile.vertices,
                                           tile.triangles, tile.volume, tile.area,
                                           {"triangles": len(tile.triangles), "region": True}))  # fmt: skip
        balance = abs(wing0.volume + wing1.volume - self.tiling.volume) / self.tiling.volume
        self.res.stats.setdefault("faults", {})[fid] = {
            "radius": fit.group.params.get("radius"), "points": data.n, "wing_volumes": [wing0.volume, wing1.volume],
            "balance": balance, "max_residual": fit.stats["max_residual"],
        }  # fmt: skip
        self.journal.append(f"Разлом «{group.name}»: крыло 0 — {_n(wing0.volume)} м³, крыло 1 — {_n(wing1.volume)} м³.")

    # --- the groups ---------------------------------------------------------------------------------------------

    def emit(self, group: Group, wing: int | None, tile: Tile, stats: dict[str, Any]) -> None:
        label = group.name if wing is None else f"{group.name}, крыло {wing}"
        gid = int(group.id or 0)
        if tile.empty:
            self.journal.append(f"«{label}»: тело пусто — всё под телами более молодых групп или вне границы.")
            return
        info = {**stats, "triangles": len(tile.triangles), "parts": tile.parts}
        if tile.sheet is not None:
            sv, st = tile.sheet
            self.res.outputs.append(Output("surface", gid, wing, None, f"{label}: контакт", sv, st, None,
                                           tile.sheet_area, {**info, "triangles": len(st)}))  # fmt: skip
        self.res.outputs.append(Output("body", gid, wing, None, label, tile.vertices, tile.triangles, tile.volume,
                                       tile.area, info))  # fmt: skip
        self.journal.append(f"«{label}»: тело {_n(tile.volume)} м³, контакт {_n(tile.sheet_area)} м².")

    def piece(self, group: Group, rkey: RKey, wing: int | None, fit: Fit, span: tuple[float, float],
              cache: BrickCache | None) -> None:  # fmt: skip
        """The surface of `fit` in the box of the region and the body of the group there."""
        box = (self.lo, self.hi) if not rkey else self.tiling.box(rkey)
        if box is None:
            return
        label = group.name if wing is None else f"{group.name}, крыло {wing}"
        a, b = span
        surface = self.surface(fit, box, cache, (a, a + 0.85 * (b - a)))
        if surface is None:
            self.journal.append(f"«{label}»: в границе модели поверхности f = 0 нет — тела нет.")
            return
        started = time.perf_counter()
        try:
            tile = self.tiling.piece(rkey, surface[0], surface[1], label)
        except Exception as err:
            if getattr(err, "code", None) is None:
                raise
            raise _solids_error(err, group) from None
        self.timing["tiling"] = self.timing.get("tiling", 0.0) + time.perf_counter() - started
        self.emit(group, wing, tile, {"fit": dict(fit.stats)})
        if self.tick is not None:
            self.tick(b, f"Тела: «{label}» готово")

    def no_contacts(self, group: Group, fl: list[FaultFit], sides: Key, why: str) -> ApiError:
        side = {0: "f < 0", 1: "f ≥ 0"}
        where = ", ".join(f"«{f.group.name}» ({side[s]})" for f, s in zip(fl, sides, strict=True) if s >= 0)
        return refuse("no_contacts", f"В данных группы «{group.name}» на крыле разлома {where} {why}: поверхность "
                      "крыла не определена.", "Разлом проходит рядом с контактами группы или за ними. Добавьте данные "
                      "группы на этом крыле, сдвиньте разлом или уберите его у группы.", group=group.id,
                      wing=wing_code([max(s, 0) for s in sides]), faults=[f.gid for f in fl])  # fmt: skip

    def cut_group(self, group: Group, data: Points, span: tuple[float, float]) -> tuple[Fit, FaultedField]:
        """A group cut by faults: the fits of its wings, the body of every wing; → the composite fit and field."""
        fl = [self.fault_fits[i] for i in self.fault_order if i in group.faults]
        m = len(fl)
        fin = [k for k in range(m) if fl[k].finite]
        sub = [k for k in range(m) if not fl[k].finite]
        signs, undefined = wing_signs(fl, data.xyz, self.tol, self.threads)
        if undefined:
            self.journal.append(f"Группа «{group.name}»: поле разлома не определено в точках: {undefined} "
                                "(взяты на оба крыла).")  # fmt: skip
        fits: dict[tuple[int, Key], Fit] = {}
        a, b = span
        regions = 1 << m
        beat = (b - a) / regions

        def sub_fit(mask: int, key: Key, share: tuple[float, float]) -> Fit:
            divided = {k for j, k in enumerate(fin) if (mask >> j) & 1}
            kkey: Key = tuple(key[k] if (k in sub or k in divided) else -1 for k in range(m))
            if (mask, kkey) not in fits:
                rows = np.ones(data.n, dtype=bool)
                for k, s in enumerate(kkey):
                    if s >= 0:
                        rows &= (signs[:, k] == s) | (signs[:, k] == 2)
                part = data.take(rows)
                if part.n == 0 or not (bool((part.f < 0.0).any()) and bool((part.f > 0.0).any())):
                    why = "нет данных" if part.n == 0 else "нет контактов"
                    raise self.no_contacts(group, fl, kkey, why)
                fits[(mask, kkey)] = self.fit(group, part, share)
                self.res.wing_fits[(int(group.id or 0), mask, kkey)] = fits[(mask, kkey)]
            return fits[(mask, kkey)]

        wings: dict[Key, WingField] = {}
        for code in range(regions):
            key: Key = tuple((code >> k) & 1 for k in range(m))
            rkey: RKey = tuple((fl[k].gid, key[k]) for k in range(m))
            lo = a + code * beat
            if self.tiling.region(rkey) is None:
                self.journal.append(f"Группа «{group.name}»: крыло {code} разлома пусто в границе модели — пропущено.")
                continue
            masks = range(1 << len(fin))
            terms = []
            for i, mask in enumerate(masks):
                share = (lo + 0.3 * beat * i / len(masks), lo + 0.3 * beat * (i + 1) / len(masks))
                terms.append((mask, sub_fit(mask, key, share)))
            wing = WingField(fl, key, [(mk, f.field) for mk, f in terms])
            wings[key] = wing
            wing_any: Any = wing
            rfit = replace(terms[-1][1], field=wing_any)
            last = terms[-1][1].stats
            self.journal.append(f"Группа «{group.name}»: крыло {code}: точек {last['points']}, участков "
                                f"{last['patches']}, наибольшая невязка в данных {_n(last['max_residual'])}.")
            self.piece(group, rkey, code, rfit, (lo + 0.3 * beat, lo + beat), None)
        field = FaultedField(fl, wings)
        every = list(fits.values())
        first = every[0]
        stats: dict[str, Any] = {
            "points": data.n, "patches": sum(int(f.stats["patches"]) for f in every),
            "eta": max(f.eta for f in every), "h_bar": first.h_bar,
            "cond_max": max(float(f.stats["cond_max"]) for f in every),
            "max_residual": max(float(f.stats["max_residual"]) for f in every),
            "time": {k: sum(f.stats["time"][k] for f in every) for k in first.stats["time"]},
            "wings": sorted(wing_code(k) for k in wings), "faults": [f.gid for f in fl],
        }  # fmt: skip
        field_any: Any = field  # the composite stands where a `PatchField` does: it is called the same way
        composite = Fit(group, first.patches, first.solutions, field_any, float(stats["eta"]), first.h_bar, stats)
        return composite, field


def build_with_faults(
    spec: ModelSpec,
    points: Points,
    boundary: tuple[Any, Any],
    *,
    relations: Mapping[int, str] | None = None,
    clip: Any = None,
    matrix: F8 | None = None,
    matrices_of: MatricesOf | None = None,
    eta_of: EtaOf | None = None,
    solver: Solver | None = None,
    brick_cache: Callable[[Group], BrickCache | None] | None = None,
    threads: int = 1,
    tick: Tick | None = None,
) -> FaultsResult:
    """The surfaces and bodies of the domains of `spec` with the stack of relations and the faults (the module text).
    `points` — the data of all groups (`data.load`) including those of the faults; the arguments are those of
    `stack.build_stack`. `brick_cache(group)` serves the groups no fault cuts only (a wing has its own field)."""
    from bf_core.geo import solids

    groups = domains_of(spec, relations)
    used = used_faults(spec, groups)
    if not used:
        base = build_stack(spec, points, boundary, relations=relations, clip=clip, matrix=matrix,
                           matrices_of=matrices_of, eta_of=eta_of, solver=solver, brick_cache=brick_cache,
                           threads=threads, tick=tick)  # fmt: skip
        return FaultsResult(base.outputs, base.fits, base.journal, base.stats)
    lo, hi, bv, bt = _boundary(boundary)
    if tick is not None:  # the fraction never goes back
        raw, top = tick, [0.0]

        def tick(share: float, text: str) -> None:
            top[0] = max(top[0], min(float(share), 1.0))
            raw(top[0], text)

    if spec.h is None:
        spec = spec.resolved((*lo, *hi))
    result = FaultsResult()
    journal = result.journal
    started_all = time.perf_counter()
    if tick is not None:
        tick(0.0, "Разломы: подготовка границы")
    cutter = _clip_solid(spec, (bv, bt), clip, journal)
    try:
        tiling = WingTiling(cutter)
    except solids.SolidError as err:
        raise _solids_error(err) from None
    plan = StackPlan(groups, threads)
    ctx = _Build(spec, lo, hi, tiling, result, points, matrix, matrices_of, eta_of, solver, threads, tick)
    names = ", ".join(f"«{g.name}»" for g in used)
    journal.append(f"Разломы: {names}; порядок — разломы, затем группы по стеку отношений.")
    head = 0.12
    for k, fault in enumerate(used):
        ctx.fault(fault, (head * k / len(used), head * (k + 1) / len(used)))
    n = len(groups)
    span = (0.94 - head) / n
    by_id = {g.id: g for g in groups}
    solved: list[Solved] = []
    by_group: dict[int, dict[str, Any]] = {}
    mask_fn = no_mask if plan.plain else plan.mask
    for k, gid in enumerate(plan.order):
        group = by_id[gid]
        a = head + k * span
        data = points.of_group(gid)
        started = time.perf_counter()
        keep = mask_fn(group, data, solved, journal)
        ctx.timing["mask"] = ctx.timing.get("mask", 0.0) + time.perf_counter() - started
        if not bool(keep.all()):
            data = data.take(keep)
        if group.faults:
            fit, field = ctx.cut_group(group, data, (a, a + span))
            result.fields[gid] = field
            by_group[gid] = {"group": gid, "name": group.name, "faults": list(field_faults(field)),
                             "wings": fit.stats["wings"], "points": data.n, "offset": None}  # fmt: skip
        else:
            fit = ctx.fit(group, data, (a, a + 0.3 * span))
            journal.append(
                f"Группа «{group.name}»: точек {data.n}, участков {fit.stats['patches']}, η = {_n(fit.eta)}, "
                f"наибольшая обусловленность {_n(fit.stats['cond_max'])}, "
                f"наибольшая невязка в данных {_n(fit.stats['max_residual'])}."
            )
            ctx.piece(group, (), None, fit, (a + 0.3 * span, a + span), None if brick_cache is None
                      else brick_cache(group))  # fmt: skip
            by_group[gid] = {"group": gid, "name": group.name, "faults": [], "wings": [], "points": data.n,
                             "offset": None}  # fmt: skip
        result.fits[gid] = fit
        solved.append(Solved(group, None, fit, fit.field, data))
    ids = [int(g.id or 0) for g in groups]
    result.fits = {i: result.fits[i] for i in ids if i in result.fits}
    # the offset of the wings (read only)
    d_max = float(spec.data.d_max) if spec.data is not None else D_MAX_DEFAULT
    if tick is not None:
        tick(0.95, "Разломы: смещение крыльев")
    for gid, field in result.fields.items():
        total, per = offsets(list(field.faults), field, ctx.sheets, ctx.h, d_max, threads)
        by_group[gid]["offset"] = total
        by_group[gid]["offset_by_fault"] = {int(f): v for f, v in per.items()}
        if total is not None:
            names = ", ".join(f"«{f.group.name}»" for f in field.faults)
            journal.append(f"Группа «{by_id[gid].name}»: смещение крыльев через {names} — среднее "
                           f"{_n(total['mean'])} м, наибольшее {_n(total['max'])} м.")  # fmt: skip
    started = time.perf_counter()
    for code in range(1 << len(used)):
        rkey = tuple((int(f.id or 0), (code >> i) & 1) for i, f in enumerate(used))
        try:
            rest = tiling.rest_of(rkey)
        except solids.SolidError as err:
            raise _solids_error(err) from None
        if not rest.empty:
            label = f"Прочее, крыло {code}"
            result.outputs.append(Output("body", None, code, None, label, rest.vertices, rest.triangles, rest.volume,
                                         rest.area, {"triangles": len(rest.triangles), "parts": rest.parts}))
            journal.append(f"«{label}»: объём {_n(rest.volume)} м³.")
    ctx.timing["tiling"] = ctx.timing.get("tiling", 0.0) + time.perf_counter() - started
    rank = {i: r for r, i in enumerate(ids)}
    fault_rank = {int(f.id or 0): r for r, f in enumerate(used)}

    def section(o: Output) -> tuple[int, int]:
        if o.group is None:
            return 2, 0
        return (0, fault_rank[o.group]) if o.group in fault_rank else (1, rank[o.group])

    result.outputs.sort(key=section)  # stable: inside a group the wings go by the code, the surface before the body
    balance = abs(tiling.total - tiling.volume) / tiling.volume if tiling.volume else 0.0
    ctx.timing["total"] = time.perf_counter() - started_all
    result.stats.update({"time": ctx.timing, "balance": balance, "boundary_volume": tiling.volume, "groups": n,
                         "levels": ctx.levels, "h": ctx.h, "stack": plan.summary(),
                         "by_group": [by_group[i] for i in ids]})  # fmt: skip
    if balance > 1e-9:
        journal.append(f"Сумма объёмов тел отличается от объёма границы на {_n(balance)} (доля) — больше 1e-9.")
    if tick is not None:
        tick(1.0, "Тела: готово")
    return result


def field_faults(field: FaultedField) -> list[int]:
    """The ids of the faults of a cut group, in the order of its wing codes."""
    return [f.gid for f in field.faults]


__all__ = ["FaultsResult", "build_with_faults", "field_faults", "used_faults"]
