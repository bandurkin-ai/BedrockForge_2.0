"""Audit checks of a drillhole set (ТЗ 7.12; ported from BF `app/audit/checks.py`). Pure numpy, no database.

Holes are addressed by a dense index 0 … H-1 (`AuditData.holes` keeps the names); collar values are arrays
per hole, surveys and samples are flat arrays with the hole index of every row (a negative index: a row
without a hole — an orphan with the hole `None`). Rows are taken in file order, i.e. by ascending `row_id`
(they are reordered when given otherwise), so the result does not depend on the order of the arrays.
A missing value is NaN (None for texts) and never becomes zero: comparisons with it are false.
BF grouped rows per hole with pandas; here every rule is a mask over rows sorted once by hole and depth,
so 50 000 holes / 5 million samples take seconds, and memory is a few dozen bytes per row on top of the data.

`run_checks` returns for every rule (also the passed ones) the number of offending rows and of holes, and
the findings grouped by rule and hole with up to MAX_EXAMPLES example rows. Example values are plain Python
(a missing or non-finite number is None), ready for JSON; their keys are named in `rules.EXAMPLE_FIELDS`.
numba compiles the only per-row loop on first use (`bf_core.jit`): importing this module does not load it.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, fields
from functools import cache, partial
from typing import Any, NamedTuple

import numpy as np

from .rules import RULES, SEVERITIES

MAX_EXAMPLES = 20  # example rows kept per finding (rule × hole)

# progress(fraction 0…1 or None, message or None); it may raise (JobCancelled) to cancel the audit.
Tick = Callable[[float | None, str | None], None]
Fields = Callable[[np.ndarray], dict[str, list[Any]]]  # positions of example rows → their columns

_NEIGHBOURS = 5  # nearest collars whose median Z a collar is compared with
_MISMATCH_DIP, _MISMATCH_AZIMUTH, _INCLINED = 10.0, 30.0, 85.0  # first station against the collar, degrees
_CHUNK = 1 << 19  # rows between two ticks in Python-level loops (a few hundredths of a second)
_GROUPS = 1 << 13  # findings between two ticks while the examples are built
_BLOCK = 1 << 12  # rows sorted at once (whole holes): fits the processor cache, hole numbers fit 16 bits
_TEXT_CHUNK = 1 << 15  # sample numbers hashed at once: the strings stay in the processor cache between passes
_TEXTS = {str, type(None)}

_THRESHOLDS = {  # name: (title for the user, zero allowed)
    "depth_tolerance": ("Допуск по глубине, м", True),
    "dogleg_per_30m": ("Изгиб, ° на 30 м", False),
    "isolated_factor": ("Изоляция устья, × медианы", False),
    "z_jump": ("Скачок Z, м", False),
    "assay_outlier_z": ("Выброс, робастных σ", False),
}


def _number(value: Any, title: str, zero_allowed: bool) -> float:
    try:
        number = float(value.strip().replace(",", ".") if isinstance(value, str) else value)
        ok = not isinstance(value, bool) and math.isfinite(number) and (number > 0 or (number == 0 and zero_allowed))
    except (TypeError, ValueError):
        ok = False
    if not ok:
        need = "не меньше нуля" if zero_allowed else "больше нуля"
        raise ValueError(f"Порог «{title}»: нужно число {need}, получено «{value}».")
    return number


def _markers(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = value.replace(";", ",").replace("\n", ",").split(",")
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise ValueError("Признаки плановой скважины: нужен список слов, например «проект, план».")
    return tuple(m for m in dict.fromkeys(v.strip().lower() for v in value) if m)


@dataclass(frozen=True)
class AuditConfig:
    """Thresholds of the audit (ТЗ 7.12 «Пороги»); a wrong value is a ValueError with a Russian message."""

    depth_tolerance: float = 0.01  # m: gaps, overlaps, comparison with TD
    dogleg_per_30m: float = 10.0  # degrees per 30 m
    isolated_factor: float = 10.0  # nearest-neighbour distance against the median one
    z_jump: float = 50.0  # m, against the median Z of the nearest collars
    assay_outlier_z: float = 8.0  # robust sigmas of log10(value)
    planned_markers: tuple[str, ...] = ("project", "planned", "plan", "проект", "план")  # statuses, lower case

    def __post_init__(self) -> None:
        for name, (title, zero_allowed) in _THRESHOLDS.items():
            object.__setattr__(self, name, _number(getattr(self, name), title, zero_allowed))
        object.__setattr__(self, "planned_markers", _markers(self.planned_markers))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> AuditConfig:
        """Thresholds from stored or sent settings: missing keys are defaults, unknown keys are ignored."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (raw or {}).items() if k in known})

    def as_dict(self) -> dict[str, Any]:
        return {**{name: getattr(self, name) for name in _THRESHOLDS}, "planned_markers": list(self.planned_markers)}


