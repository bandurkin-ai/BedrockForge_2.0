"""Statistics of assay components: declustering, normal scores, variograms.

All calculations work on one component of a drillhole set. Every assay
interval with a finite value becomes a sample located at the midpoint of the
interval on the hole trajectory. Declustering and variograms use the plan view
(X, Y) only: they are 2D, so composite the data to a common support first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Assay, Component
from app.routers.projects import get_set
from app.routers.viz import _trajectories
from app.stats import (VariogramModel, backtr, bootstrap, cell_declus, experimental_variogram, idw,
                       local_expectation, local_percentile, local_prob_exceed, local_std, nscore,
                       polygonal_declus, resample_groups, variogram_map)

router = APIRouter(prefix="/api", tags=["statistics"])

# The pair calculations are O(n^2); beyond this the data should be composited.
MAX_SAMPLES = 20000
MAX_MAP_CELLS = 40000


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
@dataclass
class Samples:
    assay_ids: np.ndarray
    holes: np.ndarray  # hole id of every sample
    x: np.ndarray
    y: np.ndarray
    v: np.ndarray

    @property
    def n(self) -> int:
        return len(self.v)

    def xy(self) -> np.ndarray:
        return np.column_stack([self.x, self.y])


def load_samples(session: Session, set_id: int, component: str, min_n: int = 2) -> Samples:
    """Interval midpoints with a finite value of the component, in assay id order."""
    get_set(session, set_id)
    exists = session.scalar(
        select(Component.id).where(Component.set_id == set_id, Component.name == component)
    )
    if exists is None:
        raise HTTPException(404, f"Компонент «{component}» не найден в наборе")

    trajs, _ = _trajectories(session, set_id)
    rows = session.execute(
        select(Assay.id, Assay.hole_id, Assay.from_m, Assay.to_m, Assay.analytes[component].as_float())
        .where(Assay.set_id == set_id, Assay.analytes.has_key(component))
        .order_by(Assay.id)
    )
    by_hole: dict[str, list[tuple[int, float, float]]] = {}
    for aid, hole, from_m, to_m, value in rows:
        if (hole in trajs and None not in (from_m, to_m, value)
                and to_m > from_m and math.isfinite(value)):
            by_hole.setdefault(hole, []).append((aid, 0.5 * (from_m + to_m), value))

    ids, hs, xs, ys, vs = [], [], [], [], []
    for hole, items in by_hole.items():
        arr = np.array(items)
        pos = trajs[hole].positions(arr[:, 1])
        ids.append(arr[:, 0])
        hs.append(np.full(len(arr), hole, dtype=object))
        xs.append(pos[:, 0])
        ys.append(pos[:, 1])
        vs.append(arr[:, 2])
    if not ids:
        raise HTTPException(422, f"Нет интервалов со значением «{component}» и траекторией")
    s = Samples(*(np.concatenate(a) for a in (ids, hs, xs, ys, vs)))
    order = np.argsort(s.assay_ids, kind="stable")
    s = Samples(s.assay_ids[order].astype(int), s.holes[order], s.x[order], s.y[order], s.v[order])
    if s.n < min_n:
        raise HTTPException(422, f"Недостаточно данных: {s.n} интервалов, нужно не менее {min_n}")
    if s.n > MAX_SAMPLES:
        raise HTTPException(
            422, f"Слишком много интервалов ({s.n}, максимум {MAX_SAMPLES}): выполните композитирование"
        )
    return s


def _clean(values) -> list:
    """JSON-safe list: NaN and infinities become null."""
    return [None if not math.isfinite(v) else v for v in np.asarray(values, dtype=float).tolist()]


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# --------------------------------------------------------------------------
# Declustering (shared by declustering, nscore and backtr)
# --------------------------------------------------------------------------
class DeclusIn(BaseModel):
    method: Literal["cell", "polygonal"] = "cell"
    # cell: sizes default to 2 % / 50 % of the larger plan extent
    cmin: float | None = Field(None, gt=0)
    cmax: float | None = Field(None, gt=0)
    ncell: int = Field(20, ge=1, le=200)
    noff: int = Field(8, ge=1, le=32)
    minmax: Literal["min", "max"] = "min"
    # polygonal: grid for the polygon areas; bounds default to the data extent
    nx: int = Field(100, ge=1, le=1000)
    ny: int = Field(100, ge=1, le=1000)
    bounds: tuple[float, float, float, float] | None = Field(None, description="xmin, xmax, ymin, ymax")

    @model_validator(mode="after")
    def _sizes(self):
        if self.cmin is not None and self.cmax is not None and self.cmin > self.cmax:
            raise ValueError("cmin не может быть больше cmax")
        return self


def compute_weights(p: DeclusIn, s: Samples) -> tuple[np.ndarray, dict]:
    """Declustering weights (sum = n) and method details for the response."""
    if p.method == "polygonal":
        w = _call(polygonal_declus, s.x, s.y, bounds=p.bounds, nx=p.nx, ny=p.ny)
        return w, {}
    span = max(float(np.ptp(s.x)), float(np.ptp(s.y)))
    if span <= 0:
        raise HTTPException(422, "Все интервалы в одной точке плана: декластеризация невозможна")
    cmin = p.cmin if p.cmin is not None else 0.02 * span
    cmax = p.cmax if p.cmax is not None else max(0.5 * span, cmin)
    w, sizes, means = _call(cell_declus, s.x, s.y, s.v, cmin, cmax,
                            ncell=p.ncell, noff=p.noff, minmax=p.minmax)
    dm = float(np.average(s.v, weights=w))
    best = None if np.allclose(w, 1.0) else float(sizes[1:][np.argmin(np.abs(means[1:] - dm))])
    return w, {"cell_sizes": _clean(sizes), "means": _clean(means),
               "cmin": cmin, "cmax": cmax, "best_cell_size": best}


class DeclusRequest(DeclusIn):
    component: str
    include_weights: bool = False


class DeclusOut(BaseModel):
    component: str
    method: str
    count: int
    naive_mean: float
    declustered_mean: float
    weight_min: float
    weight_max: float
    # cell method: the declustered mean for every tried cell size (index 0: size 0, the naive mean)
    cell_sizes: list[float | None] | None = None
    means: list[float | None] | None = None
    cmin: float | None = None
    cmax: float | None = None
    best_cell_size: float | None = None
    assay_ids: list[int] | None = None
    weights: list[float] | None = None


@router.post("/sets/{set_id}/stats/declustering", response_model=DeclusOut)
def declustering(set_id: int, body: DeclusRequest, session: Session = Depends(get_session)):
    """Declustering weights of a component (they sum to the number of samples)."""
    s = load_samples(session, set_id, body.component)
    w, extra = compute_weights(body, s)
    out = DeclusOut(
        component=body.component, method=body.method, count=s.n,
        naive_mean=float(s.v.mean()), declustered_mean=float(np.average(s.v, weights=w)),
        weight_min=float(w.min()), weight_max=float(w.max()), **extra,
    )
    if body.include_weights:
        out.assay_ids, out.weights = s.assay_ids.tolist(), w.tolist()
    return out


# --------------------------------------------------------------------------
# Normal scores
# --------------------------------------------------------------------------
class NscoreRequest(BaseModel):
    component: str
    declus: DeclusIn | None = Field(None, description="weights for the transform table; none = equal")
    zmin: float | None = None
    zmax: float | None = None


class NscoreOut(BaseModel):
    component: str
    count: int
    # the transform table: sorted values and their normal scores
    values: list[float]
    scores: list[float]
    zmin: float
    zmax: float
    assay_ids: list[int]
    # normal score of every sample, in assay_ids order
    sample_scores: list[float]


def _transform(body, s: Samples):
    w = compute_weights(body.declus, s)[0] if body.declus else None
    return _call(nscore, s.v, wts=w, zmin=body.zmin, zmax=body.zmax)


@router.post("/sets/{set_id}/stats/nscore", response_model=NscoreOut)
def normal_score(set_id: int, body: NscoreRequest, session: Session = Depends(get_session)):
    """Normal score transform of a component, optionally with declustering weights."""
    s = load_samples(session, set_id, body.component, min_n=1)
    ns, tr = _transform(body, s)
    return NscoreOut(
        component=body.component, count=s.n, values=tr.values.tolist(), scores=tr.scores.tolist(),
        zmin=tr.zmin, zmax=tr.zmax, assay_ids=s.assay_ids.tolist(), sample_scores=ns.tolist(),
    )


class BacktrRequest(NscoreRequest):
    scores_in: list[float] = Field(alias="scores", min_length=1, max_length=20000)
    ltail: Literal["linear", "power"] | None = "linear"
    utail: Literal["linear", "power", "hyperbolic"] | None = "linear"
    ltpar: float | None = Field(None, gt=0)
    utpar: float | None = Field(None, gt=0)

    model_config = {"populate_by_name": True}


class BacktrOut(BaseModel):
    component: str
    values: list[float]


@router.post("/sets/{set_id}/stats/backtr", response_model=BacktrOut)
def back_transform(set_id: int, body: BacktrRequest, session: Session = Depends(get_session)):
    """Back-transform normal scores to component values through the data's own table."""
    s = load_samples(session, set_id, body.component, min_n=1)
    _, tr = _transform(body, s)
    y = np.asarray(body.scores_in, dtype=float)
    if not np.all(np.isfinite(y)):
        raise HTTPException(422, "scores должны быть конечными числами")
    values = _call(backtr, y, tr, body.ltail, body.utail, body.ltpar, body.utpar)
    return BacktrOut(component=body.component, values=values.tolist())


