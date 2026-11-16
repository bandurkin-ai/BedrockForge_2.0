"""Swath plots: block models and samples compared by their means in slices across a direction.

    POST   /api/stats/swath               the plot: any number of series, in any order
    POST   /api/projects/{id}/swaths      {name, settings} saves a plot in the project tree («Аудит»)
    PATCH  /api/swaths/{id}               {settings}
    (renamed, moved to a folder and deleted as any object of the tree: /api/datasets/{id})
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Union

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Dataset
from app.routers.blockmodels import Filter, attribute, block_mask, column, get_model
from app.routers.datasets import DatasetOut, dataset_out, get_dataset
from app.routers.projects import get_project, get_set
from app.routers.stats import LENGTH_FIELD, SampleFilter, _field_sql, filter_values
from app.routers.viz import _trajectories
from app.samples import resolve_source, source_sql
from app.stats import swath as sw
from app.storage import blobs

router = APIRouter(prefix="/api", tags=["swath"])

SWATH_KIND = "swath"
MAX_SERIES = 12


class BlockSeries(BaseModel):
    kind: Literal["blocks"] = "blocks"
    model_id: int
    field: str
    # volume: by block volume; tonnage: volume × density (a field); none: every block counts the same
    weight: Literal["volume", "tonnage", "none"] = "volume"
    density: str | None = None
    filters: list[Filter] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _density(self):
        if self.weight == "tonnage" and not self.density:
            raise ValueError("Для веса по тоннажу укажите поле плотности")
        return self


class SampleSeries(SampleFilter):
    kind: Literal["samples"] = "samples"
    set_id: int
    # box: every sample inside the cuboid of the swaths; blocks: only those inside a block of a
    # block model series (its filters applied), `within_series`: its number in the list
    # (none: the first block model series).
    within: Literal["box", "blocks"] = "box"
    within_series: int | None = Field(None, ge=0)


Series = Annotated[Union[BlockSeries, SampleSeries], Field(discriminator="kind")]


class SwathIn(BaseModel):
    direction: Literal["x", "y", "z", "azimuth"] = "x"
    # for "azimuth": degrees clockwise from north; the swaths are vertical slabs across it
    azimuth: float = Field(0.0, ge=0, le=360)
    # none: whole parent blocks of the first block model (or a round number without one)
    width: float | None = Field(None, gt=0)
    # a boundary between swaths passes through this coordinate along the direction
    anchor: float | None = None
    series: list[Series] = Field(min_length=1, max_length=MAX_SERIES)


class _Blocks:
    """A block model series: its blocks after the filters."""

    def __init__(self, session: Session, s: BlockSeries):
        d = get_model(session, s.model_id)
        a = attribute(d, s.field)
        if a["kind"] != "number":
            raise HTTPException(422, f"Поле {a['name']} модели «{d.name}» не числовое")
        centres = blobs.read_array(d.blobs["centres"], "<f8", 3)
        sizes = blobs.read_array(d.blobs["sizes"], "<f4", 3).astype(np.float64)
        mask = block_mask(d, s.filters, len(centres))
        values = np.asarray(column(d, a), dtype=np.float64)
        volume = sizes[:, 0] * sizes[:, 1] * sizes[:, 2]
        if s.weight == "tonnage":
            da = attribute(d, s.density)
            if da["kind"] != "number":
                raise HTTPException(422, "Плотность должна быть числовым полем")
            weights = volume * np.asarray(column(d, da), dtype=np.float64)
            self.weight_name, self.weight_unit = f"тоннаж ({da['name']})", "т"
        elif s.weight == "none":
            weights = np.ones(len(centres))
            self.weight_name, self.weight_unit = "без весов", "блоков"
        else:
            weights = volume
            self.weight_name, self.weight_unit = "объём", "м³"
        self.dataset = d
        self.grid = d.meta.get("grid") or {}
        self.centres, self.sizes = centres[mask], sizes[mask]
        self.values, self.weights = values[mask], weights[mask]
        self.blocks_total = int(len(mask))
        self.name = f"{d.name} · {a['name']}"
        self.field, self.unit = a["name"], a.get("unit") or ""
        if not len(self.centres):
            raise HTTPException(422, f"В модели «{d.name}» нет блоков после отбора")


class _Samples:
    """A sample series: the middles of the intervals with a value, after the value filters."""

    def __init__(self, session: Session, s: SampleSeries):
        d = get_set(session, s.set_id)
        src = resolve_source(session, s.set_id, s.table)
        value_sql, params = _field_sql(s.field, "f")
        weight_sql = "1.0"
        if s.weight in ("field", "length_field"):
            weight_sql, wp = _field_sql(s.weight_field, "w")
            params.update(wp)
        if s.weight in ("length", "length_field"):
            weight_sql = f"(CASE WHEN to_m > from_m THEN to_m - from_m END) * {weight_sql}"
        from_sql, src_params = source_sql(src)
        params.update(src_params)
        where = "TRUE"
        if s.holes:
            where += " AND hole_id = ANY(:holes)"
            params["holes"] = s.holes
        rows = session.execute(text(
            f"SELECT hole_id, from_m, to_m, {value_sql}, {weight_sql} FROM {from_sql} WHERE {where}"
        ), params).all()
        nan = math.nan
        data = np.array([(nan if a is None else a, nan if b is None else b, nan if v is None else v, nan if w is None else w)
                         for _, a, b, v, w in rows], dtype=np.float64).reshape(-1, 4)
        v, w, index, counts = filter_values(data[:, 2], data[:, 3], s)
        # The position: the middle of the interval on the hole's path.
        trajs, _ = _trajectories(session, s.set_id)
        depth = 0.5 * (data[index, 0] + data[index, 1])
        holes = np.array([rows[i][0] for i in index], dtype=object)
        xyz = np.full((len(index), 3), np.nan)
        for hole in set(holes.tolist()):
            traj = trajs.get(hole)
            if traj is None:
                continue
            at = holes == hole
            ok = np.isfinite(depth[at])
            pos = np.full((int(at.sum()), 3), np.nan)
            if ok.any():
                pos[ok] = traj.positions(depth[at][ok])
            xyz[at] = pos
        placed = np.isfinite(xyz).all(axis=1)
        counts["no_position"] = int((~placed).sum())
        self.points, self.values, self.weights = xyz[placed], v[placed], w[placed]
        self.counts = counts
        name = "Длина интервала" if s.field == LENGTH_FIELD else s.field.partition(":")[2]
        self.field = name
        self.unit = "м" if s.field == LENGTH_FIELD else src.units.get(name, "")
        self.name = f"{d.name} · {name}" if src.builtin else f"{d.name} · {src.name} · {name}"
        self.weight_name = {"none": "без весов", "length": "длина", "field": s.weight_field or "",
                            "length_field": f"длина × {(s.weight_field or '').partition(':')[2]}"}[s.weight]
        if s.weight == "field":
            self.weight_name = (s.weight_field or "").partition(":")[2] or "Длина интервала"
        if not len(self.values):
            raise HTTPException(422, f"В наборе «{d.name}» нет проб со значением «{name}» и положением на скважине")


def _clean(a: np.ndarray) -> list:
    return [None if not math.isfinite(x) else float(x) for x in np.asarray(a, dtype=np.float64)]


@router.post("/stats/swath")
def swath_plot(body: SwathIn, session: Session = Depends(get_session)) -> dict:
    try:
        f = sw.frame(body.direction, body.azimuth)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    loaded: list[_Blocks | _Samples] = [
        _Blocks(session, s) if isinstance(s, BlockSeries) else _Samples(session, s) for s in body.series
    ]
    models = [x for x in loaded if isinstance(x, _Blocks)]

    # The cuboid of the swaths: around the blocks of every model; without a model, around the samples.
    if models:
        each = np.array([sw.bounds(f, m.centres, m.sizes) for m in models])
    else:
        each = np.array([sw.bounds(f, x.points) for x in loaded])
    box = np.column_stack([each[:, :, 0].min(axis=0), each[:, :, 1].max(axis=0)])

    block = None
    if models:
        parent = np.asarray(models[0].grid.get("parent") or models[0].sizes.max(axis=0), dtype=np.float64)
        # Along an azimuth a block has no single size: the larger of its horizontal sides.
        block = float(parent[{"x": 0, "y": 1, "z": 2}[f.direction]]) if f.direction != "azimuth" else float(max(parent[0], parent[1]))
    width = body.width if body.width is not None else sw.default_width(float(box[0, 1] - box[0, 0]), block)
    try:
        start, n = sw.layout(float(box[0, 0]), float(box[0, 1]), width, body.anchor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    box[0] = start, start + n * width
    tol = 1e-9 * max(1.0, float(np.abs(box).max()))

    series = []
    for i, (spec, x) in enumerate(zip(body.series, loaded)):
        out = {"index": i, "kind": spec.kind, "name": x.name, "field": x.field, "unit": x.unit,
               "weight": spec.weight, "weight_name": x.weight_name}
        if isinstance(x, _Blocks):
            r = sw.swath_means(x.centres @ f.u, x.values, x.weights, start, width, n)
            out.update(weight_unit=x.weight_unit, dataset_id=x.dataset.id,
                       counts={"blocks": x.blocks_total, "selected": int(len(x.values)), "used": r["total"]["count"],
                               "empty": int((~np.isfinite(x.values)).sum()), "no_weight": int((~(np.isfinite(x.weights) & (x.weights > 0))).sum())})
        else:
            pts = x.points
            proj = pts @ f.axes.T
            inside = ((proj >= box[:, 0] - tol) & (proj <= box[:, 1] + tol)).all(axis=1)
            counts = {**x.counts, "outside_box": int((~inside).sum()), "outside_blocks": 0}
            within = "box"
            if spec.within == "blocks":
                if not models:
                    raise HTTPException(422, "Отбор проб по блокам: добавьте ряд блочной модели")
                k = spec.within_series
                target = loaded[k] if k is not None and k < len(loaded) else None
                if k is None:
                    target = models[0]
                if not isinstance(target, _Blocks):
                    raise HTTPException(422, f"Ряд {(k or 0) + 1} — не блочная модель: по нему нельзя отбирать пробы")
                g = target.grid
                in_blocks = sw.points_in_blocks(pts, target.centres, target.sizes,
                                                g.get("origin") or (target.centres - target.sizes / 2).min(axis=0),
                                                g.get("parent") or target.sizes.max(axis=0))
                counts["outside_blocks"] = int((inside & ~in_blocks).sum())
                inside &= in_blocks
                within = target.name
            r = sw.swath_means(proj[inside, 0], x.values[inside], x.weights[inside], start, width, n)
            counts["used"] = r["total"]["count"]
            out.update(weight_unit="м" if spec.weight == "length" else "", dataset_id=spec.set_id, counts=counts, within=within)
        out.update(mean=_clean(r["mean"]), count=[int(c) for c in r["count"]], weights=_clean(r["weight"]), total=r["total"])
        series.append(out)

    notes = []
    if block and width < block * (1 - 1e-9):
        notes.append(f"Полоса ({width:g} м) уже блока ({block:g} м): блок целиком идёт в полосу своего центра, часть полос пуста.")
    return {
        "direction": f.direction, "azimuth": f.azimuth,
        "frame": {"u": f.u.tolist(), "v": f.v.tolist(), "w": f.w.tolist()},
        # the cuboid in the frame: its extent along u, v and w
        "box": {"u": box[0].tolist(), "v": box[1].tolist(), "w": box[2].tolist()},
        "start": start, "width": width, "count": n,
        "centres": [start + (k + 0.5) * width for k in range(n)],
        "block": block,
        "series": series,
        "notes": notes,
    }


# ------------------------------------------------------------------- saved plots (project tree)
class SwathSaveIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    settings: dict = Field(default_factory=dict)


class SwathPatch(BaseModel):
    settings: dict


def get_swath(session: Session, dataset_id: int) -> Dataset:
    d = get_dataset(session, dataset_id)
    if d.kind != SWATH_KIND:
        raise HTTPException(404, "Swath plot не найден")
    return d


@router.post("/projects/{project_id}/swaths", response_model=DatasetOut, status_code=201)
def create_swath(project_id: int, body: SwathSaveIn, session: Session = Depends(get_session)):
    """A swath plot as an object of the project tree (section «Аудит»): its settings, counted anew when opened."""
    get_project(session, project_id)
    d = Dataset(project_id=project_id, kind=SWATH_KIND, name=body.name.strip(), meta={"settings": body.settings})
    session.add(d)
    session.commit()
    return dataset_out(session, d)


@router.patch("/swaths/{dataset_id}", response_model=DatasetOut)
def patch_swath(dataset_id: int, body: SwathPatch, session: Session = Depends(get_session)):
    d = get_swath(session, dataset_id)
    d.meta = {**(d.meta or {}), "settings": body.settings}
    session.commit()
    return dataset_out(session, d)