@dataclass
class AuditData:
    """A drillhole set as arrays. Numbers are float64 with NaN for a missing value; texts are None when missing."""

    holes: np.ndarray  # object: names; the index is the dense hole id 0 … H-1
    has_collar: np.ndarray  # bool per hole: False for a hole known only from surveys or samples
    x: np.ndarray  # per hole, as y, z, td, azimuth, dip
    y: np.ndarray
    z: np.ndarray
    td: np.ndarray
    azimuth: np.ndarray
    dip: np.ndarray
    status: np.ndarray  # object per hole
    survey_row_id: np.ndarray  # int64; file order is ascending id
    survey_hole: np.ndarray  # dense hole id per row; negative: no hole
    survey_depth: np.ndarray
    survey_azimuth: np.ndarray
    survey_dip: np.ndarray
    assay_row_id: np.ndarray
    assay_hole: np.ndarray
    assay_from: np.ndarray
    assay_to: np.ndarray
    assay_sample: np.ndarray | None = None  # object per row; None: the table has no sample numbers
    components: dict[str, np.ndarray] = field(default_factory=dict)  # name → value per sample row


@dataclass
class AuditResult:
    """`summary`: one dict per rule in the order of `RULES` — code, severity, table, title, description,
    rows (offending rows; for the grade rules — values), holes (holes with the finding).
    `findings`: one dict per rule and hole — code, severity, table, hole (name or None), count (rows),
    examples (up to MAX_EXAMPLES dicts) — sorted by severity (errors first), table, code and hole name."""

    summary: list[dict[str, Any]]
    findings: list[dict[str, Any]]


# --- findings and their examples ------------------------------------------------------------------


