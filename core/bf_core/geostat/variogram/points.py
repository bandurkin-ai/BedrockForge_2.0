"""The points of a variography calculation (ТЗ 8.6.1, 8.0.1; ADR 0022 §2, §4): the middles of the intervals of one
table of a drillhole set with a value, the domain, the weights and the normal scores.

    p = load(project, spec.data, tick=tick)      # Points; ApiError 422 `no_values`, `too_few_values`, `no_spread`
    p.xyz, p.values                             # float64 [n, 3] (the origin subtracted), [n]: the variable
    q, sampled = sample(p, n_new, seed)         # n_new points without replacement (PCG64(seed)), the order kept
    n_new = sample_size(p.n, estimate)          # ⌊n·√(2·10⁹ / estimate)⌋ — the pairs above PAIRS_MAX (§4)
    defaults(project, data)                     # the answer of `GET /variography/defaults` (e16-api.md)

A point is an interval of `stats/source.read` (rows in line order: hole, FROM, TO) with a finite value and a
position — the middle by depth on its hole path (`stats/source.midpoints`); a row without a value, without FROM /
TO or a trajectory, or outside the chosen values of the domain does not take part and is counted, by its first
reason in that order: `counts {rows, missing, no_position, outside_domain, used, no_weight}` — a gap is never a 0.
The domain is a text or numeric field of the same table or of another interval table (the interval of that table
the middle of the sample lies in, `domain_at`); a number is compared by `format(x, ".15g")` (as `spec` writes it).

The weights (by default DECL_W) only build the table of the normal scores (and are not in the sums of the pairs,
as GSLIB): a point whose weight is missing or not positive stays in the pairs, is not a row of the table and
reads its score from it (`no_weight`). The variable of the calculation is the value (`transform: none`) or its
normal score (`nscore`, `stats/transforms.nscore` with the weights). `sigma2` — its variance with the divisor n
(BF `v.var()`), 1 for the normal scores; `sigma2_z` — that of the values. The origin — the centre of the extent of
the points — is subtracted before any calculation (8.0.1); `origin` gives it back.

`inputs` — what «устарел» compares (ADR 0022 §12): the tables read {id, name_key, token, rev} and the key of the
trajectories of the set (`drillholes.trajectory`: its version and the revisions of its tables).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from bf_core.api.errors import ApiError

from . import spec

Tick = Callable[[float | None, str | None], None]

STAGE_READ = "Чтение таблицы"
STAGE_TRAJ = "Траектории"
STAGE_NSCORE = "Нормальные оценки"


@dataclass(frozen=True)
class Points:
    xyz: np.ndarray  # float64 [n, 3]: X, Y, Z of the middles less `origin`
    origin: np.ndarray  # float64 [3]
    values: np.ndarray  # float64 [n]: the variable of the calculation (z or its normal score)
    z: np.ndarray  # float64 [n]: the values of the field
    weights: np.ndarray | None  # float64 [n], NaN — no weight; None — no weight field
    hole: np.ndarray  # int32 [n]: dense hole index (into `holes`), ascending
    holes: list[str]
    depth: np.ndarray  # float64 [n]: the middle (FROM + TO) / 2 along the hole
    length: np.ndarray  # float64 [n]: TO − FROM
    row_ids: np.ndarray  # int64 [n]: dh_rows.id
    counts: dict[str, int]
    sigma2: float
    sigma2_z: float
    median_length: float
    transform: Any = None  # stats.transforms.NormalScoreTransform of `nscore`
    inputs: dict[str, Any] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return int(self.values.shape[0])

    def subset(self, rows: np.ndarray) -> Points:
        """The points `rows` (ascending positions): the variance, the table and the counts stay those of all."""
        w = None if self.weights is None else self.weights[rows]
        return replace(self, xyz=self.xyz[rows], values=self.values[rows], z=self.z[rows], weights=w,
                       hole=self.hole[rows], depth=self.depth[rows], length=self.length[rows],
                       row_ids=self.row_ids[rows])  # fmt: skip

    def extent(self) -> dict[str, list[float]]:
        """The extent in world coordinates."""
        lo, hi = self.xyz.min(axis=0) + self.origin, self.xyz.max(axis=0) + self.origin
        return {"min": [float(v) for v in lo], "max": [float(v) for v in hi]}


def _beat(tick: Tick | None, message: str) -> None:
    if tick is not None:
        tick(None, message)


def _number_at(cols: Any, dom: Any, name: str) -> np.ndarray:
    """A numeric field of `dom` for every row of `cols` by the middle of the sample (as `source.domain_at` does for
    a text one): NaN — no interval holds the middle, or its value is missing."""
    from bf_core.stats import source_kernels as sk

    values = dom.number(name)
    if dom.table.id == cols.table.id:
        return np.asarray(values)
    lo, hi = sk.hole_ranges(cols.holes, dom.holes, dom.starts)
    rows, _outside = sk.rows_covering(cols.starts, 0.5 * (cols.from_m + cols.to_m), lo, hi, dom.from_m, dom.to_m)
    out = np.full(cols.n, np.nan)
    ok = rows >= 0
    out[ok] = values[rows[ok]]
    return out


def _in_domain(project: Any, data: spec.Data, cols: Any) -> np.ndarray | None:
    """bool [n]: the middle is in one of the chosen values of the domain; None — no domain."""
    from bf_core.stats import source

    d = data.domain
    if d is None:
        return None
    dom = cols if d.table_id == data.table_id else source.read(project, data.set_id, d.table_id, [d.field])
    if d.kind == "text":
        got = source.domain_at(cols, dom, d.field)
        chosen = [k for k, label in enumerate(got.labels) if label.strip() in d.values]
        return np.isin(got.codes, np.asarray(chosen, dtype=np.int32))
    x = _number_at(cols, dom, d.field)
    finite = np.isfinite(x)
    keys = np.unique(x[finite])
    chosen_keys = keys[[format(float(v), ".15g") in d.values for v in keys]] if keys.size else keys
    return finite & np.isin(x, chosen_keys)


def _inputs(project: Any, data: spec.Data, cols: Any) -> dict[str, Any]:
    from bf_core.composites import source as dh_source
    from bf_core.db import dialect
    from bf_core.stats import source

    tables = [cols.table]
    if data.domain is not None and data.domain.table_id != data.table_id:
        with dialect.read_tx(project.engine) as conn:
            tables.append(source.find_table(conn, data.set_id, data.domain.table_id))
    st = dh_source.trajectories(project, data.set_id)
    return {"tables": [{"id": t.id, "name_key": t.name_key, "token": t.token, "rev": t.rev} for t in tables],
            "trajectories": [str(k) for k in st.key[2:]]}  # fmt: skip


def _scores(z: np.ndarray, w: np.ndarray | None) -> tuple[np.ndarray, Any]:
    """The normal scores of `z` by the table of the points with a positive weight (all without weights)."""
    from bf_core.stats.transforms import nscore
    from bf_core.stats.weighted import StatsError

    try:
        if w is None:
            return nscore(z)
        good = np.isfinite(w) & (w > 0.0)
        if not good.any():
            raise spec.bad("bad_param", "Нет ни одного положительного веса.",
                           "Выберите другое поле весов или снимите веса.", "weights")  # fmt: skip
        part, table = nscore(z[good], w[good])
        out = table.forward(z)
        out[good] = part
        return out, table
    except StatsError as e:
        raise spec.bad(e.code, e.message, e.hint, e.param or "transform") from e


def load(project: Any, data: spec.Data, *, tick: Tick | None = None) -> Points:
    """The points of `data` (module docstring). `project` needs `engine`, `uid` and `folder`."""
    from bf_core.stats import source

    _beat(tick, STAGE_READ)
    names = [data.field, *([data.weights] if data.weights else [])]
    if data.domain is not None and data.domain.table_id == data.table_id and data.domain.field not in names:
        names.append(data.domain.field)
    cols = source.read(project, data.set_id, data.table_id, names)
    z_all = np.asarray(cols.number(data.field))
    inside = _in_domain(project, data, cols)
    _beat(tick, STAGE_TRAJ)
    xyz_all = source.midpoints(project, cols)
    has_value = np.isfinite(z_all)
    has_pos = np.isfinite(xyz_all).all(axis=1)
    used = has_value & has_pos & (inside if inside is not None else True)
    rows = np.flatnonzero(used)
    w = None if data.weights is None else np.asarray(cols.number(data.weights))[rows]
    counts = {
        "rows": cols.n + cols.skipped,
        "missing": int((~has_value).sum()),
        "no_position": cols.skipped + int((has_value & ~has_pos).sum()),
        "outside_domain": int((has_value & has_pos).sum()) - int(rows.size),
        "used": int(rows.size),
        "no_weight": 0 if w is None else int((~(np.isfinite(w) & (w > 0.0))).sum()),
    }
    if rows.size == 0:
        raise ApiError(422, "no_values", "Нет точек для расчёта: у проб нет значения, положения или домена.",
                       "Проверьте поле, домен и траектории скважин.", {"counts": counts})  # fmt: skip
    if rows.size < 2:
        raise ApiError(422, "too_few_values", "Для вариограммы нужно не меньше двух точек.",
                       "Расширьте домен или выберите другое поле.", {"counts": counts})  # fmt: skip
    z = z_all[rows]
    if float(z.min()) == float(z.max()):
        raise ApiError(422, "no_spread", "Все значения точек расчёта равны: вариограмма не определена.",
                       "Выберите другое поле или домен.", {"counts": counts})  # fmt: skip
    xyz = xyz_all[rows]
    origin = 0.5 * (xyz.min(axis=0) + xyz.max(axis=0))
    length = np.subtract(cols.to_m, cols.from_m)[rows]
    table = None
    values = z
    if data.transform == "nscore":
        _beat(tick, STAGE_NSCORE)
        values, table = _scores(z, w)
    sigma2_z = float(np.var(z))
    return Points(
        xyz=xyz - origin, origin=origin, values=values, z=z, weights=w, hole=cols.hole_of()[rows],
        holes=list(cols.holes), depth=0.5 * (cols.from_m + cols.to_m)[rows], length=length,
        row_ids=np.asarray(cols.row_ids)[rows], counts=counts,
        sigma2=1.0 if table is not None else sigma2_z, sigma2_z=sigma2_z, median_length=float(np.median(length)),
        transform=table, inputs=_inputs(project, data, cols),
    )  # fmt: skip


# --- sampling (8.6.2; ADR 0022 §4) ----------------------------------------------------------------------


def sample_size(n: int, estimate: float, limit: float = spec.PAIRS_MAX) -> int:
    """The points to keep when the estimate of the checks of pairs is above `limit`: ⌊n·√(limit / estimate)⌋ (the
    pairs fall as n²), at least 2; n — no sampling."""
    if not estimate > limit:
        return n
    return max(2, min(n, math.floor(n * math.sqrt(limit / estimate))))


def sample(p: Points, n_new: int, seed: int = spec.SEED) -> tuple[Points, dict[str, int] | None]:
    """`n_new` points of `p` without replacement, `Generator(PCG64(seed))`, in their order; `sampled {n, of, seed}`
    for the answer — None when all are kept."""
    if n_new >= p.n:
        return p, None
    rng = np.random.Generator(np.random.PCG64(seed))
    rows = np.sort(rng.choice(p.n, size=n_new, replace=False))
    return p.subset(rows), {"n": int(n_new), "of": p.n, "seed": int(seed)}


# --- GET /variography/defaults -------------------------------------------------------------------------------


def defaults(project: Any, data: spec.Data) -> dict[str, Any]:
    """The answer of `GET /variography/defaults` (e16-api.md): the counts, σ², the median length, the defaults of
    ADR 0022 §2 and the ranges of 8.6.4."""
    from bf_core.db import dialect

    p = load(project, data)
    with dialect.read_tx(project.engine) as conn:
        has_decl = spec._weights(conn, data.table_id, None) is not None
    return {"set_id": data.set_id, "table": str(data.table_id), "table_name": data.table_name, "field": data.field,
            "unit": data.unit, "transform": data.transform, "counts": p.counts, "sigma2": p.sigma2,
            "sigma2_z": p.sigma2_z, "median_length": p.median_length, "weights": data.weights,
            "has_decl_w": has_decl, "extent": p.extent(),
            "defaults": spec.defaults_json(spec.defaults_for(p.median_length)),
            "ranges": spec.ranges_json()}  # fmt: skip