# --------------------------------------------------------------------------
# Variograms
# --------------------------------------------------------------------------
class StructureIn(BaseModel):
    it: Literal["spherical", "exponential", "gaussian"]
    cc: float = Field(ge=0)
    hmaj: float = Field(gt=0)
    hmin: float | None = Field(None, gt=0, description="default: hmaj")
    azi: float = Field(0.0, description="azimuth of the major direction, degrees from north")


class ModelIn(BaseModel):
    nug: float = Field(0.0, ge=0)
    structures: list[StructureIn] = Field(default_factory=list, max_length=4)

    def build(self) -> VariogramModel:
        return _call(VariogramModel, self.nug, [
            {k: v for k, v in st.model_dump().items() if v is not None} for st in self.structures
        ])


class DirectionIn(BaseModel):
    azimuth: float = 0.0
    azimuth_tol: float = Field(90.0, description=">= 90: omnidirectional")
    bandwidth: float | None = Field(None, gt=0, description="default: unlimited")


class VariogramRequest(BaseModel):
    component: str
    lag: float = Field(gt=0)
    nlag: int = Field(10, ge=1, le=200)
    lag_tol: float | None = Field(None, ge=0, description="default: lag / 2")
    directions: list[DirectionIn] = Field(default_factory=lambda: [DirectionIn()], min_length=1, max_length=8)
    standardize: bool = False
    # the model is drawn at the lag centres in every direction (same units as the result)
    model: ModelIn | None = None


