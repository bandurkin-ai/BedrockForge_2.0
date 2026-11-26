"""Data points of an implicit model (ADR 0026, решения 4–6; ТЗ 8.9.2): what the RBF of every group interpolates.

    pts = load(project, spec, groups=None, cancel=None) -> Points      all groups of the spec (or the chosen ids)
    pts.of_group(gid) -> Points          the rows of one group (wing −1 — every wing; 0 / 1 — Э19б)
    pts.to_parquet() -> bytes            the blob `implicit_points` (§2.1 of the contract);  Points.from_parquet(bytes)
    canonical_order(xyz, f, kind, *tie)  lexsort by (x, y, z, f, kind) — решение 4
    merge_coincident(xyz, f, tol)        the rows kept: points within 1e−9 m with equal f are one, different f stay
    assemble(parts, group_names=…)       parts → one Points: groups ascending, canonical order inside, merged, journal

A row of `Points` is one datum: `xyz` float64 in project coordinates (never shifted), `f` the value, `kind` an index of
`spec.POINT_TYPES` (`contact` 0, `inside` −D, `outside` +D, `manual`, `value`), `group` the id of the group, `wing`
(−1: no division by a fault), `src` an index into `sources` (a hole name or «слой …»), `span` the interval of the hole
(from, to; NaN — none; a contact is [t, t]) and `vertex` the vertex of a layer (−1 — none; a point between the vertices
of a line carries the first vertex of its segment).

Rules of the points (решения 4–6, ТЗ 8.9.2)
  * the order does not depend on the order of holes, tables, layers or threads: groups ascending, inside a group the
    lexicographic (x, y, z, f, kind), then the source name, the span and the vertex; `sources` are sorted names;
  * points closer than 1e−9 m with an equal value are one (the first in the order stays), with different values both
    stay (their conflict is settled by the smoothing η, Д-25); both counts go to `counts` and `notes` (the journal);
  * a gap in the code is a break (no points), a missing grade is no point — never a zero (ТЗ 8.0.1);
  * more than 2 million points — 422 `too_many_points`; a group of a domain without a change of sign — 422
    `no_contacts`; texts are Russian, `spec.refuse` gives the {code, message, hint}.

`load` reads the tables of the project (`composites.source`, the trajectories of the set — ТЗ 7.11) and the layers of
strings (`lines.store`); the builders — `data_holes.domain_points`, `data_manual.layer_points`, `grade_points` — work
on arrays. numba is not imported here: the kernels of `data_kernels.py` come inside the functions that need them, so a
job handler that imports this module does not pay for numba in the API process.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from bf_core.api.errors import ApiError

from .spec_parts import COINCIDE, POINT_TYPES, POINTS_MAX, refuse

if TYPE_CHECKING:
    from .spec import Group, ModelSpec

F8 = NDArray[np.float64]
I8 = NDArray[np.int64]
Tick = Callable[[float | None, str | None], None]

KIND = {name: i for i, name in enumerate(POINT_TYPES)}  # the i1 code of a point
WING_NONE = -1
_PARQUET_VERSION = 1
_COLUMNS = ("x", "y", "z", "f", "kind", "group", "wing", "src", "from", "to", "vertex")
_SCALARS = ("points", "merged", "conflicts")


def _beat(tick: Tick | None, share: float | None = None, text: str | None = None) -> None:
    """A sign of life between two steps: a cancel is raised here."""
    if tick is not None:
        tick(share, text)


@dataclass(frozen=True, eq=False)
class Points:
    """The data points of a model by groups, inside a group in the canonical order (module docstring)."""

    xyz: F8
    f: F8
    kind: NDArray[np.int8]
    group: NDArray[np.int32]
    wing: NDArray[np.int8]
    src: NDArray[np.int32]
    span: F8
    vertex: I8
    sources: list[str] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # the journal of the load, Russian
    levels: dict[int, list[float]] = field(default_factory=dict)  # shells: the level of every threshold (T(t))

    @property
    def n(self) -> int:
        return int(self.f.shape[0])

    @staticmethod
    def empty(*, notes: Sequence[str] = (), sources: Sequence[str] = ()) -> Points:
        return Points(np.zeros((0, 3)), np.zeros(0), np.zeros(0, np.int8), np.zeros(0, np.int32),
                      np.zeros(0, np.int8), np.zeros(0, np.int32), np.zeros((0, 2)), np.zeros(0, np.int64),
                      list(sources), {"points": 0}, list(notes))  # fmt: skip

    def take(self, rows: Any) -> Points:
        """The chosen rows (indices or a mask) in the given order; sources, notes and levels stay as they are, counts
        are only the number of points (the counts of a load describe the whole load)."""
        r = np.asarray(rows)
        return Points(self.xyz[r], self.f[r], self.kind[r], self.group[r], self.wing[r], self.src[r], self.span[r],
                      self.vertex[r], list(self.sources), {"points": int(np.atleast_1d(self.f[r]).shape[0])},
                      list(self.notes), dict(self.levels))  # fmt: skip

    def of_group(self, gid: int, wing: int = WING_NONE) -> Points:
        """The rows of group `gid`; `wing` −1 — all of them, 0 / 1 — the points of that wing (Э19б)."""
        mask = self.group == gid
        if wing != WING_NONE:
            mask &= self.wing == wing
        out = self.take(np.flatnonzero(mask))
        return Points(out.xyz, out.f, out.kind, out.group, out.wing, out.src, out.span, out.vertex, out.sources,
                      out.counts, out.notes, {g: v for g, v in self.levels.items() if g == gid})  # fmt: skip

    # --- the blob `implicit_points` ----------------------------------------------------------------------------

    def to_parquet(self) -> bytes:
        """The Parquet file of the points (one row group, zstd): the same points give the same bytes."""
        import pyarrow as pa
        import pyarrow.parquet as pq

        cols = {"x": self.xyz[:, 0], "y": self.xyz[:, 1], "z": self.xyz[:, 2], "f": self.f, "kind": self.kind,
                "group": self.group, "wing": self.wing, "src": self.src, "from": self.span[:, 0],
                "to": self.span[:, 1], "vertex": self.vertex}  # fmt: skip
        meta = {"v": _PARQUET_VERSION, "sources": self.sources, "counts": self.counts, "notes": self.notes,
                "levels": {str(g): v for g, v in self.levels.items()}}  # fmt: skip
        table = pa.table({k: pa.array(np.ascontiguousarray(v)) for k, v in cols.items()})
        table = table.replace_schema_metadata({"bf_points": json.dumps(meta, ensure_ascii=False, sort_keys=True)})
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink, compression="zstd", row_group_size=max(1, self.n), use_dictionary=False,
                       write_statistics=False)  # fmt: skip
        return bytes(sink.getvalue().to_pybytes())

    @staticmethod
    def from_parquet(data: bytes) -> Points:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pq.read_table(pa.BufferReader(data))
        raw = (table.schema.metadata or {}).get(b"bf_points")
        if raw is None:
            raise ApiError(422, "bad_points", "Точки данных модели повреждены.", "Постройте модель заново.")
        meta = json.loads(raw)

        def col(name: str, dtype: Any) -> Any:
            return np.ascontiguousarray(table.column(name).to_numpy(zero_copy_only=False), dtype=dtype)

        xyz = np.column_stack([col("x", "<f8"), col("y", "<f8"), col("z", "<f8")]).astype(np.float64)
        span = np.column_stack([col("from", "<f8"), col("to", "<f8")]).astype(np.float64)
        return Points(xyz, col("f", np.float64), col("kind", np.int8), col("group", np.int32), col("wing", np.int8),
                      col("src", np.int32), span, col("vertex", np.int64), list(meta["sources"]),
                      dict(meta["counts"]), list(meta["notes"]),
                      {int(g): [float(x) for x in v] for g, v in meta["levels"].items()})  # fmt: skip


# --- order and merging (решение 4) -----------------------------------------------------------------------------


def canonical_order(xyz: Any, f: Any, kind: Any, *tie: Any) -> NDArray[np.intp]:
    """The permutation to the canonical order: (x, y, z, f, kind) lexicographically, then the optional `tie` keys (most
    significant first). −0.0 and +0.0 are equal."""
    a = np.asarray(xyz, dtype=np.float64)
    keys = [*(np.asarray(t) for t in reversed(tie)), np.asarray(kind), np.asarray(f), a[:, 2], a[:, 1], a[:, 0]]
    return np.lexsort(keys)


def merge_coincident(xyz: Any, f: Any, tol: float = COINCIDE, *, segment: Any = None,
                     stats: dict[str, int] | None = None, tick: Tick | None = None) -> NDArray[np.intp]:  # fmt: skip
    """The rows that stay of points in the canonical order: a point within `tol` m of an earlier kept point with an
    equal `f` is dropped (the first stays); close points with different values both stay. `segment` — the group
    (and wing) of every row: points of different segments never merge (the rows of a segment are contiguous).
    `stats` gets `merged` (rows dropped) and `conflicts` (pairs of kept points within `tol` whose values differ).
    The pairs come from a k-d tree of every segment (O(N log N): the holes of a vertical fan share x and y)."""
    p = np.asarray(xyz, dtype=np.float64)
    v = np.asarray(f, dtype=np.float64)
    n = int(v.shape[0])
    seg = None if segment is None else np.asarray(segment)
    i_all: list[Any] = []
    j_all: list[Any] = []
    if n > 1:
        cut = np.zeros(1, dtype=np.int64) if seg is None else np.flatnonzero(seg[1:] != seg[:-1]) + 1
        edges = [0, *cut.tolist(), n]
        for a, b in zip(edges[:-1], edges[1:], strict=True):
            _beat(tick)
            if b - a > 1:
                _pairs_within(p[a:b], tol, a, i_all, j_all)
    drop = np.zeros(n, dtype=bool)
    conflicts = 0
    if i_all:
        pi, pj = np.concatenate(i_all), np.concatenate(j_all)
        same = v[pi] == v[pj]
        # drop[b] depends only on the final state of the earlier rows: the pairs go by their later row
        order = np.argsort(pj[same], kind="stable")
        for a, b in zip(pi[same][order].tolist(), pj[same][order].tolist(), strict=True):
            if not drop[a]:
                drop[b] = True
        other = ~same
        conflicts = int((~drop[pi[other]] & ~drop[pj[other]]).sum())
    if stats is not None:
        stats["merged"] = int(drop.sum())
        stats["conflicts"] = conflicts
    return np.flatnonzero(~drop)


def _pairs_within(p: F8, tol: float, shift: int, i_all: list[Any], j_all: list[Any]) -> None:
    """The pairs i < j of the rows of `p` (finite rows only) at a distance ≤ tol, as row numbers + `shift`. The tree
    asks a hair wider and the exact test is the squared distance against tol² (the same bits for any size)."""
    from scipy.spatial import cKDTree

    ok = np.flatnonzero(np.isfinite(p).all(axis=1))
    if ok.shape[0] < 2:
        return
    q = p[ok]
    pairs = cKDTree(q).query_pairs(tol * (1.0 + 1e-9) + 1e-300, output_type="ndarray")
    if not pairs.shape[0]:
        return
    i, j = pairs[:, 0], pairs[:, 1]
    # the rows are ordered by x: the later row is not further than tol along x (the same test as the scan had)
    near = (q[j, 0] - q[i, 0] <= tol) & (((q[i] - q[j]) ** 2).sum(axis=1) <= tol * tol)
    if near.any():
        i_all.append(ok[i[near]] + shift)
        j_all.append(ok[j[near]] + shift)


# --- parts into one set of points -------------------------------------------------------------------------------


def _sum_counts(parts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in parts:
        for key, val in c.items():
            if key == "groups":
                groups = out.setdefault("groups", {})
                for gid, row in val.items():
                    mine = groups.setdefault(str(gid), {})
                    for k2, v2 in row.items():
                        mine[k2] = mine.get(k2, 0) + v2
            elif key in _SCALARS or key.startswith(("kind_", "no_")):
                out[key] = out.get(key, 0) + val
    return out


def assemble(parts: Sequence[Points], *, merge: bool = True, group_names: Mapping[int, str] | None = None,
             tick: Tick | None = None) -> Points:  # fmt: skip
    """The parts as one set of points (module docstring): `sources` of all parts become the sorted unique names,
    rows go by group, wing and the canonical order, coincident points are merged, `counts` and the journal are
    summed (and the merge adds its own lines). A part may be empty; no part — no points."""
    labels = sorted({s for p in parts for s in p.sources})
    where = {s: i for i, s in enumerate(labels)}
    cols: dict[str, list[Any]] = {k: [] for k in ("xyz", "f", "kind", "group", "wing", "src", "span", "vertex")}
    for p in parts:
        if not p.n:
            continue
        lut = np.array([where[s] for s in p.sources], dtype=np.int32)
        for k, v in (("xyz", p.xyz), ("f", p.f), ("kind", p.kind), ("group", p.group), ("wing", p.wing),
                     ("src", lut[p.src] if lut.size else p.src), ("span", p.span), ("vertex", p.vertex)):  # fmt: skip
            cols[k].append(v)
    notes = [t for p in parts for t in p.notes]
    levels: dict[int, list[float]] = {}
    for p in parts:
        levels.update(p.levels)
    counts = _sum_counts(p.counts for p in parts)
    if not cols["f"]:
        counts["points"] = 0
        e = Points.empty(notes=notes)
        return Points(e.xyz, e.f, e.kind, e.group, e.wing, e.src, e.span, e.vertex, [], counts, notes, levels)
    xyz = np.concatenate(cols["xyz"]) + 0.0  # −0.0 becomes +0.0: equal values, equal bytes
    f, kind, group, wing = (np.concatenate(cols[k]) for k in ("f", "kind", "group", "wing"))
    src, span, vertex = (np.concatenate(cols[k]) for k in ("src", "span", "vertex"))
    f = f + 0.0
    _beat(tick, None, "Данные модели: порядок точек")
    order = np.lexsort((vertex, span[:, 1], span[:, 0], src, kind, f, xyz[:, 2], xyz[:, 1], xyz[:, 0], wing, group))
    xyz, f, kind, group, wing, src, span, vertex = (a[order] for a in (xyz, f, kind, group, wing, src, span, vertex))
    stats: dict[str, int] = {}
    if merge and f.shape[0] > 1:
        _beat(tick, None, "Данные модели: совпадающие точки")
        seg = group.astype(np.int64) * 4 + (wing.astype(np.int64) + 1)
        keep = merge_coincident(xyz, f, COINCIDE, segment=seg, stats=stats, tick=tick)
        if keep.shape[0] != f.shape[0]:
            if group_names is not None or stats.get("merged"):
                notes.extend(_merge_notes(group[np.setdiff1d(np.arange(f.shape[0]), keep)], group_names, stats))
            xyz, f, kind, group, wing, src, span, vertex = (a[keep] for a in (xyz, f, kind, group, wing, src, span,
                                                                              vertex))  # fmt: skip
        if stats.get("conflicts"):
            notes.append(f"Совпадающих точек с разным значением — {stats['conflicts']}: остались обе, их разность "
                         "сглаживает параметр η (сглаживание «Авто» или s > 0).")  # fmt: skip
    counts["merged"] = counts.get("merged", 0) + stats.get("merged", 0)
    counts["conflicts"] = counts.get("conflicts", 0) + stats.get("conflicts", 0)
    used = np.unique(src)
    names = [labels[i] for i in used.tolist()]
    src = np.searchsorted(used, src).astype(np.int32)
    counts["points"] = int(f.shape[0])
    for name, code in KIND.items():
        counts[f"kind_{name}"] = int((kind == code).sum())
    counts["groups"] = _group_counts(group, kind, counts.get("groups", {}))
    return Points(np.ascontiguousarray(xyz), f, kind, group, wing, src, span, vertex, names, counts, notes, levels)


def _group_counts(group: Any, kind: Any, old: Mapping[str, Any]) -> dict[str, Any]:
    """The points of every group by kind; the merged and conflicting counts of the parts stay."""
    out: dict[str, Any] = {}
    for gid in np.unique(group).tolist():
        sel = group == gid
        row = {"points": int(sel.sum())}
        for name, code in KIND.items():
            c = int((sel & (kind == code)).sum())
            if c:
                row[name] = c
        for k in ("merged", "conflicts"):
            if k in old.get(str(gid), {}):
                row[k] = old[str(gid)][k]
        out[str(gid)] = row
    return out


def _merge_notes(dropped_groups: Any, names: Mapping[int, str] | None, stats: Mapping[str, int]) -> list[str]:
    """Lines of the journal about the merged points: one per group when the names are known."""
    if names is None:
        return [f"Слито совпадающих точек с равным значением (в пределах 1e−9 м) — {stats['merged']}."]
    out = []
    ids, counts = np.unique(dropped_groups, return_counts=True)
    for gid, c in zip(ids.tolist(), counts.tolist(), strict=True):
        out.append(f"Группа «{names.get(gid, gid)}»: слито совпадающих точек с равным значением "
                   f"(в пределах 1e−9 м) — {c}.")  # fmt: skip
    return out


# --- contacts and size checks ------------------------------------------------------------------------------------


def check_size(n: int) -> None:
    """422 `too_many_points` over 2 million points."""
    if n > POINTS_MAX:
        raise refuse("too_many_points", f"Точек данных {n:,} — больше {POINTS_MAX:,}.".replace(",", " "),
                     "Увеличьте шаг точек Δ_т или уменьшите D_max.", points=int(n))  # fmt: skip


def check_contacts(points: Points, gid: int, *, wing: int = WING_NONE) -> None:
    """422 `no_contacts` {group} when the data of a group have no change of sign: no negative or no positive value."""
    mask = points.group == gid
    if wing != WING_NONE:
        mask &= points.wing == wing
    f = points.f[mask]
    if not (bool((f < 0.0).any()) and bool((f > 0.0).any())):
        extra: dict[str, Any] = {"group": int(gid)}
        if wing != WING_NONE:
            extra["wing"] = int(wing)
        raise refuse("no_contacts", **extra)


def indicator(f: Any, threshold: float) -> F8:
    """The values of an indicator shell (решение 6): [g ≥ t] − 0,5; NaN stays NaN."""
    g = np.asarray(f, dtype=np.float64)
    return np.where(np.isnan(g), np.nan, np.where(g >= threshold, 0.5, -0.5))


def grade_points(xyz: Any, g: Any, *, transform: str = "linear", thresholds: Sequence[float] | None = None) -> Any:
    """`values, notes = grade_points(xyz, g, transform=…, thresholds=…)` — see `data_manual.grade_points`."""
    from . import data_manual

    return data_manual.grade_points(xyz, g, transform=transform, thresholds=thresholds)


# --- the load ------------------------------------------------------------------------------------------------------


def _bad(message: str, hint: str, param: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def load(project: Any, spec: ModelSpec, *, groups: Iterable[Any] | None = None, cancel: Tick | None = None) -> Points:
    """The data points of the groups of `spec` (all, or the ids / Group objects of `groups`), read from the project.

    `cancel` is a tick (`(share, text)`; may raise `JobCancelled`), called between the portions of work. 422
    `too_many_points`, `no_contacts`, `bad_param` {param} (a code field or a grade field that is not there)."""
    from . import data_holes, data_manual

    want = None if groups is None else {int(getattr(g, "id", g)) for g in groups}
    chosen = [(k, g) for k, g in enumerate(spec.groups) if want is None or g.id in want]
    names = {int(g.id): g.name for _k, g in chosen if g.id is not None}
    parts: list[Points] = []
    codes: data_holes.CodeTable | None = None
    total = 0
    for step, (k, g) in enumerate(chosen):
        gid = int(g.id) if g.id is not None else k + 1
        _beat(cancel, step / max(1, len(chosen)), f"Данные группы «{g.name}»")
        got: list[Points] = []
        if g.type in ("domain", "fault"):
            if g.codes:
                if spec.data is None:
                    raise _bad(
                        "Для кодов группы не выбрана таблица кодов.", "Выберите набор, таблицу и поле кода.", "data"
                    )
                if codes is None:
                    codes = data_holes.read_code_table(project, spec.data, cancel)
                got.append(data_holes.domain_points_of(codes, g.codes, d_max=spec.data.d_max, step=spec.data.step,
                                                       group=gid, limit=POINTS_MAX - total, tick=cancel))  # fmt: skip
            got.extend(data_manual.group_layers(project, spec, gid, total + sum(p.n for p in got), cancel))
        elif g.type == "shell":
            got.append(data_holes.shell_points(project, spec, g, gid, k, cancel))
        for p in got:
            parts.append(_labelled(p, g.name))
            total += p.n
        check_size(total)
    out = assemble(parts, group_names=names, tick=cancel)
    check_size(out.n)
    for _k, g in chosen:
        if g.type in ("domain", "fault"):
            check_contacts(out, int(g.id) if g.id is not None else _k + 1)
    return out


def _labelled(p: Points, name: str) -> Points:
    """The journal lines of a part get the name of its group."""
    if not p.notes:
        return p
    return Points(p.xyz, p.f, p.kind, p.group, p.wing, p.src, p.span, p.vertex, p.sources, p.counts,
                  [f"Группа «{name}»: {t[:1].lower() + t[1:]}" for t in p.notes], p.levels)  # fmt: skip


def load_vein_sides(
    project: Any, spec: ModelSpec, group: Group, cancel: Tick | None = None
) -> tuple[F8, F8, list[str]]:
    """The hanging wall and the footwall of a vein (решение 17): the top and the bottom of the intervals with the
    codes of `group` — two arrays of points (x, y, z) in project coordinates, sorted, and the lines of the journal."""
    from . import data_holes

    if spec.data is None or not group.codes:
        raise _bad("У жилы нет кодов или не выбрана таблица кодов.", "Выберите набор, таблицу и поле кода.", "data")
    table = data_holes.read_code_table(project, spec.data, cancel)
    return data_holes.vein_sides_of(table, group.codes, tick=cancel)


__all__ = ["KIND", "Points", "assemble", "canonical_order", "check_contacts", "check_size", "grade_points",
           "indicator", "load", "load_vein_sides", "merge_coincident"]  # fmt: skip
