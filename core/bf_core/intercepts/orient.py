"""The orientation of intervals: the sources of ТЗ 8.5.2 as a chain (ADR 0021 §4, решения 4–6, 9).

    rows = Intervals.of(lines, st)                             the rows of a table snapshot, in its line order
    rows = Intervals.grouped(holes, dense, starts, frm, to)    any intervals grouped by hole (candidates of 8.5.3)
    o = resolve(spec.orientation, rows, Inputs(traj, lines, tables, mesh, bodies), threads=…, tick=…) -> Orientation
        o.normal          float64 [n, 3]  the unit normal of the plane of every interval; NaN — none
        o.alpha, o.dipdir float64 [n]     ALPHA, DIPDIR, degrees; NaN — none
        o.src             uint8 [n]       SRC_* — the source that gave it; TT_SRC is `SRC_LABELS[src]`
        o.sin_alpha       float64 [n]     not NaN — TT = l·sin α_к (core: a measurement inside the interval);
                                          None — no source gave one
        o.flag            uint8 [n]       FLAG_CORE_LOCAL (bit 4): a core measurement that was not turned
        o.valid           bool [n]        the interval takes part: both depths, TO > FROM
        o.absent, o.journal, o.report     sources that were not there; lines of the job log; numbers per source

The chain. `spec.orientation` — 0–3 parsed sources (`spec.Source`) by priority; an interval takes the plane of the
first source that gave one, a later source is asked only about the intervals still without a plane (`todo`).
Nothing gave — SRC_NONE («нет»): the caller sets bit 2 and leaves TT empty. An interval without a depth or
with TO ≤ FROM is out of the calculation: no source is asked about it (ADR 0021 §12).

Sources.
    global   α, Aп for every interval.
    domain   The code of the interval → α, Aп by the table of the request. The code — a field of the table of
             the intervals, of another interval table, or the closed bodies of `domain.wireframes` (their
             stretches, `composites.coding.body_stretches`). Rows of the table itself take their own code; any
             other interval — the code with the greatest length inside it (`intervals.mode_by_length`; equal —
             the first down the hole). Codes are compared as the table of the request writes them: text
             without spaces around, a number by `format(x, ".15g")` (−0.0 is 0). No code, or a code the table
             does not list — the source gave nothing.
    mesh     `Inputs.mesh` — a function of the protocol `MeshSource` (`orient_mesh`, 15.7). No function, or it
             answers None (the preview of a large wireframe) — the source is not there: the next one of the
             chain is taken and `absent` names it.
    core     Measurements (α_к, β_к) of a table of the set: `core_orient.measurements` and `assign`. The depth
             of a measurement — its FROM (an event), the middle (an interval).
    trend    A trend of the project (ТЗ 8.5.2, вопрос № 57; ADR 0026, решение 27). A global one — the plane of its
             minor axis (the axis of the least ratio, the third by a tie: `rotation.plane_of` of its angles) for
             every interval, as a source `global` with those angles. A structural one — the local normal
             (`trend_struct.local_axes`, R_j's third row) at the middle of the interval, along the hole; an
             interval without a position (no trajectory) gets none: NaN, never 0. TT_SRC — «тренд».
α and Aп of the first two are written as the request gave them (360 → 0); of the others — by the normal, as
`formulas.plane_angles` takes them. A normal a source gives must be a unit vector (to 1e−9): a wrong TT is
never written silently — ValueError.

Tables are snapshots `composites.source.Lines`; holes of different tables are matched by `dh_holes.id`. The
result does not depend on `threads`; `tick` gets the share done and may raise to cancel.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from bf_core.composites import intervals as iv
from bf_core.composites import strips
from bf_core.composites.coords import hole_index
from bf_core.composites.strips import Tick
from bf_core.drillholes.desurvey import Trajectories
from bf_core.jit import njit

from . import core_orient as co
from . import directions as dr
from .formulas import LEVEL_DEG, VERTICAL, plane_normal

if TYPE_CHECKING:
    from .spec import Source

(SRC_NONE, SRC_GLOBAL, SRC_DOMAIN, SRC_MESH_CROSSING, SRC_MESH_NEAREST, SRC_CORE_MEASURED,
 SRC_CORE_NEAREST, SRC_TREND) = range(8)  # fmt: skip
#: TT_SRC of a code (ADR 0021 §4).
SRC_LABELS = ("нет", "глобальная", "домен", "каркас: пересечение", "каркас: ближайшая", "керн: замер",
              "керн: ближайший", "тренд")  # fmt: skip
KIND_TITLES = {"global": "глобальная", "domain": "по домену", "mesh": "каркас", "core": "керн", "trend": "тренд"}
UNIT_EPS = 1e-9  # |n|² − 1 of a normal a source may give
_NAMED = 5  # codes named in a line of the journal


@dataclass(frozen=True, eq=False)
class Intervals:
    """Intervals of holes grouped by hole: the rows starts[i] : starts[i + 1] are of the hole holes[i], in any
    order inside it; they may overlap. Make them with `of` or `grouped` (the arrays are checked there)."""

    holes: np.ndarray  # int64 [nh]: the key of every hole — dh_holes.id; a hole is listed once
    dense: np.ndarray  # int64 [nh]: its index in the trajectories; −1 — none
    starts: np.ndarray  # int64 [nh + 1]
    frm: np.ndarray  # float64 [n]
    to: np.ndarray  # float64 [n]
    table_id: int | None = None  # the intervals are the rows of this table, in its line order

    @classmethod
    def grouped(cls, holes: Any, dense: Any, starts: Any, frm: Any, to: Any, table_id: int | None = None) -> Intervals:
        h, d, s = dr._holes(holes), dr._holes(dense), dr._holes(starts)
        f, t = dr._plain(frm, np.float64), dr._plain(to, np.float64)
        ok = h.ndim == 1 and d.shape == h.shape and s.shape == (h.shape[0] + 1,) and f.ndim == 1 and t.shape == f.shape
        if not ok or s[0] != 0 or s[-1] != f.shape[0] or bool((np.diff(s) < 0).any()):
            raise ValueError("intervals: holes, dense [nh], starts [nh + 1] and frm, to [starts[-1]] do not agree")
        if np.unique(h).shape[0] != h.shape[0]:
            raise ValueError("intervals: a hole is listed twice")
        return cls(h, d, s, f, t, table_id)

    @classmethod
    def of(cls, lines: Any, st: Any) -> Intervals:
        """The rows of a snapshot (`composites.source.Lines`) in its line order; `st` — the trajectories of the
        set (`drillholes.trajectory.SetTrajectories`: `hole_ids` ascending, `traj`)."""
        ids = np.asarray(lines.hole_db_ids, dtype=np.int64)
        dense = hole_index(st.hole_ids, ids)
        dense[np.isin(dense, np.asarray(st.traj.skipped, dtype=np.int64))] = -1
        return cls.grouped(ids, dense, lines.starts, lines.from_m, lines.to_m, int(lines.table_id))

    @property
    def n(self) -> int:
        return int(self.frm.shape[0])

    @cached_property
    def group(self) -> np.ndarray:
        """int64 [n]: the place of the hole of every row in `holes`."""
        return np.repeat(np.arange(self.holes.shape[0], dtype=np.int64), np.diff(self.starts))

    @cached_property
    def valid(self) -> np.ndarray:
        """bool [n]: the row takes part — both depths are there and TO > FROM."""
        with np.errstate(invalid="ignore"):
            return np.isfinite(self.frm) & np.isfinite(self.to) & (self.to > self.frm)  # type: ignore[no-any-return]

    def keys(self) -> np.ndarray:
        """int64 [n]: the key (dh_holes.id) of the hole of every row."""
        return self.holes[self.group]  # type: ignore[no-any-return]

    def dense_rows(self) -> np.ndarray:
        """int64 [n]: the index of the hole of every row in the trajectories; −1 — none."""
        return self.dense[self.group]  # type: ignore[no-any-return]

    def targets(self) -> iv.Lines:
        """The intervals as targets of `intervals.mode_by_length`: the holes are their places in `holes`."""
        return iv.Lines(np.arange(self.holes.shape[0], dtype=np.int64), self.starts, self.frm, self.to)

    def places(self, hole_ids: Any) -> np.ndarray:
        """For every hole id — its place in `holes`; −1 — these intervals do not have the hole."""
        ids = np.ascontiguousarray(hole_ids, dtype=np.int64)
        if self.holes.shape[0] == 0:
            return np.full(ids.shape[0], -1, dtype=np.int64)
        order = np.argsort(self.holes, kind="stable")
        at = hole_index(self.holes[order], ids)
        return np.where(at >= 0, order[np.maximum(at, 0)], -1).astype(np.int64)


@dataclass(eq=False)
class Given:
    """What a source gave: arrays over all the rows (only those of `todo` are read)."""

    normal: Any  # float64 [n, 3], or [3] — one for every row: unit normals; a row with a NaN — nothing given
    src: Any  # uint8 [n] or one code: SRC_* of the rows given
    alpha: Any = None  # float64, one per normal: the angles as the request gave them; None — by the normal
    dipdir: Any = None
    index: np.ndarray | None = None  # int [n]: the row takes normal[index] [k, 3], alpha[index]; −1 — nothing
    sin_alpha: np.ndarray | None = None  # float64 [n]: not NaN — TT = l·sin α_к, given even without a normal
    flag: np.ndarray | None = None  # uint8 [n]: FLAG_CORE_LOCAL
    journal: list[str] = field(default_factory=list)  # lines for the job log
    report: dict[str, Any] = field(default_factory=dict)  # numbers for `dh_results.report`


class MeshSource(Protocol):
    """The source «каркас или поверхность» (`orient_mesh`, 15.7): the planes of the rows `todo` by the wireframes
    of `source` (`spec.Source` of the kind mesh: `meshes`, `d_max`, `r_n`) — `Given` with SRC_MESH_CROSSING /
    SRC_MESH_NEAREST per row, or None when it cannot answer at all (the next source of the chain is taken)."""

    def __call__(self, rows: Intervals, traj: Trajectories, source: Any, *, todo: np.ndarray, threads: int = 1,
                 tick: Tick | None = None) -> Given | None: ...  # fmt: skip


@dataclass(eq=False)
class Inputs:
    """What the sources read."""

    traj: Trajectories  # of the set; `Intervals.dense` are its holes
    lines: Any = None  # source.Lines: the table of the calculation with its domain and core fields read
    tables: Mapping[int, Any] = field(default_factory=dict)  # source.Lines of the other tables by table id
    mesh: MeshSource | None = None  # None — the source «каркас» is not there
    #: `domain.wireframes` → the bodies by priority (`composites.coding.Body`); None — `jobs_coding.read_bodies`
    #: (the job and the preview compute inside `jobs_coding.reading(project)`).
    bodies: Callable[[Any], Iterable[Any]] | None = None
    #: a structural trend (its `meta`) → what `trend_struct.local_axes` reads (`trend_struct.load_source`); None —
    #: through the project of `jobs_coding.reading` (the job and the preview compute inside it).
    trend: Callable[[Any], Any] | None = None

    def table(self, table_id: int) -> Any:
        if self.lines is not None and int(self.lines.table_id) == int(table_id):
            return self.lines
        got = self.tables.get(int(table_id))
        if got is None:
            raise ValueError(f"orientation: the table {int(table_id)} was not read (Inputs.tables)")
        return got


@dataclass(frozen=True, eq=False)
class Orientation:
    """The planes of intervals — see the module docstring."""

    normal: np.ndarray
    alpha: np.ndarray
    dipdir: np.ndarray
    src: np.ndarray
    sin_alpha: np.ndarray | None
    flag: np.ndarray
    valid: np.ndarray
    absent: tuple[str, ...]  # kinds of the sources that were not there
    journal: list[str]
    report: list[dict[str, Any]]  # per source of the chain: {"source": kind, "rows": given, …}


def _part(tick: Tick | None, lo: float, hi: float) -> Tick | None:
    return None if tick is None else (lambda f: tick(lo + (hi - lo) * f))


def _count(x: int) -> str:
    return f"{int(x):,}".replace(",", " ")


# --- the code of a domain ------------------------------------------------------------------------------------


def _column(table: Any, name: str, text: bool = False) -> Any:
    """A field of a snapshot the orientation reads; ValueError — the caller did not read it."""
    got = (table.codes if text else table.numbers).get(name)
    if got is None:
        raise ValueError(f"orientation: the field {name!r} of the table {int(table.table_id)} was not read")
    return got


def _field_codes(table: Any, d: Any) -> tuple[np.ndarray, list[str]]:
    """int32 codes of the domain field (−1 — empty) in the line order of its table, and every code as the table
    of the request writes it."""
    if d.kind == "text":
        codes = np.asarray(_column(table, d.field, text=True), dtype=np.int32)
        return codes, [str(x).strip() for x in table.labels[d.field]]
    from bf_core.composites.domains import number_codes

    codes, uniques = number_codes(np.asarray(_column(table, d.field), dtype=np.float64) + 0.0)  # −0.0 is 0
    return codes, [format(float(u), ".15g") for u in uniques.tolist()]


def _body_codes(wf: Any, rows: Intervals, inputs: Inputs, threads: int, tick: Tick | None) -> tuple[Any, ...]:
    """The code of every interval by the stretches of the holes inside the closed bodies `wf`."""
    from bf_core.composites import coding

    ok = rows.valid
    if not ok.any():
        return np.full(rows.n, -1, dtype=np.int32), [], []
    if inputs.bodies is not None:
        bodies = inputs.bodies(wf)
    else:
        from bf_core.composites.jobs_coding import read_bodies

        bodies = read_bodies(wf)
    extent = iv.build_lines(rows.group[ok], rows.frm[ok], rows.to[ok])  # the holes and how deep their rows go
    st = coding.body_stretches(extent, inputs.traj, rows.dense[extent.holes], bodies,
                               n_bodies=len(getattr(wf, "targets", ())) or None, threads=threads,
                               tick=_part(tick, 0.0, 0.8))  # fmt: skip
    code = iv.mode_by_length(st.lines, st.code, rows.targets(), threads=threads, tick=_part(tick, 0.8, 1.0))
    return code, [str(x).strip() for x in st.names], list(st.journal)


def domain_codes(d: Any, rows: Intervals, inputs: Inputs, *, threads: int = 1,
                 tick: Tick | None = None) -> tuple[np.ndarray, list[str], list[str]]:  # fmt: skip
    """The domain of every interval by `d` (`composites.jobs.DomainSpec`): int32 codes [n] (−1 — none), the
    code as the table of the request writes it, lines for the job log (module docstring)."""
    if d.wireframes is not None:
        return _body_codes(d.wireframes, rows, inputs, threads, tick)  # type: ignore[return-value]
    table = inputs.table(d.table_id)
    codes, names = _field_codes(table, d)
    if rows.table_id is not None and rows.table_id == int(table.table_id):  # the rows carry their own code
        if codes.shape[0] != rows.n:
            raise ValueError("orientation: the intervals are not the rows of their table")
        return codes, names, []
    hole = rows.places(table.hole_db_ids)[table.hole_of()]
    keep = hole >= 0
    carrier = iv.build_lines(hole[keep], np.asarray(table.from_m)[keep], np.asarray(table.to_m)[keep])
    code = iv.mode_by_length(carrier, carrier.take(codes[keep]), rows.targets(), threads=threads, tick=tick)
    return code, names, []


# --- the sources ---------------------------------------------------------------------------------------------


def _global(s: Any, rows: Intervals, inputs: Inputs, todo: np.ndarray, threads: int, tick: Tick | None) -> Given:
    return Given(plane_normal(s.alpha, s.dipdir), SRC_GLOBAL, float(s.alpha), float(s.dipdir) % 360.0)


def _domain(s: Any, rows: Intervals, inputs: Inputs, todo: np.ndarray, threads: int, tick: Tick | None) -> Given:
    code, names, journal = domain_codes(s.domain, rows, inputs, threads=threads, tick=tick)
    listed = {str(a.code): i for i, a in enumerate(s.attitudes)}
    place = np.array([*(listed.get(name, -1) for name in names), -1], dtype=np.int64)
    which = place[code]  # the row of the table of every interval; the code −1 takes the last one: −1
    alpha = np.array([float(a.alpha) for a in s.attitudes])
    dipdir = np.array([float(a.dipdir) % 360.0 for a in s.attitudes])
    empty = todo & (code < 0)
    lost = todo & (code >= 0) & (which < 0)
    report = {"no_code": int(np.count_nonzero(empty)), "not_listed": int(np.count_nonzero(lost))}
    if report["no_code"]:
        journal.append(f"По домену: интервалов без кода домена — {_count(report['no_code'])}.")
    if report["not_listed"]:
        met = sorted({names[c] for c in np.unique(code[lost]).tolist()})
        more = f" и ещё {len(met) - _NAMED}" if len(met) > _NAMED else ""
        journal.append(f"По домену: кодов нет в таблице доменов — {', '.join(f'«{c}»' for c in met[:_NAMED])}{more} "
                       f"(интервалов — {_count(report['not_listed'])}).")  # fmt: skip
    return Given(plane_normal(alpha, dipdir), SRC_DOMAIN, alpha, dipdir, index=which, journal=journal, report=report)


def _mesh(s: Any, rows: Intervals, inputs: Inputs, todo: np.ndarray, threads: int, tick: Tick | None) -> Given | None:
    if inputs.mesh is None:
        return None
    return inputs.mesh(rows, inputs.traj, s, todo=todo, threads=threads, tick=tick)


def _core(s: Any, rows: Intervals, inputs: Inputs, todo: np.ndarray, threads: int, tick: Tick | None) -> Given:
    t = inputs.table(s.table_id)
    place = rows.places(t.hole_db_ids)[t.hole_of()]  # the hole of every measurement among those of the intervals
    mine = place >= 0  # a measurement of a hole the intervals do not have is nobody's
    dense = rows.dense[place[mine]]
    depth = (0.5 * (np.asarray(t.from_m, dtype=np.float64) + np.asarray(t.to_m, dtype=np.float64)))[mine]
    meas = co.measurements(inputs.traj, dense, depth, np.asarray(_column(t, s.alpha_field))[mine],
                           np.asarray(_column(t, s.beta_field))[mine], line=s.line, threads=threads,
                           tick=_part(tick, 0.0, 0.4))  # fmt: skip
    o = co.assign(rows.keys(), rows.frm, rows.to, np.asarray(t.hole_ids)[mine], depth, meas, d_core=s.d_core,
                  threads=threads, tick=_part(tick, 0.4, 1.0))  # fmt: skip
    src = np.array([SRC_NONE, SRC_CORE_MEASURED, SRC_CORE_NEAREST], dtype=np.uint8)[o.kind]
    report = {"measurements": int(depth.shape[0]), "turned": meas.n_ok, "local": meas.n_local, "unused": meas.n_unused}
    journal = []
    if meas.n_unused:
        journal.append(f"Керн: замеров вне расчёта — {_count(meas.n_unused)} (нет глубины, α_к вне 0–90° или нет "
                       f"β_к).")  # fmt: skip
    if meas.n_local:
        journal.append(f"Керн: замеров, не пересчитанных в мировую нормаль, — {_count(meas.n_local)} (θ < 1° или нет "
                       f"траектории): дают TT = l·sin α_к только своему интервалу (TT_FLAG 4).")  # fmt: skip
    return Given(o.normal, src, sin_alpha=o.sin_alpha, flag=o.flag, journal=journal, report=report)


def _trend_minor(meta: Any) -> tuple[float, float]:
    """α, Aп of the plane of the global trend `meta`: the plane normal to its minor axis (the least ratio; of equal
    ones the later axis). With the third axis — `rotation.plane_of` of its angles, as the source `global` has them."""
    from bf_core.geostat import rotation
    from bf_core.geostat import trend as tr

    s = tr.shape(meta.get("ang1"), meta.get("ang2", 0.0), meta.get("ang3", 0.0), meta.get("ratios"))
    minor = max(range(3), key=lambda k: (-s.ratios[k], k))
    if minor == 2:
        alpha, dipdir, _pitch = rotation.plane_of(s.ang1, s.ang2, s.ang3)
        return alpha, dipdir
    r = rotation.rotation_matrix(s.ang1, s.ang2, s.ang3)
    n = r[minor]
    return _plane_angles(float(n[0]), float(n[1]), float(n[2]))


def _trend_source(s: Any, inputs: Inputs) -> Any:
    if inputs.trend is not None:
        return inputs.trend(s.trend_meta)
    from bf_core.composites.jobs_coding import reading_project
    from bf_core.geostat import trend_struct

    project = reading_project()
    if project is None:
        raise RuntimeError("a structural trend is read inside jobs_coding.reading(project)")
    return trend_struct.load_source(project, s.trend_meta)


def _trend(s: Any, rows: Intervals, inputs: Inputs, todo: np.ndarray, threads: int, tick: Tick | None) -> Given:
    meta = s.trend_meta
    if meta.get("kind", "global") != "structural":
        alpha, dipdir = _trend_minor(meta)
        return Given(plane_normal(alpha, dipdir), SRC_TREND, alpha, dipdir)
    from bf_core.composites import coords
    from bf_core.geostat import trend_struct

    at = np.flatnonzero(todo)
    normal = np.full((rows.n, 3), np.nan)
    if at.size == 0:
        return Given(normal, SRC_TREND)
    source = _trend_source(s, inputs)
    middle = 0.5 * (rows.frm[at] + rows.to[at])
    xyz = coords.positions(inputs.traj, rows.dense_rows()[at], middle, threads=threads, tick=_part(tick, 0.0, 0.3))
    placed = np.isfinite(xyz).all(axis=1)  # a point without a position gets no axes: NaN, never 0
    info: dict[str, Any] = {}
    axes = trend_struct.local_axes(meta, xyz[placed], source=source, threads=threads, tick=_part(tick, 0.3, 1.0),
                                   report=info)  # fmt: skip
    n = axes[:, 2, :]
    length = np.sqrt(np.einsum("ij,ij->i", n, n))
    with np.errstate(invalid="ignore", divide="ignore"):
        normal[at[placed]] = n / length[:, None]  # unit to the last bit: `_merge` takes 1e-9
    lost = int(np.count_nonzero(~placed))
    journal = list(info.get("journal") or [])
    if lost:
        journal.append(f"Тренд «{s.trend_name}»: интервалов без положения — {_count(lost)} (ориентировки нет).")
    report = {"trend": s.trend_name, "no_position": lost, "nearest": int(info.get("nearest") or 0)}
    return Given(normal, SRC_TREND, journal=journal, report=report)


_SOURCES: dict[str, Callable[..., Given | None]] = {"global": _global, "domain": _domain, "mesh": _mesh,
                                                    "core": _core, "trend": _trend}


# --- the chain -----------------------------------------------------------------------------------------------


@njit(cache=True, nogil=True)
def _plane_angles(x, y, z):  # type: ignore[no-untyped-def]
    """α and Aп, degrees, of a plane by a vector along its normal (any length and side) —
    `formulas.plane_angles` for one vector: the upper side, of a vertical plane Aп ∈ [0°; 180°), of a level
    one Aп = 0. NaN for a zero or missing vector."""
    scale = max(abs(x), abs(y), abs(z))
    if not (scale > 0.0 and scale < np.inf):
        return np.nan, np.nan
    x /= scale
    y /= scale
    z /= scale
    norm = math.sqrt(x * x + y * y + z * z)
    x /= norm
    y /= norm
    z /= norm
    upright = abs(z) <= VERTICAL
    if upright:
        west = x < 0.0 or (x == 0.0 and y < 0.0)
        south = (math.degrees(math.atan2(-x, -y)) if west else math.degrees(math.atan2(x, y))) >= 180.0
        flip = west != south
    else:
        flip = z < 0.0
    if flip:
        x = -x
        y = -y
    x += 0.0  # −0.0 → 0.0
    y += 0.0
    alpha = math.degrees(math.atan2(math.hypot(x, y), abs(z)))
    dipdir = math.degrees(math.atan2(x, y)) % 360.0
    if dipdir >= 360.0 or (upright and dipdir >= 180.0) or alpha < LEVEL_DEG:
        dipdir = 0.0
    return alpha, dipdir + 0.0


@njit(cache=True, nogil=True)
def _merge(todo, index, g_normal, g_src, g_alpha, g_dipdir, g_sin, g_flag, normal, src, alpha, dipdir, sin_alpha,
           flag, r0, r1):  # type: ignore[no-untyped-def]  # fmt: skip
    """The rows r0 ≤ r < r1 still without a plane (`todo`) take what a source gave them: a normal (by `index`,
    the one for all, or their own) with its angles (given, else by the normal) or sin α_к. An array of no
    elements — the source gave no such thing. Returns the rows taken and the rows whose normal is not a unit
    vector (they take nothing: the caller refuses the source)."""
    by_index = index.shape[0] > 0
    shared = g_normal.shape[0] == 1 and not by_index
    one_src = g_src.shape[0] == 1
    angles = g_alpha.shape[0] > 0
    has_sin = g_sin.shape[0] > 0
    has_flag = g_flag.shape[0] > 0
    taken = 0
    wrong = 0
    for r in range(r0, r1):
        if not todo[r]:
            continue
        q = r
        if by_index:
            q = index[r]
            if q < 0:
                continue
        elif shared:
            q = 0
        x = g_normal[q, 0]
        y = g_normal[q, 1]
        z = g_normal[q, 2]
        plane = math.isfinite(x) and math.isfinite(y) and math.isfinite(z)
        if plane and abs(x * x + y * y + z * z - 1.0) > UNIT_EPS:
            wrong += 1
            continue
        direct = has_sin and math.isfinite(g_sin[r])
        if not (plane or direct):
            continue
        if plane:
            normal[r, 0] = x
            normal[r, 1] = y
            normal[r, 2] = z
            if angles:
                alpha[r] = g_alpha[q]
                dipdir[r] = g_dipdir[q]
            else:
                alpha[r], dipdir[r] = _plane_angles(x, y, z)
        if direct:
            sin_alpha[r] = g_sin[r]
        src[r] = g_src[0] if one_src else g_src[r]
        if has_flag:
            flag[r] = g_flag[r]
        todo[r] = False
        taken += 1
    return taken, wrong


def _arrays(given: Given, n: int, kind: str) -> tuple[np.ndarray, ...]:
    """The arrays of a source as `_merge` reads them (no elements — not given); ValueError — they do not fit the
    rows."""

    def flat(x: Any, dtype: Any, size: int) -> np.ndarray:
        if x is None:
            return np.zeros(0, dtype=dtype)
        a = dr._plain(np.asarray(x, dtype=dtype).reshape(-1), dtype)
        if a.shape[0] != size:
            raise ValueError
        return a

    try:
        normal = dr._plain(np.asarray(given.normal, dtype=np.float64).reshape(-1, 3), np.float64)
        k = normal.shape[0]
        index = flat(given.index, np.int64, n)
        if (given.index is None and k not in (1, n)) or (index.shape[0] and int(index.max()) >= k):
            raise ValueError
        angles = given.alpha is not None and given.dipdir is not None
        alpha, dipdir = (
            flat(given.alpha if angles else None, np.float64, k),
            flat(given.dipdir if angles else None, np.float64, k),
        )
        src = dr._plain(np.asarray(given.src, dtype=np.uint8).reshape(-1), np.uint8)
        if src.shape[0] not in (1, n) or int(src.max(initial=0)) >= len(SRC_LABELS):
            raise ValueError
        return index, normal, src, alpha, dipdir, flat(given.sin_alpha, np.float64, n), flat(given.flag, np.uint8, n)
    except (TypeError, ValueError):
        raise ValueError(f"orientation: the source {kind} gave arrays that do not fit the {n} intervals") from None


def _take(given: Given, kind: str, todo: np.ndarray, out: tuple[np.ndarray, ...], threads: int) -> int:
    """The rows of `todo` take what the source gave (`_merge` over strips of rows); returns how many did."""
    n = todo.shape[0]
    index, g_normal, g_src, g_alpha, g_dipdir, g_sin, g_flag = _arrays(given, n, kind)
    normal, src, alpha, dipdir, sin, flag = out

    def strip(lo: int, hi: int) -> tuple[int, int]:
        return _merge(todo, index, g_normal, g_src, g_alpha, g_dipdir, g_sin, g_flag, normal, src, alpha, dipdir,
                      sin, flag, lo, hi)  # type: ignore[no-any-return]  # fmt: skip

    taken = strips.run(strip, n, threads=threads, size=dr.STRIP_ROWS)
    if any(wrong for _n, wrong in taken):
        raise ValueError(f"orientation: the source {kind} gave normals that are not unit vectors")
    return int(sum(got for got, _w in taken))


def resolve(orientation: Sequence[Source], rows: Intervals, inputs: Inputs, *, threads: int = 1,
            tick: Tick | None = None) -> Orientation:  # fmt: skip
    """The plane of every interval by the chain of sources (module docstring)."""
    n = rows.n
    normal = np.full((n, 3), np.nan)
    alpha, dipdir = np.full(n, np.nan), np.full(n, np.nan)
    src, flag = np.zeros(n, dtype=np.uint8), np.zeros(n, dtype=np.uint8)
    sin_alpha: np.ndarray | None = None  # [n] once a source gives sin α_к
    todo = rows.valid.copy()
    absent: list[str] = []
    journal: list[str] = []
    report: list[dict[str, Any]] = []
    for k, s in enumerate(orientation):
        share = _part(tick, k / len(orientation), (k + 1) / len(orientation))
        idle = not todo.any() and not (s.kind == "mesh" and inputs.mesh is None)  # nobody is left to ask about
        given = None if idle else _SOURCES[s.kind](s, rows, inputs, todo, threads, share)
        if idle:
            report.append({"source": s.kind, "rows": 0})
        elif given is None:
            absent.append(s.kind)
            journal.append(f"Источник ориентировки «{KIND_TITLES[s.kind]}» недоступен: по нему ничего не считалось.")
            report.append({"source": s.kind, "rows": 0, "absent": True})
        else:
            if given.sin_alpha is not None and sin_alpha is None:
                sin_alpha = np.full(n, np.nan)
            sin = sin_alpha if sin_alpha is not None else np.zeros(0)
            taken = _take(given, s.kind, todo, (normal, src, alpha, dipdir, sin, flag), threads)
            journal += given.journal
            report.append({"source": s.kind, "rows": taken, **given.report})
        if share is not None:
            share(1.0)
    return Orientation(normal, alpha, dipdir, src, sin_alpha, flag, rows.valid, tuple(absent), journal, report)