class DirectionOut(BaseModel):
    azimuth: float
    azimuth_tol: float
    bandwidth: float | None
    dist: list[float | None]
    gamma: list[float | None]
    npairs: list[int]
    model_gamma: list[float] | None = None


class VariogramOut(BaseModel):
    component: str
    count: int
    variance: float
    lags: list[float]
    directions: list[DirectionOut]


@router.post("/sets/{set_id}/stats/variogram", response_model=VariogramOut)
def variogram(set_id: int, body: VariogramRequest, session: Session = Depends(get_session)):
    """Experimental semivariogram (plan view) in one or several directions."""
    s = load_samples(session, set_id, body.component)
    model = body.model.build() if body.model else None
    lags = body.lag * np.arange(1, body.nlag + 1)
    out = []
    for d in body.directions:
        dist, gamma, npairs = _call(
            experimental_variogram, s.xy(), s.v, body.lag, body.nlag, lag_tol=body.lag_tol,
            azimuth=d.azimuth, azimuth_tol=d.azimuth_tol,
            bandwidth=np.inf if d.bandwidth is None else d.bandwidth, standardize=body.standardize,
        )
        out.append(DirectionOut(
            azimuth=d.azimuth, azimuth_tol=d.azimuth_tol, bandwidth=d.bandwidth,
            dist=_clean(dist), gamma=_clean(gamma), npairs=npairs.tolist(),
            model_gamma=model.gamma(lags, d.azimuth).tolist() if model else None,
        ))
    return VariogramOut(component=body.component, count=s.n, variance=float(s.v.var()),
                        lags=lags.tolist(), directions=out)


class MapRequest(BaseModel):
    component: str
    nxlag: int = Field(10, ge=1, le=100)
    nylag: int = Field(10, ge=1, le=100)
    dxlag: float = Field(gt=0)
    dylag: float = Field(gt=0)
    minnp: int = Field(5, ge=1)
    standardize: bool = False

    @model_validator(mode="after")
    def _size(self):
        if (2 * self.nxlag + 1) * (2 * self.nylag + 1) > MAX_MAP_CELLS:
            raise ValueError("слишком большая карта")
        return self


