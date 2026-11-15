"""Colourings by grade classes saved in a project (to load into other columns).

    GET    /api/projects/{id}/palettes
    PUT    /api/projects/{id}/palettes      {name, classes}   creates, or replaces the one with this name
    DELETE /api/palettes/{id}
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Palette
from app.routers.projects import get_project

router = APIRouter(prefix="/api", tags=["palettes"])


class ColorClass(BaseModel):
    # lower bound of the class; null: everything below the next class
    from_: float | None = Field(None, alias="from")
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    label: str = Field("", max_length=64)

    model_config = {"populate_by_name": True}


class PaletteIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    classes: list[ColorClass] = Field(min_length=1, max_length=40)


def _out(p: Palette) -> dict:
    return {"id": p.id, "name": p.name, "classes": p.classes}


@router.get("/projects/{project_id}/palettes")
def list_palettes(project_id: int, session: Session = Depends(get_session)) -> list[dict]:
    get_project(session, project_id)
    return [_out(p) for p in session.scalars(select(Palette).where(Palette.project_id == project_id).order_by(Palette.name))]


@router.put("/projects/{project_id}/palettes")
def save_palette(project_id: int, body: PaletteIn, session: Session = Depends(get_session)) -> dict:
    get_project(session, project_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Нужно название раскраски")
    classes = [c.model_dump(by_alias=True) for c in body.classes]
    bounds = [c["from"] for c in classes if c["from"] is not None]
    if bounds != sorted(bounds) or len(set(bounds)) != len(bounds) or any(c["from"] is None for c in classes[1:]):
        raise HTTPException(422, "Границы классов должны возрастать; без нижней границы может быть только первый класс")
    p = session.scalar(select(Palette).where(Palette.project_id == project_id, Palette.name == name))
    if p is None:
        p = Palette(project_id=project_id, name=name)
        session.add(p)
    p.classes = classes
    session.commit()
    return _out(p)


@router.delete("/palettes/{palette_id}", status_code=204)
def delete_palette(palette_id: int, session: Session = Depends(get_session)) -> None:
    p = session.get(Palette, palette_id)
    if p is None:
        raise HTTPException(404, "Раскраска не найдена")
    session.delete(p)
    session.commit()