def _text(value: Any) -> str:
    """A status or a sample number as BF compares them: missing is empty, spaces around do not count."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    return str(value).strip()


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    return str(value)


def _plain(values: np.ndarray | None, count: int) -> list[Any]:
    """Values of an example column for JSON: a missing or non-finite number is None."""
    if values is None:
        return [None] * count
    out = values.tolist()
    if values.dtype.kind == "f":
        return out if np.isfinite(values).all() else [v if math.isfinite(v) else None for v in out]
    return out if values.dtype.kind != "O" or set(map(type, out)) <= _TEXTS else [_cell(v) for v in out]


def _column(values: Any, count: int, name: str, dtype: Any = np.float64) -> np.ndarray:
    out = np.ascontiguousarray(values, dtype=dtype)
    if out.shape != (count,):
        raise ValueError(f"Аудит: массив «{name}» имеет размер {out.shape}, ожидалось {count} значений.")
    return out


class _Run:
    """Per-hole values, the findings collected so far and the progress of one audit."""

    def __init__(self, data: AuditData, tick: Tick | None) -> None:
        n = len(data.holes)
        names: list[str | None] = [None if v is None else str(v) for v in np.asarray(data.holes, dtype=object).tolist()]
        names.append(None)  # slot n: the rows without a hole
        order = sorted(range(n + 1), key=lambda i: names[i] or "")
        self.n_holes = n
        self.by_rank = [names[i] for i in order]
        self.rank = np.empty(n + 1, dtype=np.int64)  # place of a hole in the list sorted by name
        self.rank[order] = np.arange(n + 1)
        self.has_collar = np.append(_column(data.has_collar, n, "has_collar", bool), False)
        self.collar: dict[str, np.ndarray] = {}  # no collar: no values, whatever the arrays hold
        for k in ("x", "y", "z", "td", "azimuth", "dip"):
            values = np.where(self.has_collar[:n], _column(getattr(data, k), n, k), np.nan)
            self.collar[k] = np.append(values, np.nan)
        self.status = _column(data.status, n, "status", object)
        self.rows = dict.fromkeys(RULES, 0)
        self.holes = dict.fromkeys(RULES, 0)
        self.found: dict[str, list[dict[str, Any]]] = {code: [] for code in RULES}
        self._tick, self._fraction = tick, 0.0
        self._message: str | None = None

    def order(self, hole: np.ndarray) -> np.ndarray:
        """Stable order by hole number in linear time: numpy radix-sorts 16-bit keys, so by 16 bits at a time."""
        order = np.argsort(hole.astype(np.uint16), kind="stable")
        for shift in range(16, int(self.n_holes).bit_length(), 16):
            self.tick()
            order = order[np.argsort((hole >> shift).astype(np.uint16)[order], kind="stable")]
        self.tick()
        return order

    def at(self, fraction: float, message: str | None = None) -> None:
        self._fraction = fraction
        self._message = message or self._message
        self.tick()

    def tick(self) -> None:
        if self._tick is not None:
            self._tick(self._fraction, self._message)

    def table(
        self, name: str, row_id: Any, hole: Any, columns: Mapping[Any, Any]
    ) -> tuple[np.ndarray, np.ndarray, dict[Any, np.ndarray]]:
        """Rows of a table in file order (ascending id) with checked sizes; rows without a hole get the slot H."""
        ids = np.ascontiguousarray(row_id, dtype=np.int64).reshape(-1)
        n = int(ids.size)
        holes = _column(hole, n, f"{name}_hole", np.int64)
        if n and int(holes.max()) >= self.n_holes:
            raise ValueError(f"Аудит: в «{name}_hole» есть номер скважины вне списка из {self.n_holes} скважин.")
        holes = np.where(holes < 0, self.n_holes, holes)
        cols = {
            k: _column(v, n, k if isinstance(k, str) else f"components[{k}]", object if k == "assay_sample" else float)
            for k, v in columns.items()
        }
        if n > 1 and bool((ids[1:] < ids[:-1]).any()):
            order = np.argsort(ids, kind="stable")
            self.tick()
            ids, holes = ids[order], holes[order]
            for k, v in cols.items():
                cols[k] = v[order]
                self.tick()
        return ids, holes, cols

    def emit(self, code: str, hole: np.ndarray, examples: Fields | None = None) -> None:
        """Findings of a rule: `hole` — the hole of every offending row in the order of the examples;
        `examples(positions)` — the example columns of the rows at these positions of `hole`."""
        n = int(hole.size)
        if n == 0:
            return
        key = self.rank[hole]
        pos = None
        if n > 1 and bool((key[1:] < key[:-1]).any()):
            pos = self.order(key)
            key = key[pos]
        first = np.flatnonzero(np.concatenate(([True], key[1:] != key[:-1])))
        counts = np.diff(np.append(first, n))
        self.rows[code] += n
        self.holes[code] += int(first.size)
        rule, out = RULES[code], self.found[code]
        head = {"code": code, "severity": rule.severity, "table": rule.table}
        names = [self.by_rank[k] for k in key[first].tolist()]
        for a in range(0, first.size, _GROUPS):
            b = min(a + _GROUPS, first.size)
            shown = np.minimum(counts[a:b], MAX_EXAMPLES if examples is not None else 0)
            ends = np.cumsum(shown)
            rows: list[dict[str, Any]] = []
            if examples is not None:  # the first rows of every hole
                sel = np.arange(int(ends[-1])) + np.repeat(first[a:b] - (ends - shown), shown)
                columns = iter(examples(sel if pos is None else pos[sel]).items())
                k, values = next(columns)
                rows = [{k: v} for v in values]
                for k, values in columns:  # column by column: twice as fast as a dict per row from a zip
                    for row, v in zip(rows, values, strict=True):
                        row[k] = v
            for name, count, m, e in zip(names[a:b], counts[a:b].tolist(), shown.tolist(), ends.tolist(), strict=True):
                out.append({**head, "hole": name, "count": count, "examples": rows[e - m : e]})
            self.tick()

    def result(self) -> AuditResult:
        summary = [
            {**asdict(r), "rows": self.rows[r.code], "holes": self.holes[r.code]} for r in RULES.values()
        ]
        order = sorted(RULES.values(), key=lambda r: (SEVERITIES.index(r.severity), r.table, r.code))
        return AuditResult(summary, [f for r in order for f in self.found[r.code]])


# An example column aligned with the reported rows (not with the rows of the table).
_Own = NamedTuple("_Own", [("values", np.ndarray)])


def _flag(run: _Run, hole: np.ndarray, code: str, rows: np.ndarray, columns: Mapping[str, Any] | None = None) -> None:
    """Reports the rows (a mask or indices) of a table whose rows belong to the holes `hole`; `columns` are
    the example columns: arrays of the whole table, `_Own` ones, or None for a column the table lacks."""
    rows = np.flatnonzero(rows) if rows.dtype == bool else rows

    def take(sel: np.ndarray) -> dict[str, list[Any]]:
        at = rows[sel]
        return {
            k: _plain(v.values[sel] if isinstance(v, _Own) else None if v is None else v[at], sel.size)
            for k, v in (columns or {}).items()
        }

    run.emit(code, hole[rows], None if columns is None else take)


# --- collars --------------------------------------------------------------------------------------


def _neighbours(x: np.ndarray, y: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Distance to the nearest other point and the indices of the k nearest other points (k < len(x))."""
    from scipy.spatial import cKDTree

    points = np.column_stack((x, y))
    _, idx = cKDTree(points).query(points, k=k + 1)
    me = idx == np.arange(x.size)[:, None]
    me[~me.any(axis=1), -1] = True  # more than k twins at one point: the point itself is not among them
    near = idx[~me].reshape(x.size, k)
    distance = np.hypot(x[:, None] - x[near], y[:, None] - y[near])
    return distance.min(axis=1), near