class MapOut(BaseModel):
    component: str
    count: int
    x: list[float]  # lag of every column
    y: list[float]  # lag of every row, ascending
    gamma: list[list[float | None]]  # [row][column]; null where fewer than minnp pairs
    npairs: list[list[int]]


@router.post("/sets/{set_id}/stats/variogram-map", response_model=MapOut)
def variogram_map_endpoint(set_id: int, body: MapRequest, session: Session = Depends(get_session)):
    """Variogram map of a component (plan view)."""
    s = load_samples(session, set_id, body.component)
    gamma, npairs = _call(variogram_map, s.xy(), s.v, body.nxlag, body.nylag, body.dxlag,
                          body.dylag, minnp=body.minnp, standardize=body.standardize)
    return MapOut(
        component=body.component, count=s.n,
        x=(body.dxlag * np.arange(-body.nxlag, body.nxlag + 1)).tolist(),
        y=(body.dylag * np.arange(-body.nylag, body.nylag + 1)).tolist(),
        gamma=[_clean(row) for row in gamma], npairs=npairs.tolist(),
    )


class ModelCurveRequest(BaseModel):
    model: ModelIn
    lags: list[float] = Field(min_length=1, max_length=2000, description="distances, >= 0")
    azimuth: float = 0.0

    @model_validator(mode="after")
    def _lags(self):
        if any(not math.isfinite(h) or h < 0 for h in self.lags):
            raise ValueError("lags должны быть конечными и неотрицательными")
        return self


class ModelCurveOut(BaseModel):
    sill: float
    gamma: list[float]
    cov: list[float]


@router.post("/stats/variogram-model", response_model=ModelCurveOut)
def variogram_model_curve(body: ModelCurveRequest):
    """Variogram model values at given distances along an azimuth (no data needed)."""
    model = body.model.build()
    h = np.asarray(body.lags, dtype=float)
    return ModelCurveOut(sill=model.sill, gamma=model.gamma(h, body.azimuth).tolist(),
                         cov=model.cov(h, body.azimuth).tolist())


# --------------------------------------------------------------------------
# Uncertainty
# --------------------------------------------------------------------------
MAX_BOOTSTRAP = 5000
HIST_BINS = 24

_STATISTICS = {
    "mean": np.mean,
    "median": np.median,
    "std": lambda a: float(np.std(a)),
    "cv": lambda a: float(np.std(a) / np.mean(a)) if np.mean(a) != 0 else float("nan"),
    "p10": lambda a: float(np.percentile(a, 10)),
    "p90": lambda a: float(np.percentile(a, 90)),
}
STATISTIC_TITLES = {"mean": "Среднее", "median": "Медиана", "std": "Стандартное отклонение",
                    "cv": "Коэффициент вариации", "p10": "P10", "p90": "P90"}


class BootstrapRequest(BaseModel):
    component: str
    statistic: Literal["mean", "median", "std", "cv", "p10", "p90"] = "mean"
    n_boot: int = Field(1000, ge=10, le=MAX_BOOTSTRAP)
    confidence: float = Field(0.9, gt=0.5, lt=1.0)
    declus: DeclusIn | None = Field(None, description="draw samples with probability proportional to the weights")
    seed: int | None = None


class BootstrapOut(BaseModel):
    component: str
    statistic: str
    count: int
    n_boot: int
    observed: float
    mean: float
    std: float
    confidence: float
    ci_low: float
    ci_high: float
    # histogram of the bootstrap distribution
    edges: list[float]
    counts: list[int]


@router.post("/sets/{set_id}/stats/bootstrap", response_model=BootstrapOut)
def bootstrap_statistic(set_id: int, body: BootstrapRequest, session: Session = Depends(get_session)):
    """Bootstrap uncertainty of a statistic of one component (resampling the intervals)."""
    s = load_samples(session, set_id, body.component)
    weights = compute_weights(body.declus, s)[0] if body.declus else None
    stat = _STATISTICS[body.statistic]
    realizations = _call(bootstrap, s.v, stat, body.n_boot, weights=weights, seed=body.seed)
    finite = realizations[np.isfinite(realizations)]
    if len(finite) == 0:
        raise HTTPException(422, "Статистика не определена для этих данных")
    tail = 100 * (1 - body.confidence) / 2
    lo, hi = np.percentile(finite, [tail, 100 - tail])
    counts, edges = np.histogram(finite, bins=HIST_BINS)
    return BootstrapOut(
        component=body.component, statistic=body.statistic, count=s.n, n_boot=body.n_boot,
        observed=float(stat(s.v)), mean=float(finite.mean()), std=float(finite.std()),
        confidence=body.confidence, ci_low=float(lo), ci_high=float(hi),
        edges=edges.tolist(), counts=counts.tolist(),
    )


