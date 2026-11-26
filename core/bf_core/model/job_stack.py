"""The stack of relations, the faults and the systems of veins in the jobs `implicit.build` / `implicit.update` (подшаг
19.36; ТЗ Д-24, Д-31, Д-39; ADR 0026 решения 15, 19–21, 22–26, 30; docs/progress/e19-api.md §4, §10).

    check_stack(spec)                                   # the refusals the route can give before the job (422)
    build_domains(spec, pre, points, solver, ...)       # the domains: `faults.build_with_faults` → outputs and parts
    build_system(project, spec, group, pre, h, ...)     # a group `vein_system`: `vein_system.build_vein_system`

The domains of the model go through `faults.build_with_faults` at once: a model without a fault that cuts a domain and
with the relations «Согласно» is `bodies.build_bodies` bit for bit (the module delegates), so the old jobs write the
files they always wrote. What the stack and the faults add to the files:

* the fields. A group no fault cuts has one field (`wing` −1); a group cut by faults has a field per wing — per fit of
  `FaultsResult.wing_fits` — stored under the code of its wing (Σ sign_k·2^k, one fault: 0 for f_F < 0, 1 for the
  other); a finite fault blends the fits of the wing with the fit of the data that are not divided (`faults_parts`),
  and those partial fits are stored under `(mask + 1)·2^m + code` (m — the faults of the group), so that every fit has
  a number of its own and the number of the full fit of a wing is its code. The fault itself has its field too
  (`wing` −1): the check of the model (Д-32) reads it like any other.
* the data. The check rebuilds the patches of a field from the stored points of its group and wing (`check.build`), so
  a field is stored with the points it was solved from: the points the stack took out of a group (erosion, onlap,
  intrusion) are not in its rows, and the points on a fault are in the rows of both wings. `Part.rows` are the rows of
  `points` of a field (None — every row of its group); the table is rewritten from them only when a field has them or
  a wing (`rows_of_parts`), else it is the table of the load as in every Э19 run.
* the numbers. `stats["stack"]` (the order of the solution, the points dropped), `stats["faults"]` (by fault id),
  `stats["by_group"][i]["offset"]` {mean, max} (the offset of the wings across the faults of the group, read only;
  None for a group that no fault cuts) with `faults` and `offset_by_fault`, `stats["vein_pairs"]`
  [{group, a, b, line_length, volume_common}].

A system of veins has no field: every vein of it is a body (`group` = the system, `stats["code"]`), the union of them is
the common body (`stats["common"]`), the lines where the veins cross are layers of strings (`LineOut`, one per pair,
written by `store_lines`). The sides of a vein are read by `data.load_vein_sides`, one call per code. The clip of the
model (`post.clip`) cuts every body of the system as it cuts a vein.

numba, manifold3d and pyfqmr come with the functions that need them, not with this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import numpy as np

from bf_core.api.errors import ApiError

from .job_stages import Built, LineOut, Part, _measures, _part_stats, _post_clip, _sub, _why
from .spec_parts import bad

if TYPE_CHECKING:
    from .bodies_parts import Fit
    from .data import Points
    from .job_stages import Prefetch
    from .spec import Group, ModelSpec
    from .update import CachingSolver, Record

Tick = Callable[[float | None, str | None], None]
WING_MAX = 127  # `implicit_points.wing` and `implicit_patches.wing` are int8
VEIN_KEYS = ("bound", "polygon", "offset", "t_min")  # the parameters of a vein of 8.9.3 inside `params.veins[i]`


# --- the refusals before the job, the numbers of the wings ------------------------------------------------------------


def _too_many(group: str = "") -> ApiError:
    of = f"группы «{group}»" if group else "группы"
    return bad("bad_param", f"У {of} слишком много полей крыльев: разломы дают их больше {WING_MAX}.",
               "Оставьте у группы меньше разломов (сильнее всего растёт число разломов с конечным радиусом) "
               "или задайте разломам бесконечный радиус.", "faults")  # fmt: skip


def wing_id(faults: int, finite: list[int], mask: int, key: tuple[int, ...]) -> int:
    """The number a field of a wing is stored under (the module text): the code of the wing for the full fit, else
    `(mask + 1)·2^m + code`; `key` has −1 where a finite fault does not divide the fit."""
    code = sum(max(int(s), 0) << k for k, s in enumerate(key))
    full = (1 << len(finite)) - 1
    out = code if mask == full else ((mask + 1) << faults) + code
    if out > WING_MAX:
        raise _too_many()
    return out


def max_wing(finite: list[bool]) -> int:
    """The largest number `wing_id` can give a group cut by faults of which `finite[k]` have a radius (the order of
    the list is the order of the wing code): the largest code of a wing of the full fit or of a blend."""
    m = len(finite)
    at = [k for k, f in enumerate(finite) if f]
    code_all = sum(1 << k for k in range(m) if not finite[k])  # the infinite faults divide every fit
    full = (1 << len(at)) - 1
    best = 0
    for mask in range(full + 1):
        code = code_all + sum(1 << at[j] for j in range(len(at)) if mask >> j & 1)
        best = max(best, code if mask == full else ((mask + 1) << m) + code)
    return best


def check_stack(spec: ModelSpec) -> None:
    """What the route can refuse before the job reads anything (e19-api §1): the relations of the domains, a cycle of
    the order of the solution, a fault that is not in the model, more faults than the wings allow (the number of a
    wing is a signed byte: `wing_id` would refuse at the end of the build)."""
    from . import faults, stack

    groups = stack.domains_of(spec)
    stack.StackPlan(groups)
    used = faults.used_faults(spec, groups)
    for g in groups:
        if not g.faults:
            continue
        flags = [f.params.get("radius") is not None for f in used if f.id in g.faults]
        if max_wing(flags) > WING_MAX:
            raise _too_many(g.name)


def _keys(xyz: Any, f: Any) -> Any:
    return np.ascontiguousarray(np.column_stack([xyz, f]), dtype="<f8").view(np.dtype((np.void, 32))).ravel()


def used_rows(points: Points, gid: int, rec: Record) -> Any:
    """The rows of `points` (the group `gid`) a field was solved from, in the order of the field; None — every row of
    the group. A field is a subset of the group, so its (x, y, z, f) name its rows."""
    idx = np.flatnonzero(points.group == gid)
    if rec.xyz is None or rec.f is None or len(rec.f) == len(idx):
        return None
    ka = _keys(points.xyz[idx], points.f[idx])
    order = np.argsort(ka, kind="stable")
    kq = _keys(rec.xyz, rec.f)
    # equal (x, y, z, f) — two contacts of different holes at one place — are taken one by one, in their order
    # (the k-th of the field is the k-th of the data among the equal ones), never both as the first
    qo = np.argsort(kq, kind="stable")
    sq = kq[qo]
    first = np.ones(len(sq), dtype=bool)
    first[1:] = sq[1:] != sq[:-1]
    start = np.maximum.accumulate(np.where(first, np.arange(len(sq)), 0))
    nth = np.empty(len(sq), dtype=np.int64)
    nth[qo] = np.arange(len(sq)) - start
    at = np.minimum(np.searchsorted(ka[order], kq) + nth, len(order) - 1)
    rows = idx[order[at]]
    if not (np.array_equal(points.xyz[rows], rec.xyz) and np.array_equal(points.f[rows], rec.f)):
        raise ApiError(500, "internal", "Точки поля не найдены в данных группы.",
                       "Скопируйте журнал задания и отправьте разработчику.")
    return rows


# --- the domains ------------------------------------------------------------------------------------------------------


def _record(solver: CachingSolver, fit: Fit, gid: int, wing: int) -> Record:
    rec = solver.record_of(fit.patches)
    assert rec is not None  # `fit_group` solved this field through the solver
    rec.group, rec.wing = gid, wing
    return rec


def build_domains(spec: ModelSpec, pre: Prefetch, points: Points, solver: CachingSolver, *,
                  bricks: Callable[[Group], Any], eta_of: Any, structural: Any, threads: int, win: Tick,
                  built: Built) -> None:  # fmt: skip
    """The domains of the model and the faults of them (the module text) into `built`: outputs, journal, parts."""
    from .faults import build_with_faults

    res = build_with_faults(spec, points, pre.boundary, clip=pre.clip, matrix=pre.matrix, matrices_of=structural,
                            eta_of=eta_of, solver=solver, brick_cache=bricks, threads=threads, tick=win)  # fmt: skip
    built.outputs.extend(res.outputs)
    built.journal.extend(res.journal)
    built.time.update({k: float(v) for k, v in res.stats.get("time", {}).items()})
    built.balance = res.stats.get("balance")
    built.boundary_volume = res.stats.get("boundary_volume")
    if res.stats.get("stack"):  # the keys of the maps are text: the answer is the same in the process and as JSON
        stack = res.stats["stack"]
        built.extra["stack"] = {**stack, "relations": {str(k): v for k, v in stack["relations"].items()}}
    if res.stats.get("faults"):
        built.extra["faults"] = {str(k): v for k, v in res.stats["faults"].items()}
    by_id = {g.id: g for g in spec.groups}
    seen = {int(e["group"]): e for e in res.stats.get("by_group", [])}

    def add(gid: int, wing: int, fit: Fit) -> None:
        rec = _record(solver, fit, gid, wing)
        stats = _part_stats(by_id[gid], rec, fit.stats)
        stats["wing"] = None if wing < 0 else wing
        if gid in res.fields or gid in seen:
            e = seen.get(gid, {})
            by_fault = e.get("offset_by_fault")
            stats.update({"faults": list(e.get("faults", [])), "offset": e.get("offset"),
                          "offset_by_fault": None if by_fault is None else {str(k): v for k, v in by_fault.items()}})
        else:
            stats.update({"faults": [], "offset": None, "offset_by_fault": None})
        built.parts.append(Part(gid, wing, rec, stats, used_rows(points, gid, rec)))

    for fid, fit in res.faults.items():
        add(int(fid), -1, fit)
    for gid, fit in res.fits.items():
        if gid not in res.fields:
            add(int(gid), -1, fit)
    for (gid, mask, key), fit in res.wing_fits.items():
        faults = res.fields[gid].faults
        finite = [k for k, f in enumerate(faults) if f.finite]
        add(int(gid), wing_id(len(faults), finite, int(mask), tuple(key)), fit)


# --- the systems of veins ---------------------------------------------------------------------------------------------


def _clipped(spec: ModelSpec, pre: Prefetch, group: Group, v: Any, t: Any, h: float, threads: int, tick: Tick,
             built: Built, label: str) -> tuple[Any, Any] | None:  # fmt: skip
    """A body of the system cut by the clip of the model (`post.clip`); None — nothing of it is left."""
    from bf_core.geo import iso_post
    from bf_core.geo.iso_bricks import IsoError

    from .bodies import _body_failure, _solids_error

    if spec.post.clip == "none":
        return v, t
    clip, boundary = _post_clip(spec, pre)
    try:
        res = iso_post.postprocess(v, t, h=h, post=replace(spec.post, v_min=0.0), clip=clip, boundary=boundary,
                                   threads=threads, tick=tick)  # fmt: skip
    except IsoError as err:
        if err.code == "iso_check":
            raise _body_failure(group, _why(err)) from None
        raise _solids_error(err, group) from None
    built.journal.extend(f"Группа «{group.name}», {label}: {line}" for line in res.journal)
    if len(res.triangles) == 0:
        built.journal.append(f"Группа «{group.name}», {label}: после обрезки ничего не осталось.")
        return None
    return res.vertices, res.triangles


def build_system(project: Any, spec: ModelSpec, group: Group, pre: Prefetch, h: float, threads: int, prog: Tick,
                 built: Built) -> None:  # fmt: skip
    """A system of veins (the module text): a body per vein, the common body, the lines of the pairs, `vein_pairs`."""
    from . import data
    from .vein_system import VeinInput, build_vein_system

    veins = group.params["veins"]
    given = []
    for k, v in enumerate(veins):
        one = replace(group, codes=(str(v["code"]),))
        hanging, footwall, notes = data.load_vein_sides(project, spec, one, _sub(prog, 0.1 * k / len(veins),
                                                                                 0.1 * (k + 1) / len(veins)))
        built.journal.extend(f"Группа «{group.name}», жила «{v['name']}»: {n}" for n in notes)
        given.append(VeinInput(str(v["code"]), str(v["name"]), hanging, footwall,
                               {key: v.get(key) for key in VEIN_KEYS}))  # fmt: skip
    try:
        res = build_vein_system(given, h, threads=threads, tick=_sub(prog, 0.1, 0.8))
    except ApiError as err:
        err.extra.setdefault("group", group.id)
        raise
    gid = int(group.id or 0)
    built.journal.extend(f"Группа «{group.name}»: {n}" for n in res.notes)
    bodies: list[tuple[str, Any, Any, dict[str, Any]]] = []
    for p in res.parts:
        if not p.empty:
            bodies.append((p.name, p.vertices, p.triangles, {"code": p.code, "status": p.status,
                                                             "raw_volume": float(p.raw_volume),
                                                             "cut_by": list(p.cut_by)}))  # fmt: skip
    if res.common is not None:
        bodies.append(("Общее тело", res.common[0], res.common[1], {"common": True}))
    from .bodies import Output

    for i, (name, v, t, info) in enumerate(bodies):
        got = _clipped(spec, pre, group, v, t, h, threads, _sub(prog, 0.8 + 0.2 * i / len(bodies),
                                                                0.8 + 0.2 * (i + 1) / len(bodies)),
                       built, f"«{name}»")  # fmt: skip
        if got is None:
            continue
        volume, area = _measures(*got)
        built.outputs.append(Output("body", gid, None, None, name, np.asarray(got[0], dtype=np.float64),
                                    np.asarray(got[1], dtype=np.int32), volume, area,
                                    {**info, "triangles": int(len(got[1]))}))  # fmt: skip
        shown = f"{volume:.6g}".replace(".", ",")
        built.journal.append(f"Группа «{group.name}»: «{name}», объём {shown} м³.")
    built.extra.setdefault("vein_pairs", []).extend({"group": gid, **p} for p in res.pairs)
    for pl in res.lines:
        if pl.lines:
            built.lines.append(LineOut(gid, str(pl.a), str(pl.b), [dict(x) for x in pl.lines]))


__all__ = ["WING_MAX", "build_domains", "build_system", "check_stack", "used_rows", "wing_id"]
