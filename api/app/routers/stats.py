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
from app.stats import (VariogramModel, backtr, cell_declus, experimental_variogram,
                       nscore, polygonal_declus, variogram_map)

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

    ids, xs, ys, vs = [], [], [], []
    for hole, items in by_hole.items():
        arr = np.array(items)
        pos = trajs[hole].positions(arr[:, 1])
        ids.append(arr[:, 0])
        xs.append(pos[:, 0])
        ys.append(pos[:, 1])
        vs.append(arr[:, 2])
    if not ids:
        raise HTTPException(422, f"Нет интервалов со значением «{component}» и траекторией")
    s = Samples(*(np.concatenate(a) for a in (ids, xs, ys, vs)))
    order = np.argsort(s.assay_ids, kind="stable")
    s = Samples(s.assay_ids[order].astype(int), s.x[order], s.y[order], s.v[order])
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