class LocalRequest(BaseModel):
    component: str
    nx: int = Field(30, ge=2, le=100)
    ny: int = Field(30, ge=2, le=100)
    nreal: int = Field(50, ge=5, le=300)
    k: int = Field(8, ge=1, le=64)
    power: float = Field(2.0, gt=0, le=10)
    radius: float | None = Field(None, gt=0, description="search radius; default 25 % of the larger plan extent")
    threshold: float | None = Field(None, description="adds the probability of reaching it")
    seed: int | None = None


class LocalOut(BaseModel):
    component: str
    count: int
    holes: int
    nreal: int
    radius: float
    x: list[float]  # cell centres of every column
    y: list[float]  # cell centres of every row, ascending
    # maps [row][column]; null where no data lie within the radius
    mean: list[list[float | None]]
    std: list[list[float | None]]
    p10: list[list[float | None]]
    p90: list[list[float | None]]
    prob_exceed: list[list[float | None]] | None = None


@router.post("/sets/{set_id}/stats/local-uncertainty", response_model=LocalOut)
def local_uncertainty(set_id: int, body: LocalRequest, session: Session = Depends(get_session)):
    """Local uncertainty of an inverse distance estimate: bootstrap the holes, estimate a plan grid each time.

    Every realization resamples whole holes with replacement (the samples of a hole are not independent),
    so the spread of the maps shows how much the estimate depends on which holes were drilled.
    """
    s = load_samples(session, set_id, body.component, min_n=3)
    n_holes = len(np.unique(s.holes))
    if n_holes < 3:
        raise HTTPException(422, f"Нужно не менее 3 скважин с данными, есть {n_holes}")
    x0, x1, y0, y1 = float(s.x.min()), float(s.x.max()), float(s.y.min()), float(s.y.max())
    span = max(x1 - x0, y1 - y0)
    if span <= 0:
        raise HTTPException(422, "Все интервалы в одной точке плана")
    radius = body.radius if body.radius is not None else 0.25 * span
    # cell centres; a degenerate side (all holes on a line) still gets a strip of the other side's cell size
    cx = x0 + (np.arange(body.nx) + 0.5) * max(x1 - x0, span / body.nx) / body.nx
    cy = y0 + (np.arange(body.ny) + 0.5) * max(y1 - y0, span / body.ny) / body.ny
    targets = np.column_stack([np.tile(cx, body.ny), np.repeat(cy, body.nx)])

    xy = s.xy()
    stack = np.empty((body.nreal, body.ny, body.nx))
    for r, idx in enumerate(_call(resample_groups, s.holes, body.nreal, body.seed)):
        stack[r] = _call(idw, xy[idx], s.v[idx], targets, k=body.k, power=body.power,
                         radius=radius).reshape(body.ny, body.nx)

    def grid(a):
        return [_clean(row) for row in a]

    return LocalOut(
        component=body.component, count=s.n, holes=n_holes, nreal=body.nreal, radius=radius,
        x=cx.tolist(), y=cy.tolist(),
        mean=grid(local_expectation(stack)), std=grid(local_std(stack)),
        p10=grid(local_percentile(stack, 10)), p90=grid(local_percentile(stack, 90)),
        prob_exceed=None if body.threshold is None else grid(local_prob_exceed(stack, body.threshold)),
    )


# --------------------------------------------------------------------------
# Histogram of an assay field (no trajectories needed, no sample limit)
# --------------------------------------------------------------------------
LENGTH_FIELD = "length"


class FieldOut(BaseModel):
    # "comp:AU", "attr:DENSITY" or "length"
    id: str
    name: str
    kind: Literal["component", "attribute", "derived"]
    unit: str = ""
    count: int