def _bad_angles(azimuth: np.ndarray, dip: np.ndarray) -> np.ndarray:
    return np.asarray((azimuth < 0) | (azimuth > 360) | (dip < -90) | (dip > 90))  # false for a missing angle


def _row_median(values: np.ndarray) -> np.ndarray:
    """Median of every row without its NaN (NaN for a row of NaN): `np.nanmedian(values, axis=1)`."""
    ordered = np.sort(values, axis=1)  # NaN go last
    count = (~np.isnan(ordered)).sum(axis=1)
    rows = np.arange(values.shape[0])
    return np.asarray(0.5 * (ordered[rows, np.maximum(count - 1, 0) // 2] + ordered[rows, count // 2]))


def _check_collar(run: _Run, cfg: AuditConfig, with_survey: np.ndarray, with_assay: np.ndarray) -> None:
    hc = np.flatnonzero(run.has_collar)  # the collar table: holes with a collar
    x, y, z, td, azimuth, dip = (run.collar[k][hc] for k in ("x", "y", "z", "td", "azimuth", "dip"))
    flag = partial(_flag, run, hc)

    flag("COLLAR_MISSING_COORDS", np.isnan(x) | np.isnan(y) | np.isnan(z), {"x": x, "y": y, "z": z})
    flag("COLLAR_INVALID_TD", np.isnan(td) | (td <= 0), {"td": td})
    flag("COLLAR_ANGLE_RANGE", _bad_angles(azimuth, dip), {"azimuth": azimuth, "dip": dip})

    located = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    if located.size >= 3:
        nearest, near = _neighbours(x[located], y[located], min(_NEIGHBOURS, located.size - 1))
        far = np.flatnonzero(nearest > cfg.isolated_factor * float(np.median(nearest)))
        flag("COLLAR_ISOLATED", located[far], {"x": x, "y": y, "nn_distance": _Own(nearest[far])})
        around = _row_median(z[located][near])
        jump = np.flatnonzero(np.abs(z[located] - around) > cfg.z_jump)
        flag("COLLAR_Z_JUMP", located[jump], {"z": z, "neighbour_z": _Own(around[jump])})

    raw = run.status[hc]
    status = [_text(v) for v in raw.tolist()]
    markers = set(cfg.planned_markers)
    planned = np.fromiter((s.lower() in markers for s in status), dtype=bool, count=len(status))
    flag("COLLAR_PLANNED_WITH_DATA", planned & (with_survey | with_assay)[hc], {"status": raw})
    flag("COLLAR_EMPTY_STATUS", np.fromiter((s == "" for s in status), dtype=bool, count=len(status)))
    flag("COLLAR_WITHOUT_SURVEY", ~with_survey[hc])
    flag("COLLAR_WITHOUT_ASSAY", ~with_assay[hc])


# --- surveys --------------------------------------------------------------------------------------


def _check_survey(run: _Run, cfg: AuditConfig, row_id: np.ndarray, hole: np.ndarray, c: dict[Any, np.ndarray]) -> None:
    depth, azimuth, dip = c["survey_depth"], c["survey_azimuth"], c["survey_dip"]
    n, tol = int(hole.size), cfg.depth_tolerance
    if n == 0:
        return
    base = {"row_id": row_id, "depth": depth}
    angles = {**base, "azimuth": azimuth, "dip": dip}
    flag = partial(_flag, run, hole)

    flag("SURVEY_ORPHAN_HOLE", ~run.has_collar[hole], base)
    empty = np.isnan(depth) | np.isnan(azimuth) | np.isnan(dip)
    flag("SURVEY_EMPTY_VALUES", empty, angles)
    bad = _bad_angles(azimuth, dip)
    flag("SURVEY_ANGLE_RANGE", bad, angles)
    td = run.collar["td"][hole]
    flag("SURVEY_BEYOND_TD", depth > td + tol, {**base, "td": td})
    run.tick()

    order = np.lexsort((depth, hole))  # by hole, then depth (NaN last), then file order
    hs, ds = hole[order], depth[order]
    start = np.concatenate(([True], hs[1:] != hs[:-1]))
    same = ~start[1:] & (ds[1:] == ds[:-1])
    twin = np.concatenate(([False], same)) | np.concatenate((same, [False]))
    flag("SURVEY_DUPLICATE_DEPTH", np.sort(order[twin]), base)

    by_file = run.order(hole)  # by hole, then file order
    hf, df = hole[by_file], depth[by_file]
    flag("SURVEY_NOT_ASCENDING", by_file[1:][(hf[1:] == hf[:-1]) & (df[1:] < df[:-1])], base)
    run.tick()

    first = order[start & ~np.isnan(ds)]  # the shallowest station of every hole
    flag("SURVEY_FIRST_NOT_ZERO", first[depth[first] > tol], base)

    # Out-of-range angles are reported by SURVEY_ANGLE_RANGE; do not add dogleg noise on top.
    g = order[~(bad | empty)[order]]
    ra, rd = np.radians(azimuth[g]), np.radians(dip[g])
    vx, vy, vz = np.cos(rd) * np.sin(ra), np.cos(rd) * np.cos(ra), np.sin(rd)
    dot = vx[1:] * vx[:-1] + vy[1:] * vy[:-1] + vz[1:] * vz[:-1]
    angle = np.degrees(np.arccos(np.clip(dot, -1.0, 1.0)))
    dz = depth[g][1:] - depth[g][:-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        dls = np.where((hole[g][1:] == hole[g][:-1]) & (dz > tol), angle / dz * 30.0, np.nan)
    bend = np.flatnonzero(dls > cfg.dogleg_per_30m)
    flag("SURVEY_DOGLEG", g[bend + 1], {**base, "dls": _Own(dls[bend])})
    run.tick()

    collar_azimuth, collar_dip = run.collar["azimuth"][hole[first]], run.collar["dip"][hole[first]]
    az, inc = azimuth[first], dip[first]
    with np.errstate(invalid="ignore"):
        turn = np.abs((az - collar_azimuth + 180.0) % 360.0 - 180.0)
        differs = (np.abs(inc - collar_dip) > _MISMATCH_DIP) | ((np.abs(inc) < _INCLINED) & (turn > _MISMATCH_AZIMUTH))
    wrong = np.flatnonzero(differs & ~(np.isnan(az) | np.isnan(inc) | np.isnan(collar_dip)))
    own = {"dip": inc, "collar_dip": collar_dip, "azimuth": az, "collar_azimuth": collar_azimuth}
    flag("SURVEY_COLLAR_MISMATCH", first[wrong], {**base, **{k: _Own(v[wrong]) for k, v in own.items()}})


# --- samples --------------------------------------------------------------------------------------



def _reach_before(hole: np.ndarray, end: np.ndarray) -> np.ndarray:
    """For rows sorted by hole and start: the deepest end of the earlier rows of the hole (NaN for the first)."""
    out = np.empty(end.size)
    reach = 0.0
    for i in range(end.size):
        if i > 0 and hole[i] == hole[i - 1]:
            out[i] = reach
            if end[i] > reach:
                reach = end[i]
        else:
            out[i] = np.nan
            reach = end[i]
    return out


@cache
def _kernel(fn: Callable[..., Any]) -> Callable[..., Any]:
    """The function compiled by numba (cached on disk); numba is loaded on first use."""
    from bf_core.jit import njit

    return njit(cache=True, nogil=True)(fn)  # type: ignore[no-any-return]


def _sort_intervals(
    run: _Run, rows: np.ndarray, hole: np.ndarray, start: np.ndarray, end: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The intervals in the stable order by hole, start, end (as given when a usual file is already in it).
    The rows are grouped by hole, then sorted in blocks of whole holes: every sort is short and works in the
    processor cache (three times faster than `np.lexsort`), and the progress is reported in between."""
    n = int(rows.size)
    if n < 2:
        return rows, hole, start, end
    later = (start[1:] > start[:-1]) | ((start[1:] == start[:-1]) & (end[1:] >= end[:-1]))
    if bool(((hole[1:] > hole[:-1]) | ((hole[1:] == hole[:-1]) & later)).all()):
        return rows, hole, start, end
    del later
    order = run.order(hole)
    rows, hole, start, end = rows[order], hole[order], start[order], end[order]
    del order
    run.tick()
    new = np.concatenate(([False], hole[1:] != hole[:-1]))
    cuts = np.flatnonzero(new)  # first rows of the holes
    at = np.searchsorted(cuts, np.arange(_BLOCK, n, _BLOCK))
    bounds = np.unique(np.concatenate(([0], cuts[at[at < cuts.size]], [n]))).tolist()
    for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:], strict=True)):
        by = np.argsort(end[a:b], kind="stable")
        if i % 64 == 63 or b - a > _CHUNK:  # a block of one very long hole
            run.tick()
        by = by[np.argsort(start[a:b][by], kind="stable")]
        local = np.cumsum(new[a:b]).astype(np.uint16)  # holes of the block numbered from its first one
        by = by[np.argsort(local[by], kind="stable")]
        rows[a:b], start[a:b], end[a:b] = rows[a:b][by], start[a:b][by], end[a:b][by]
    return rows, hole, start, end


def _duplicate_samples(run: _Run, sample: np.ndarray) -> np.ndarray:
    """Rows whose sample number (non-empty, spaces around ignored) occurs in the table more than once."""
    found: list[np.ndarray] = []
    codes: list[np.ndarray] = []
    for a in range(0, int(sample.size), _TEXT_CHUNK):
        chunk = sample[a : a + _TEXT_CHUNK]
        texts: Any = chunk.tolist()
        if set(map(type, texts)) <= _TEXTS:  # the usual column: no Python-level loop
            rows = np.flatnonzero(chunk.astype(bool))
            texts = map(str.strip, filter(None, texts))
        else:
            rows, texts = np.arange(chunk.size), map(_text, texts)
        codes.append(np.fromiter(map(hash, texts), dtype=np.int64, count=rows.size))
        found.append(rows + a)
        if a % _CHUNK == 0:
            run.tick()
    rows, code = np.concatenate(found), np.concatenate(codes)
    ordered = np.sort(code)
    run.tick()
    twice = np.unique(ordered[1:][ordered[1:] == ordered[:-1]])
    if twice.size == 0:
        return rows[:0]
    table = np.zeros(1 << 20, dtype=bool)  # a quick filter before the exact search
    table[twice & 0xFFFFF] = True
    maybe = np.flatnonzero(table[code & 0xFFFFF])
    at = np.minimum(np.searchsorted(twice, code[maybe]), twice.size - 1)
    rows = rows[maybe[twice[at] == code[maybe]]]  # equal hashes: equal numbers and (hardly ever) collisions
    seen: Counter[str] = Counter()
    numbers: list[list[str]] = []
    for a in range(0, int(rows.size), _CHUNK):
        numbers.append([_text(v) for v in sample[rows[a : a + _CHUNK]].tolist()])
        seen.update(numbers[-1])
        run.tick()
    keep: list[bool] = []
    for part in numbers:
        keep += [t != "" and seen[t] > 1 for t in part]
        run.tick()
    return rows[np.array(keep, dtype=bool)]


def _outliers(values: np.ndarray, threshold: float) -> np.ndarray:
    """Rows far above the usual level: robust z (median, MAD) of log10 of the positive values."""
    positive = np.flatnonzero(values > 0)
    if positive.size == 0:
        return positive
    with np.errstate(invalid="ignore"):
        logs = np.log10(values[positive])
        logs -= np.median(logs)
        mad = float(np.median(np.abs(logs)))
        return positive[logs / (1.4826 * mad) > threshold] if mad > 0 else positive[:0]


def _check_assay(
    run: _Run, cfg: AuditConfig, row_id: np.ndarray, hole: np.ndarray, c: dict[Any, np.ndarray], names: list[str]
) -> None:
    """`c`: the columns «assay_from», «assay_to», «assay_sample» (if any) and the components by their number."""
    start, end, sample = c["assay_from"], c["assay_to"], c.get("assay_sample")
    n, tol = int(hole.size), cfg.depth_tolerance
    if n == 0:
        return
    base = {"row_id": row_id, "sample": sample, "from": start, "to": end}
    flag = partial(_flag, run, hole)

    flag("ASSAY_ORPHAN_HOLE", ~run.has_collar[hole], {"row_id": row_id, "from": start, "to": end})
    invalid = np.isnan(start) | np.isnan(end) | (start < 0) | (start >= end)
    flag("ASSAY_INVALID_INTERVAL", invalid, base)
    td = run.collar["td"][hole]
    flag("ASSAY_BEYOND_TD", end > td + tol, {**base, "td": td})
    run.at(0.3)

    rows = np.flatnonzero(~invalid)
    rows, h, f, t = _sort_intervals(run, rows, hole[rows], start[rows], end[rows])
    run.at(0.45)
    reach = _kernel(_reach_before)(h, t)
    for code, mask in (("ASSAY_OVERLAP", f < reach - tol), ("ASSAY_GAP", f > reach + tol)):
        hit = np.flatnonzero(mask)
        flag(code, rows[hit], {**base, "prev_to": _Own(reach[hit])})
    del rows, h, f, t, reach

    if sample is not None:
        run.at(0.5, "Аудит: номера проб")
        flag("ASSAY_DUPLICATE_SAMPLE", _duplicate_samples(run, sample), base)

    run.at(0.65, "Аудит: содержания")
    codes = ("ASSAY_NEGATIVE_VALUE", "ASSAY_EMPTY_VALUE", "ASSAY_OUTLIER")
    found: dict[str, list[tuple[np.ndarray, int]]] = {code: [] for code in codes}
    for j in range(len(names)):
        values = c[j]
        hits = (np.flatnonzero(values < 0), np.flatnonzero(np.isnan(values)), _outliers(values, cfg.assay_outlier_z))
        for code, hit in zip(codes, hits, strict=True):
            if hit.size:
                found[code].append((hit, j))
        run.at(0.65 + 0.25 * (j + 1) / len(names))
    for code, parts in found.items():  # component by component, as BF's long table
        if parts:
            columns: dict[str, Any] = {"row_id": row_id}
            columns["component"] = _Own(np.concatenate([np.full(hit.size, names[j], dtype=object) for hit, j in parts]))
            if code != "ASSAY_EMPTY_VALUE":
                columns["value"] = _Own(np.concatenate([c[j][hit] for hit, j in parts]))
            flag(code, np.concatenate([hit for hit, _ in parts]), {**columns, "sample": sample, "from": start})


# --- the audit ------------------------------------------------------------------------------------


def run_checks(data: AuditData, config: AuditConfig | None = None, tick: Tick | None = None) -> AuditResult:
    """All 27 rules over a set. `tick(fraction, message)` is called at least about once a second; it may raise
    to cancel. Wrong array sizes are a ValueError."""
    cfg = config or AuditConfig()
    run = _Run(data, tick)
    run.at(0.0, "Аудит: подготовка данных")
    survey = {k: getattr(data, k) for k in ("survey_depth", "survey_azimuth", "survey_dip")}
    s_id, s_hole, s_cols = run.table("survey", data.survey_row_id, data.survey_hole, survey)
    assay: dict[Any, Any] = dict(enumerate(data.components.values()))
    assay.update(assay_from=data.assay_from, assay_to=data.assay_to)
    if data.assay_sample is not None:
        assay["assay_sample"] = data.assay_sample
    a_id, a_hole, a_cols = run.table("assay", data.assay_row_id, data.assay_hole, assay)

    run.at(0.02, "Аудит: устья")
    slots = run.n_holes + 1
    _check_collar(run, cfg, np.bincount(s_hole, minlength=slots) > 0, np.bincount(a_hole, minlength=slots) > 0)
    run.at(0.06, "Аудит: инклинометрия")
    _check_survey(run, cfg, s_id, s_hole, s_cols)
    run.at(0.2, "Аудит: интервалы проб")
    _check_assay(run, cfg, a_id, a_hole, a_cols, [str(k) for k in data.components])
    run.at(0.95, "Аудит: сбор результатов")
    result = run.result()
    run.at(1.0)
    return result
