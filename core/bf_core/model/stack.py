"""The stack of relations of the domains of an implicit model (подшаг 19.32; ТЗ Д-24; ADR 0026 решения 15, 19;
контракт docs/progress/e19-api.md §4, §10).

    res = build_stack(spec, points, boundary, *, relations=None, clip=None, matrix=None, eta_of=None, solver=None,
                      brick_cache=None, threads=1, tick=None, matrices_of=None) -> BodiesResult

The same pipeline as `bodies.build_bodies` («mask of the data → solution → body → subtraction»), with the order of the
solution and the mask of the data of the stack. The groups of `spec.groups` go by age, the youngest first; the
`relation` of a group is carried by the YOUNGER one: how it treats every older domain (контракт §4):

    conformable  «Согласно»   the younger body wins the overlap (the chronology of 8.9.2); no data is dropped
    erode        «Эрозия»     the younger surface cuts the older ones: the older group drops its data points with
                              f_younger < 0 (inside the younger body, and on its surface: the contact of the cut is the
                              cut, not the surface of the older layer); the older body is the older minus the younger
    onlap        «Налегание»  the younger group lies on the older ones: it drops its data points BELOW the older surface
                              (f_older < 0); the younger body is the younger ∩ {f_older ≥ 0}, so the OLDER body wins
    intrusion    «Интрузия»   (Д-31) the younger group is a closed body {f_I < 0} that replaces the volumes of ALL the
                              older groups: every older group drops its data points strictly INSIDE the body
                              (f_I < −tol; the contact on the surface of I is the contact of the older layer with I
                              and stays); the body of I is subtracted from the older ones, and it is itself cut by the
                              groups younger than I (their own relation decides how: «Согласно» / «Эрозия» — they win)

The mask is by the VALUE of the field of the other group at the data point (`Fit.field`, the unbounded RBF), never by
the wireframe: a point outside the boundary, or under a third body, is still on its side of the surface. The
tolerance of «on the surface» is the residual of the fit of the group whose surface it is (the RBF reproduces its own
contacts to that): a contact shared by two groups is not thrown out by the rounding of the field.

The order of the solution is the order of the winners: a pair (younger i, older j) has the winner i for `conformable`
/ `erode` and j for `onlap`; the groups are solved, and the bodies are subtracted, in a topological order of these
pairs (the smallest position first: the youngest). A group is masked by the groups that have been solved before it,
so the field it looks at is never a field that needs it. The intrusion is in front of the older groups (it wins them)
and behind the younger ones that win it (their relation: `conformable` / `erode`), so a young cover that erodes the top
of the intrusion is solved first, then the intrusion, then the host rock. A cycle is refused (`bad_relations`); the
relations of one group are uniform towards all the older ones, so the carrier-by-carrier graph has none, but faults
(19.33) put their own edges into `precedence`.

Only the domains are in the stack; shells, veins and expansions are made by their own modules, faults by `faults`
(19.33). The dropped points are in the journal (the group, how many of how many, by whose surface and relation) and in
`stats["stack"]`. The result is the same bits for any `threads` and any order of the holes: the masks are pure
functions of the points and of the fields.

manifold3d, pyfqmr and numba are imported by the stages that need them (`bodies`), not with this module.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

from .bodies import BodiesResult, Solved, build_bodies
from .spec_parts import RELATIONS, refuse

if TYPE_CHECKING:
    from bf_core.geo.iso_bricks import BrickCache

    from .bodies_parts import EtaOf, MatricesOf, Solver
    from .data import Points
    from .spec import ModelSpec
    from .spec_parts import Group

F8 = NDArray[np.float64]
Tick = Callable[[float, str], None]

TOL_REL = 1e-9  # the least tolerance of «on the surface», in h̄ (the median distance between the data points)
NAMES = {"conformable": "Согласно", "erode": "Эрозия", "onlap": "Налегание", "intrusion": "Интрузия"}
#: the relations this module builds
BUILT = ("conformable", "erode", "onlap", "intrusion")
#: the relations of a YOUNGER group that mask the data of an older one, with the side that is dropped
MASKING = ("erode", "intrusion")


def _bad(code: str, message: str, hint: str, **extra: Any) -> ApiError:
    return ApiError(422, code, message, hint, extra or None)


def domains_of(spec: ModelSpec, relations: Mapping[int, str] | None = None) -> tuple[Group, ...]:
    """The domains of `spec` by age (the youngest first) with the relations of `relations` {group id: relation} set
    over those of the model. 422 `bad_param` for a group that is not in the model, a fault, an unknown relation;
    `not_ready` for a relation this module does not build yet."""
    rel = dict(relations or {})
    by_id = {g.id: g for g in spec.groups}
    for gid, value in rel.items():
        g = by_id.get(gid)
        if g is None:
            raise _bad("bad_param", "Отношение задано несуществующей группе.", "Обновите вкладку модели.",
                       param=f"relations.{gid}")  # fmt: skip
        if g.type == "fault":
            raise _bad("bad_param", f"У разлома «{g.name}» нет отношений.", "Разлом задаёт крылья групп, а не стек.",
                       param=f"relations.{gid}")  # fmt: skip
        if g.type != "domain" and value != g.relation:  # a shell, a vein, an expansion is not in the stack
            raise _bad("bad_param", f"У группы «{g.name}» нет отношения к другим группам.",
                       "Отношения задаются доменам литологии: оболочки, жилы и расширения в стек не входят.",
                       param=f"relations.{gid}")  # fmt: skip
        if value not in RELATIONS:
            known = ", ".join(NAMES[r] for r in RELATIONS)
            raise _bad("bad_param", f"Отношение группы «{g.name}»: неизвестное значение «{value}».",
                       f"Выберите одно из: {known}.", param=f"relations.{gid}")  # fmt: skip
    groups = tuple(replace(g, relation=rel.get(g.id, g.relation)) if g.id in rel else g
                   for g in spec.groups if g.type == "domain")  # fmt: skip
    for g in groups:
        if g.id is None:
            raise _bad("bad_param", f"У группы «{g.name}» нет номера.", "Сохраните модель и повторите.", param="groups")
        if g.relation not in BUILT:
            raise _bad("not_ready", f"Отношение группы «{g.name}» («{NAMES.get(g.relation, g.relation)}») "
                       "пока не строится.", "Выберите «Согласно», «Эрозия» или «Налегание».", group=g.id)  # fmt: skip
    return groups


def precedence(groups: Sequence[Group]) -> list[tuple[int, int]]:
    """The pairs (winner, loser) of positions in `groups` (the youngest first): for the relation of the younger group
    towards every older one — the younger wins for `conformable` / `erode` / `intrusion`, the older for `onlap`."""
    edges: list[tuple[int, int]] = []
    for i, g in enumerate(groups):
        for j in range(i + 1, len(groups)):
            edges.append((j, i) if g.relation == "onlap" else (i, j))
    return edges


def order_of(count: int, edges: Sequence[tuple[int, int]], names: Sequence[str] | None = None) -> list[int]:
    """A topological order of the positions 0…count−1 by `edges` (a, b): a before b; among the free ones the smallest
    position (the youngest) first. 422 `bad_relations` for a cycle, with the groups it holds."""
    after: list[list[int]] = [[] for _ in range(count)]
    wait = [0] * count
    for a, b in edges:
        after[a].append(b)
        wait[b] += 1
    ready = [i for i in range(count) if wait[i] == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for b in after[i]:
            wait[b] -= 1
            if wait[b] == 0:
                heapq.heappush(ready, b)
    if len(order) < count:
        stuck = [i for i in range(count) if wait[i] > 0]
        shown = ", ".join(f"«{names[i] if names else i}»" for i in stuck)
        raise _bad("bad_relations", f"Отношения групп образуют цикл: {shown} ждут друг друга.",
                   "Измените отношение одной из этих групп или их порядок в стеке.", groups=stuck)  # fmt: skip
    return order


class StackPlan:
    """The order of the solution of the domains and the mask of their data (see the module text)."""

    def __init__(self, groups: Sequence[Group], threads: int = 1) -> None:
        self.groups = tuple(groups)
        self.threads = max(int(threads), 1)
        names = [g.name for g in self.groups]
        self.sequence = order_of(len(self.groups), precedence(self.groups), names)
        self.pos = {int(g.id): k for k, g in enumerate(self.groups) if g.id is not None}
        self.order = tuple(int(self.groups[k].id or 0) for k in self.sequence)
        self.dropped: list[dict[str, Any]] = []

    @property
    def plain(self) -> bool:
        """Every relation is «Согласно»: the plain chronology of `build_bodies`."""
        return all(g.relation == "conformable" for g in self.groups)

    def sources(self, group: Group, solved: Sequence[Solved]) -> list[tuple[Solved, str]]:
        """The solved groups that mask `group` and how: (the group, `erode` | `intrusion` | `onlap`), in the order of
        the solution."""
        me = self.pos[int(group.id or 0)]

        def kind(j: int) -> str | None:
            if j < me and self.groups[j].relation in MASKING:
                return self.groups[j].relation
            return "onlap" if j > me and self.groups[me].relation == "onlap" else None

        found = [(s, k) for s in solved if (k := kind(self.pos[int(s.group.id or 0)])) is not None]
        need = sum(1 for j in range(len(self.groups)) if kind(j) is not None)
        if need != len(found):  # the order of the solution must put the masking groups first
            raise _bad("bad_relations", f"Порядок расчёта группы «{group.name}» нарушен.",
                       "Это ошибка вызова: повторите построение.", group=group.id)  # fmt: skip
        return found

    def mask(self, group: Group, points: Points, solved: Sequence[Solved], journal: list[str]) -> NDArray[np.bool_]:
        """The data points of `group` that take part in its solution (the rule of the module text); the dropped ones go
        to the journal and to `self.dropped` (`relation`: erode / onlap / intrusion — the relation of the group `by`).
        422 `no_contacts` when nothing is left to define the surface."""
        keep = np.ones(points.n, dtype=bool)
        for source, kind in self.sources(group, solved):
            rows = np.flatnonzero(keep)
            if rows.size == 0:
                break
            fit = source.fit
            f = np.asarray(fit.field(points.xyz[rows], threads=self.threads), dtype=np.float64)
            tol = max(TOL_REL * fit.h_bar, float(fit.stats.get("max_residual", 0.0)))
            with np.errstate(invalid="ignore"):  # NaN (no patch reaches the point) is neither above nor below
                drop = f < tol if kind == "erode" else f < -tol  # the cut drops its own contacts, the others keep them
            lost = int(drop.sum())
            nan = int(np.isnan(f).sum())
            keep[rows[drop]] = False
            if lost or nan:
                self.dropped.append({"group": group.id, "by": source.group.id, "relation": kind, "points": lost,
                                     "of": int(rows.size), "undefined": nan})  # fmt: skip
            if lost:
                where = {"erode": "внутри тела или на поверхности", "intrusion": "внутри тела"}.get(
                    kind, "ниже поверхности"
                )
                journal.append(
                    f"Группа «{group.name}»: {NAMES[kind].lower()} группы «{source.group.name}» — "
                    f"отброшено точек {lost} из {rows.size} ({where} группы «{source.group.name}»)."
                )
            if nan:
                journal.append(f"Группа «{group.name}»: поле группы «{source.group.name}» не определено в точках: "
                               f"{nan} (оставлены).")  # fmt: skip
        if not bool(keep.all()):
            left = points.f[keep]
            if not (bool((left < 0.0).any()) and bool((left > 0.0).any())):
                raise refuse("no_contacts", f"В данных группы «{group.name}» после отбрасывания точек по отношениям "
                             "стека контактов не осталось: поверхность не определена.",
                             "Проверьте порядок групп и отношения во вкладке «Стек» или добавьте данные группы "
                             "по обе стороны её поверхности.", group=group.id, dropped=int((~keep).sum()))  # fmt: skip
        return keep

    def summary(self) -> dict[str, Any]:
        """`stats["stack"]`: the order of the solution (ids), the relations, the dropped points by group and source."""
        total = sum(int(d["points"]) for d in self.dropped)
        return {"order": list(self.order), "relations": {int(g.id or 0): g.relation for g in self.groups},
                "dropped": [dict(d) for d in self.dropped], "dropped_total": total}  # fmt: skip


def build_stack(
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
) -> BodiesResult:
    """The surfaces and bodies of the domains of `spec` with the stack of relations: the arguments and the result are
    those of `bodies.build_bodies`. `relations` {group id: relation} overrides the relations of the model (the
    «Стек» window before it is saved); the order of `spec.groups` is the age. With every relation «Согласно» the
    result is the bits of `build_bodies`. Outputs and fits go in the order of the groups; `stats["stack"]` has the
    order of the solution and the dropped points. Faults, shells and veins are not in the stack: the callers make
    them apart (19.33 `faults`, 19.14 `shells`, 19.15 `veins`)."""
    groups = domains_of(spec, relations)
    plan = StackPlan(groups, threads)
    res = build_bodies(spec, points, boundary, clip=clip, matrix=matrix, matrices_of=matrices_of, eta_of=eta_of,
                       solver=solver, brick_cache=brick_cache, threads=threads, tick=tick,
                       order=None if plan.plain else plan.order, mask_fn=None if plan.plain else plan.mask)  # fmt: skip
    if not plan.plain and groups:
        names = {int(g.id or 0): g.name for g in groups}
        line = ", ".join(f"«{names[i]}»" for i in plan.order)
        rels = "; ".join(f"«{g.name}» — {NAMES[g.relation].lower()}" for g in groups if g.relation != "conformable")
        res.journal.insert(0, f"Стек отношений: порядок расчёта — {line}; {rels}.")
    res.stats["stack"] = plan.summary()
    return res


__all__ = ["BUILT", "NAMES", "StackPlan", "build_stack", "domains_of", "order_of", "precedence"]