@router.get("/sets/{set_id}/stats/fields", response_model=list[FieldOut])
def numeric_fields(set_id: int, session: Session = Depends(get_session)):
    """Numeric fields of the assay table: components, numeric attributes, interval length."""
    from sqlalchemy import text

    get_set(session, set_id)
    units = {c.name: c.unit for c in session.scalars(select(Component).where(Component.set_id == set_id))}
    out = []
    for column, kind, prefix in (("analytes", "component", "comp"), ("attributes", "attribute", "attr")):
        rows = session.execute(text(
            f"SELECT e.key, count(*) FROM assay, jsonb_each(assay.{column}) AS e "
            "WHERE assay.set_id = :s AND jsonb_typeof(e.value) = 'number' GROUP BY e.key ORDER BY e.key"
        ), {"s": set_id}).all()
        out += [FieldOut(id=f"{prefix}:{k}", name=k, kind=kind, unit=units.get(k, "") if kind == "component" else "", count=n)
                for k, n in rows]
    n = session.scalar(text("SELECT count(*) FROM assay WHERE set_id = :s AND to_m > from_m"), {"s": set_id}) or 0
    out.append(FieldOut(id=LENGTH_FIELD, name="Длина интервала", kind="derived", unit="м", count=n))
    return out


def _field_sql(field_id: str, key: str) -> tuple[str, dict]:
    if field_id == LENGTH_FIELD:
        return "CASE WHEN to_m > from_m THEN to_m - from_m END", {}
    prefix, _, name = field_id.partition(":")
    column = {"comp": "analytes", "attr": "attributes"}.get(prefix)
    if column is None or not name:
        raise HTTPException(422, f"Неизвестное поле «{field_id}»")
    return (f"CASE WHEN jsonb_typeof({column} -> :{key}) = 'number' THEN ({column} ->> :{key})::float8 END",
            {key: name})


class SampleFilter(BaseModel):
    """Which values of a field go into a chart, and how they are treated (shared by all charts)."""

    field: str
    # none | length | field (weight_field) | length_field (length × weight_field, e.g. density: tonnage)
    weight: Literal["none", "length", "field", "length_field"] = "none"
    weight_field: str | None = None
    # Values below zero: Micromine and many labs write "<0.01" as -0.01.
    negatives: Literal["keep", "exclude", "half_abs"] = "keep"
    exclude_zero: bool = False
    # Cuts at both ends; statistics are after them. "cap": values beyond are replaced by the cut
    # (top cut of outliers, floor at the detection limit); "exclude": samples beyond are left out
    # (e.g. only ore above a cut-off grade).
    bottom_cut: float | None = None
    bottom_mode: Literal["cap", "exclude"] = "exclude"
    top_cut: float | None = None
    top_mode: Literal["cap", "exclude"] = "cap"
    holes: list[str] | None = Field(None, max_length=10000)

    @model_validator(mode="after")
    def _weights(self):
        if self.weight in ("field", "length_field") and not self.weight_field:
            raise ValueError("Укажите поле веса")
        if self.bottom_cut is not None and self.top_cut is not None and self.top_cut <= self.bottom_cut:
            raise ValueError("Верхняя отсечка должна быть больше нижней")
        return self


class HistogramIn(SampleFilter):
    log: bool = False
    bin_width: float | None = Field(None, gt=0)
    bins: int | None = Field(None, ge=1, le=1000)
    start: float | None = None
    end: float | None = None

    @model_validator(mode="after")
    def _range(self):
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("Конец диапазона должен быть больше начала")
        return self


@dataclass
class FieldValues:
    values: np.ndarray
    weights: np.ndarray
    counts: dict


