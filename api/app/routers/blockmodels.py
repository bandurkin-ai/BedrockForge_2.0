"""Block models: attribute values for the viewer, a block by its number, the grade–tonnage report."""

from __future__ import annotations

from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.blocks.report import Grade, grade_tonnage
from app.db import get_session
from app.models import Dataset
from app.routers.datasets import get_dataset
from app.storage import blobs

router = APIRouter(prefix="/api/blockmodels", tags=["blockmodels"])


def get_model(session: Session, dataset_id: int) -> Dataset:
    d = get_dataset(session, dataset_id)
    if d.kind != "blocks":
        raise HTTPException(404, "Блочная модель не найдена")
    return d


def attribute(d: Dataset, key: str) -> dict:
    a = next((a for a in d.meta.get("attributes", []) if a["key"] == key or a["name"] == key), None)
    if a is None:
        raise HTTPException(422, f"Поля «{key}» нет в модели")
    return a


def column(d: Dataset, a: dict) -> np.ndarray:
    return blobs.read_array(d.blobs[a["key"]], "<f8" if a["kind"] == "number" else "<i4")


def volumes(d: Dataset) -> np.ndarray:
    s = blobs.read_array(d.blobs["sizes"], "<f4", 3).astype(np.float64)
    return s[:, 0] * s[:, 1] * s[:, 2]


@router.get("/{dataset_id}/render")
def render_copy(dataset_id: int, session: Session = Depends(get_session)) -> Response:
    """float32 per block: centre relative to meta.render_origin, then size (6 numbers)."""
    from fastapi.responses import FileResponse

    d = get_model(session, dataset_id)
    sha = d.blobs["render"]
    return FileResponse(blobs.path_of(sha), media_type="application/octet-stream",
                        headers={"ETag": f'"{sha}"', "Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/{dataset_id}/values/{key}")
def attribute_values(dataset_id: int, key: str, session: Session = Depends(get_session)) -> Response:
    """Values of an attribute as float32 per block (categories: their code, empty = NaN), for colouring."""
    d = get_model(session, dataset_id)
    a = attribute(d, key)
    v = column(d, a)
    out = v.astype("<f4") if a["kind"] == "number" else np.where(v < 0, np.nan, v).astype("<f4")
    return Response(out.tobytes(), media_type="application/octet-stream", headers={"Cache-Control": "no-cache"})


@router.get("/{dataset_id}/blocks/{index}")
def block(dataset_id: int, index: int, session: Session = Depends(get_session)) -> dict:
    """One block: centre, size and every attribute (the viewer's click)."""
    d = get_model(session, dataset_id)
    n = d.meta.get("block_count", 0)
    if not 0 <= index < n:
        raise HTTPException(404, "Блока с таким номером нет")
    c = blobs.read_array(d.blobs["centres"], "<f8", 3)[index]
    s = blobs.read_array(d.blobs["sizes"], "<f4", 3)[index]
    values = {}
    for a in d.meta.get("attributes", []):
        v = column(d, a)[index]
        if a["kind"] == "number":
            values[a["name"]] = None if not np.isfinite(v) else float(v)
        else:
            values[a["name"]] = a["labels"][int(v)] if v >= 0 else None
    return {"index": index, "centre": [float(x) for x in c], "size": [float(x) for x in s], "values": values}


class Filter(BaseModel):
    key: str
    labels: list[str] | None = None
    min: float | None = None
    max: float | None = None


class ReportIn(BaseModel):
    # A density field, or a constant (t/m³) for every block.
    density: str | None = None
    density_value: float | None = Field(None, gt=0)
    grades: list[str] = Field(default_factory=list, max_length=20)
    cutoff_field: str | None = None
    cutoffs: list[float] = Field(default_factory=list, max_length=100)
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    group_by: str | None = None
    # Remember the density and grades as the model's defaults.
    remember: bool = False

    @model_validator(mode="after")
    def _density(self):
        if not self.density and self.density_value is None:
            raise ValueError("Укажите поле плотности или постоянную плотность")
        return self


@router.post("/{dataset_id}/report")
def report(dataset_id: int, body: ReportIn, session: Session = Depends(get_session)) -> dict:
    d = get_model(session, dataset_id)
    vol = volumes(d)
    n = len(vol)
    if body.density:
        da = attribute(d, body.density)
        if da["kind"] != "number":
            raise HTTPException(422, "Плотность должна быть числовым полем")
        density = np.asarray(column(d, da), dtype=np.float64)
    else:
        density = np.full(n, float(body.density_value))
    grades = []
    for key in body.grades:
        a = attribute(d, key)
        if a["kind"] != "number":
            raise HTTPException(422, f"Поле {a['name']} не числовое")
        grades.append(Grade(a["key"], a["name"], np.asarray(column(d, a)), a.get("unit")))
    cutoff = None
    cutoffs: list[float | None] = [None]
    if body.cutoff_field:
        ca = attribute(d, body.cutoff_field)
        if ca["kind"] != "number":
            raise HTTPException(422, "Поле борта должно быть числовым")
        cutoff = np.asarray(column(d, ca))
        cutoffs = sorted(set(body.cutoffs)) or [0.0]
    mask = np.ones(n, dtype=bool)
    for f in body.filters:
        a = attribute(d, f.key)
        v = column(d, a)
        if a["kind"] == "category":
            wanted = [i for i, label in enumerate(a["labels"]) if label in set(f.labels or [])]
            mask &= np.isin(v, wanted)
        else:
            if f.min is not None:
                mask &= v >= f.min
            if f.max is not None:
                mask &= v <= f.max
    groups = None
    if body.group_by:
        ga = attribute(d, body.group_by)
        if ga["kind"] == "category":
            groups = (np.asarray(column(d, ga)), ga["labels"])
        else:
            v = np.asarray(column(d, ga))
            u = np.unique(v[np.isfinite(v)])
            if len(u) > 50:
                raise HTTPException(422, f"У поля {ga['name']} больше 50 значений: группировать по нему нельзя")
            codes = np.full(n, -1, dtype=np.int32)
            for i, x in enumerate(u):
                codes[v == x] = i
            groups = (codes, [f"{x:g}" for x in u])
    result = grade_tonnage(vol, density, grades, cutoff=cutoff, cutoffs=cutoffs, mask=mask, groups=groups)
    result["grades"] = [{"key": g.key, "name": g.name, "unit": g.unit} for g in grades]
    result["density"] = attribute(d, body.density)["name"] if body.density else body.density_value
    if body.remember:
        d.meta = {**d.meta, "report": {"density": body.density, "density_value": body.density_value,
                                       "grades": body.grades, "cutoff_field": body.cutoff_field,
                                       "cutoffs": body.cutoffs}}
        session.commit()
    return result


class UnitIn(BaseModel):
    unit: Literal["g/t", "%", "ppm", "ppb"] | None


@router.patch("/{dataset_id}/attributes/{key}")
def set_unit(dataset_id: int, key: str, body: UnitIn, session: Session = Depends(get_session)) -> dict:
    """Unit of a grade field (decides how metal is counted)."""
    d = get_model(session, dataset_id)
    a = attribute(d, key)
    if a["kind"] != "number":
        raise HTTPException(422, "Единицы — только у числовых полей")
    d.meta = {**d.meta, "attributes": [{**x, "unit": body.unit} if x["key"] == a["key"] else x for x in d.meta["attributes"]]}
    session.commit()
    return {"key": a["key"], "unit": body.unit}