def load_field_values(session: Session, set_id: int, f: SampleFilter) -> FieldValues:
    """Values and weights of a field of a set's assay table after the filters, with counts of
    what was left out (missing value, no weight, negatives, zeros, top cut)."""
    from sqlalchemy import text

    get_set(session, set_id)
    value_sql, params = _field_sql(f.field, "f")
    weight_sql = "1.0"
    if f.weight in ("field", "length_field"):
        weight_sql, wp = _field_sql(f.weight_field, "w")
        params.update(wp)
    if f.weight in ("length", "length_field"):
        weight_sql = f"(CASE WHEN to_m > from_m THEN to_m - from_m END) * {weight_sql}"
    where = "set_id = :s"
    params["s"] = set_id
    if f.holes:
        where += " AND hole_id = ANY(:holes)"
        params["holes"] = f.holes
    rows = session.execute(text(f"SELECT {value_sql}, {weight_sql} FROM assay WHERE {where}"), params).all()
    data = np.array([(np.nan if v is None else v, np.nan if w is None else w) for v, w in rows], dtype=np.float64).reshape(-1, 2)
    v, w = data[:, 0], data[:, 1]
    total = len(v)
    missing = ~np.isfinite(v)
    bad_weight = ~missing & ~(np.isfinite(w) & (w > 0))
    keep = ~missing & ~bad_weight
    v, w = v[keep], w[keep]
    negative = v < 0
    n_negative = int(negative.sum())
    if f.negatives == "exclude":
        v, w = v[~negative], w[~negative]
    elif f.negatives == "half_abs":
        v = np.where(negative, np.abs(v) / 2, v)
    n_zero = 0
    if f.exclude_zero:
        zero = v == 0
        n_zero = int(zero.sum())
        v, w = v[~zero], w[~zero]
    cut = {"bottom_capped": 0, "bottom_excluded": 0, "capped": 0, "top_excluded": 0}
    if f.bottom_cut is not None:
        low = v < f.bottom_cut
        if f.bottom_mode == "cap":
            cut["bottom_capped"] = int(low.sum())
            v = np.where(low, f.bottom_cut, v)
        else:
            cut["bottom_excluded"] = int(low.sum())
            v, w = v[~low], w[~low]
    if f.top_cut is not None:
        high = v > f.top_cut
        if f.top_mode == "cap":
            cut["capped"] = int(high.sum())
            v = np.where(high, f.top_cut, v)
        else:
            cut["top_excluded"] = int(high.sum())
            v, w = v[~high], w[~high]
    if len(v) == 0:
        raise HTTPException(422, "Нет значений после фильтров")
    return FieldValues(v, w, {
        "rows": total, "missing": int(missing.sum()), "bad_weight": int(bad_weight.sum()),
        "negative": n_negative, "zero_excluded": n_zero, **cut, "used": int(len(v)),
    })


@router.post("/sets/{set_id}/stats/histogram")
def histogram_of_field(set_id: int, body: HistogramIn, session: Session = Depends(get_session)) -> dict:
    from app.stats.histogram import HistogramError, HistogramSpec, describe, histogram

    fv = load_field_values(session, set_id, body)
    v, w = fv.values, fv.weights
    n_nonpositive_log = int(np.count_nonzero(v <= 0)) if body.log else 0
    try:
        h = histogram(v, w, HistogramSpec(log=body.log, bin_width=body.bin_width, bins=body.bins,
                                          start=body.start, end=body.end))
    except HistogramError as exc:
        raise HTTPException(422, str(exc)) from exc
    weighted = body.weight != "none"
    weight_total = float(w.sum())
    sums = np.asarray(h.weights)
    percent = (sums / weight_total * 100).tolist()
    cumulative = ((h.below["weight"] + np.cumsum(sums)) / weight_total * 100).tolist()
    return {
        "field": body.field,
        "log": body.log,
        "weighted": weighted,
        "counts": {**fv.counts, "nonpositive_log": n_nonpositive_log},
        "stats": describe(v, w if weighted else None),
        "stats_unweighted": describe(v) if weighted else None,
        "bins": {
            "edges": h.edges, "counts": h.counts, "weights": h.weights, "percent": percent,
            "cumulative_percent": cumulative, "width": h.bin_width, "below": h.below, "above": h.above,
        },
        "conventions": "Процентили по GSLIB: середина накопленного веса, линейная интерполяция (без весов — Hazen). "
                       "Дисперсия — генеральная. Бины [a, b), последний закрыт.",
    }


# --------------------------------------------------------------------------
# Q-Q plot: a sample against another sample or a fitted distribution
# --------------------------------------------------------------------------
class QQSample(SampleFilter):
    kind: Literal["sample"] = "sample"
    set_id: int


class QQTheory(BaseModel):
    kind: Literal["normal", "lognormal"]


class QQIn(BaseModel):
    x: QQSample
    y: QQSample | QQTheory = Field(discriminator="kind")
    # Sample against sample: quantiles at k / (n + 1), k = 1..n (99: every percent).
    quantiles: int = Field(99, ge=3, le=999)


def _side(session: Session, s: QQSample) -> tuple[FieldValues, dict]:
    from app.stats.histogram import describe

    fv = load_field_values(session, s.set_id, s)
    weighted = s.weight != "none"
    return fv, {"set_id": s.set_id, "field": s.field, "weighted": weighted, "counts": fv.counts,
                "stats": describe(fv.values, fv.weights if weighted else None)}


@router.post("/stats/qq")
def qq_plot(body: QQIn, session: Session = Depends(get_session)) -> dict:
    """Q-Q plot data. For two samples the x and y sides may come from different drillhole sets."""
    from app.stats.qq import QQError, sample_vs_distribution, sample_vs_sample

    fx, x_info = _side(session, body.x)
    try:
        if isinstance(body.y, QQSample):
            fy, y_info = _side(session, body.y)
            out = sample_vs_sample(fx.values, fx.weights, fy.values, fy.weights, body.quantiles)
            return {"mode": "sample", "x": x_info, "y": y_info, **out}
        out = sample_vs_distribution(fx.values, fx.weights, body.y.kind)
    except QQError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"mode": "theory", "x": x_info, "y": None, **out}


# --------------------------------------------------------------------------
# Dendrogram: clustering of fields by correlation
# --------------------------------------------------------------------------
class DendrogramIn(BaseModel):
    # None: every component of the set.
    fields: list[str] | None = Field(None, max_length=200)
    method: Literal["pearson", "spearman"] = "pearson"
    log: bool = True
    linkage: Literal["average", "complete", "single", "weighted"] = "average"
    distance: Literal["1-r", "1-|r|"] = "1-r"
    missing: Literal["pairwise", "complete"] = "pairwise"
    weight: Literal["none", "length"] = "length"
    negatives: Literal["keep", "exclude", "half_abs"] = "half_abs"
    exclude_zero: bool = False
    holes: list[str] | None = Field(None, max_length=10000)


@router.post("/sets/{set_id}/stats/dendrogram")
def dendrogram_of_fields(set_id: int, body: DendrogramIn, session: Session = Depends(get_session)) -> dict:
    from sqlalchemy import text

    from app.stats.dendrogram import DendrogramError, cluster, correlation_matrix

    get_set(session, set_id)
    fields = body.fields
    if fields is None:
        fields = [f"comp:{c.name}" for c in session.scalars(select(Component).where(Component.set_id == set_id).order_by(Component.name))]
    fields = list(dict.fromkeys(fields))
    if len(fields) < 2:
        raise HTTPException(422, "Нужно хотя бы два числовых поля")
    exprs, params = [], {"s": set_id}
    for i, f in enumerate(fields):
        sql, p = _field_sql(f, f"f{i}")
        exprs.append(sql)
        params.update(p)
    where = "set_id = :s"
    if body.holes:
        where += " AND hole_id = ANY(:holes)"
        params["holes"] = body.holes
    rows = session.execute(text(
        f"SELECT CASE WHEN to_m > from_m THEN to_m - from_m END, {', '.join(exprs)} FROM assay WHERE {where}"), params).all()
    data = np.array([[np.nan if v is None else v for v in row] for row in rows], dtype=np.float64).reshape(-1, len(fields) + 1)
    w = data[:, 0] if body.weight == "length" else np.ones(len(data))
    v = data[:, 1:]
    ok = np.isfinite(w) & (w > 0)
    w, v = w[ok], v[ok]
    v = v.copy()
    if body.negatives == "exclude":
        v[v < 0] = np.nan
    elif body.negatives == "half_abs":
        v = np.where(v < 0, np.abs(v) / 2, v)
    if body.exclude_zero:
        v[v == 0] = np.nan
    dropped_log = 0
    if body.log:
        bad = np.isfinite(v) & (v <= 0)
        dropped_log = int(bad.sum())
        v[bad] = np.nan
        v = np.log(v)
    r, n = correlation_matrix(v, w, body.method, body.missing)
    names = []
    units = {c.name: c.unit for c in session.scalars(select(Component).where(Component.set_id == set_id))}
    for f in fields:
        name = "Длина интервала" if f == LENGTH_FIELD else f.partition(":")[2]
        names.append(name)
    try:
        tree = cluster(r, names, body.distance, body.linkage)
    except DendrogramError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "fields": fields,
        "names": names,
        "units": [units.get(n, "") for n in names],
        "correlation": [[None if not math.isfinite(x) else x for x in row] for row in r.tolist()],
        "pairs": n.tolist(),
        "rows": int(len(v)),
        "dropped_nonpositive_log": dropped_log,
        **tree,
    }
