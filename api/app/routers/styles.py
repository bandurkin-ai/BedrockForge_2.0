"""The styles library of a project: palettes, layer styles and display templates of the displays along holes.

    GET    /api/projects/{id}/styles?level=
    PUT    /api/projects/{id}/styles          {name, level, data_kind?, body, default_for?}
                                              creates, or replaces the one with this (level, name)
    PATCH  /api/styles/{id}                   {name?, body?, default_for?, data_kind?}
    DELETE /api/styles/{id}                   (the client unlinks the layers first)
    GET    /api/styles/{id}/usage             displays of the project that refer to the style
    GET    /api/projects/{id}/styles/export?ids=1,2     a .bfstyle.json file
    POST   /api/projects/{id}/styles/import   {file, conflicts, default}

The frontend owns the shape of `body`; the server checks only the classes and the category colours in it.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Chart, Dataset, Style
from app.routers.projects import get_project

router = APIRouter(prefix="/api", tags=["styles"])

Level = Literal["palette", "layer", "display"]
DataKind = Literal["number", "text", "event", "mixed"]
Decision = Literal["replace", "keep_both", "skip"]

LEVELS = ("palette", "layer", "display")
DATA_KINDS = ("number", "text", "event", "mixed")
FILE_FORMAT = "bedrockforge-styles"
FILE_VERSION = 1
MAX_NAME = 255
MAX_CLASSES = 40
MAX_LABEL = 64
MAX_BODY_BYTES = 256 * 1024
MAX_EXTRA_BYTES = 64 * 1024
MAX_DEFAULT_FOR = 50
MAX_FIELD_NAME = 64
MAX_IMPORT_STYLES = 1000
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# Keys of a file entry the server understands; the rest is kept in `extra`.
_ENTRY_KEYS = ("name", "level", "data_kind", "body", "default_for")
# Never taken from a file, even as extras: they belong to the row.
_ROW_KEYS = ("id", "project_id", "created_at", "updated_at", "extra")


class StyleIn(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    level: Level
    data_kind: DataKind = "number"
    body: dict
    default_for: list = Field(default_factory=list)


class StylePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=MAX_NAME)
    body: dict | None = None
    default_for: list | None = None
    data_kind: DataKind | None = None


class ImportIn(BaseModel):
    file: Any
    # "<level>:<name>" -> what to do with a style that already exists
    conflicts: dict[str, Decision] = Field(default_factory=dict)
    default: Decision = "skip"


def _out(s: Style) -> dict:
    return {"id": s.id, "name": s.name, "level": s.level, "data_kind": s.data_kind, "body": s.body,
            "default_for": s.default_for or [], "created_at": s.created_at, "updated_at": s.updated_at}


def _get(session: Session, style_id: int) -> Style:
    s = session.get(Style, style_id)
    if s is None:
        raise HTTPException(404, "Стиль не найден")
    return s


def _find(session: Session, project_id: int, level: str, name: str) -> Style | None:
    return session.scalar(select(Style).where(Style.project_id == project_id, Style.level == level, Style.name == name))


def _all(session: Session, project_id: int, level: str | None = None) -> list[Style]:
    q = select(Style).where(Style.project_id == project_id)
    if level is not None:
        q = q.where(Style.level == level)
    return list(session.scalars(q.order_by(Style.level, Style.name, Style.id)))


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_classes(classes: Any, where: str) -> str | None:
    """The rules of the old palettes: colours, at most 40 classes, ascending bounds."""
    if not isinstance(classes, list):
        return f"{where}: классы должны быть списком"
    if len(classes) > MAX_CLASSES:
        return f"{where}: не больше {MAX_CLASSES} классов"
    bounds = []
    for i, c in enumerate(classes):
        n = i + 1
        if not isinstance(c, dict):
            return f"{where}: класс {n} должен быть объектом"
        bound = c.get("from")
        if bound is not None and not _is_number(bound):
            return f"{where}: граница класса {n} должна быть числом"
        color = c.get("color")
        if not isinstance(color, str) or not _COLOR.match(color):
            return f"{where}: цвет класса {n} должен быть в виде #rrggbb"
        label = c.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > MAX_LABEL):
            return f"{where}: подпись класса {n} должна быть строкой не длиннее {MAX_LABEL} символов"
        if "hidden" in c and not isinstance(c["hidden"], bool):
            return f"{where}: признак «скрыт» класса {n} должен быть true или false"
        if bound is None and i > 0:
            return f"{where}: границы классов должны возрастать; без нижней границы может быть только первый класс"
        if bound is not None:
            bounds.append(bound)
    if any(b <= a for a, b in zip(bounds, bounds[1:])):
        return f"{where}: границы классов должны возрастать; без нижней границы может быть только первый класс"
    return None


def _check_cat_colors(colors: Any, where: str) -> str | None:
    if not isinstance(colors, dict):
        return f"{where}: цвета категорий должны быть объектом"
    for key, color in colors.items():
        if not isinstance(color, str) or not _COLOR.match(color):
            return f"{where}: цвет категории «{key}» должен быть в виде #rrggbb"
    return None


def _check_part(part: dict, where: str) -> str | None:
    if "classes" in part and (error := _check_classes(part["classes"], where)):
        return error
    if "catColors" in part and (error := _check_cat_colors(part["catColors"], where)):
        return error
    return None


def body_error(body: Any) -> str | None:
    """What is wrong with the body of a style, or None."""
    if not isinstance(body, dict):
        return "содержимое стиля должно быть объектом"
    if _json_size(body) > MAX_BODY_BYTES:
        return f"стиль слишком большой (больше {MAX_BODY_BYTES // 1024} КБ)"
    if error := _check_part(body, "раскраска"):
        return error
    tracks = body.get("tracks")
    if isinstance(tracks, list):
        for i, track in enumerate(tracks):
            if isinstance(track, dict) and (error := _check_part(track, f"слой {i + 1}")):
                return error
    return None


def default_for_error(value: Any) -> str | None:
    if not isinstance(value, list):
        return "список полей по умолчанию должен быть списком строк"
    if len(value) > MAX_DEFAULT_FOR:
        return f"не больше {MAX_DEFAULT_FOR} полей по умолчанию"
    if any(not isinstance(v, str) or not v or len(v) > MAX_FIELD_NAME for v in value):
        return f"имена полей по умолчанию должны быть непустыми строками не длиннее {MAX_FIELD_NAME} символов"
    return None


def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise HTTPException(422, "Нужно название стиля")
    return name


def _checked(error: str | None, name: str | None = None) -> None:
    if error:
        raise HTTPException(422, f"Стиль «{name}»: {error}" if name else error[0].upper() + error[1:])


@router.get("/projects/{project_id}/styles")
def list_styles(project_id: int, level: Level | None = Query(None), session: Session = Depends(get_session)) -> list[dict]:
    get_project(session, project_id)
    return [_out(s) for s in _all(session, project_id, level)]


@router.put("/projects/{project_id}/styles")
def save_style(project_id: int, body: StyleIn, session: Session = Depends(get_session)) -> dict:
    get_project(session, project_id)
    name = _clean_name(body.name)
    _checked(body_error(body.body))
    _checked(default_for_error(body.default_for))
    s = _find(session, project_id, body.level, name)
    if s is None:
        s = Style(project_id=project_id, level=body.level, name=name, extra={})
        session.add(s)
    s.data_kind = body.data_kind
    s.body = body.body
    s.default_for = body.default_for
    session.commit()
    return _out(s)


@router.get("/projects/{project_id}/styles/export")
def export_styles(project_id: int, ids: str | None = Query(None), session: Session = Depends(get_session)) -> JSONResponse:
    get_project(session, project_id)
    styles = _all(session, project_id)
    if ids is not None:
        try:
            wanted = {int(v) for v in ids.split(",") if v.strip()}
        except ValueError:
            raise HTTPException(422, "Список стилей: нужны номера через запятую") from None
        missing = wanted - {s.id for s in styles}
        if missing:
            raise HTTPException(404, "Стиль не найден: " + ", ".join(str(i) for i in sorted(missing)))
        styles = [s for s in styles if s.id in wanted]
    entries = []
    for s in styles:
        entry = {k: v for k, v in (s.extra or {}).items() if k not in _ENTRY_KEYS and k not in _ROW_KEYS}
        entry.update(name=s.name, level=s.level, data_kind=s.data_kind, body=s.body, default_for=s.default_for or [])
        entries.append(entry)
    return JSONResponse(
        {"format": FILE_FORMAT, "version": FILE_VERSION, "styles": entries},
        headers={"Content-Disposition": 'attachment; filename="styles.bfstyle.json"'},
    )


def _parse_file(file: Any) -> list[dict]:
    """The entries of a styles file, checked; 422 names the style that is wrong."""
    if not isinstance(file, dict) or file.get("format") != FILE_FORMAT:
        raise HTTPException(422, "Это не файл стилей BedrockForge")
    version = file.get("version")
    if isinstance(version, bool) or version != FILE_VERSION:
        raise HTTPException(422, f"Неизвестная версия файла стилей: {version}")
    raw = file.get("styles")
    if not isinstance(raw, list):
        raise HTTPException(422, "В файле стилей нет списка стилей")
    if len(raw) > MAX_IMPORT_STYLES:
        raise HTTPException(422, f"В файле слишком много стилей (больше {MAX_IMPORT_STYLES})")
    entries = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            raise HTTPException(422, f"Стиль {i + 1} в файле: должен быть объектом")
        name = e.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME:
            raise HTTPException(422, f"Стиль {i + 1} в файле: нужно название не длиннее {MAX_NAME} символов")
        name = name.strip()
        if e.get("level") not in LEVELS:
            raise HTTPException(422, f"Стиль «{name}»: неизвестный уровень «{e.get('level')}»")
        data_kind = e.get("data_kind", "number")
        if data_kind not in DATA_KINDS:
            raise HTTPException(422, f"Стиль «{name}»: неизвестный вид данных «{data_kind}»")
        default_for = e.get("default_for", [])
        _checked(body_error(e.get("body")), name)
        _checked(default_for_error(default_for), name)
        extra = {k: v for k, v in e.items() if k not in _ENTRY_KEYS and k not in _ROW_KEYS}
        if _json_size(extra) > MAX_EXTRA_BYTES:
            raise HTTPException(422, f"Стиль «{name}»: слишком много дополнительных данных")
        entries.append({"name": name, "level": e["level"], "data_kind": data_kind, "body": e["body"],
                        "default_for": default_for, "extra": extra})
    return entries


def _free_name(name: str, taken: set[str]) -> str:
    """ "name (2)", "name (3)", ...: the first one not taken."""
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = name[: MAX_NAME - len(suffix)].rstrip() + suffix
        if candidate not in taken:
            return candidate
        n += 1


@router.post("/projects/{project_id}/styles/import")
def import_styles(project_id: int, body: ImportIn, session: Session = Depends(get_session)) -> dict:
    get_project(session, project_id)
    entries = _parse_file(body.file)
    existing = {(s.level, s.name): s for s in _all(session, project_id)}
    created = replaced = skipped = 0
    renamed = []
    for e in entries:
        level, name = e["level"], e["name"]
        current = existing.get((level, name))
        if current is not None:
            decision = body.conflicts.get(f"{level}:{name}", body.default)
            if decision == "skip":
                skipped += 1
                continue
            if decision == "replace":
                current.data_kind = e["data_kind"]
                current.body = e["body"]
                current.default_for = e["default_for"]
                current.extra = e["extra"]
                replaced += 1
                continue
            name = _free_name(name, {n for lv, n in existing if lv == level})
            renamed.append({"from": e["name"], "to": name, "level": level})
        s = Style(project_id=project_id, level=level, name=name, data_kind=e["data_kind"], body=e["body"],
                  default_for=e["default_for"], extra=e["extra"])
        session.add(s)
        existing[(level, name)] = s
        created += 1
    session.commit()
    return {"created": created, "replaced": replaced, "skipped": skipped, "renamed": renamed,
            "styles": [_out(s) for s in _all(session, project_id)]}


@router.patch("/styles/{style_id}")
def patch_style(style_id: int, body: StylePatch, session: Session = Depends(get_session)) -> dict:
    s = _get(session, style_id)
    if body.name is not None:
        name = _clean_name(body.name)
        other = _find(session, s.project_id, s.level, name)
        if other is not None and other.id != s.id:
            raise HTTPException(409, f"Стиль с названием «{name}» уже есть в библиотеке")
        s.name = name
    if body.body is not None:
        _checked(body_error(body.body))
        s.body = body.body
    if body.default_for is not None:
        _checked(default_for_error(body.default_for))
        s.default_for = body.default_for
    if body.data_kind is not None:
        s.data_kind = body.data_kind
    session.commit()
    return _out(s)


@router.delete("/styles/{style_id}", status_code=204)
def delete_style(style_id: int, session: Session = Depends(get_session)) -> None:
    session.delete(_get(session, style_id))
    session.commit()


def _refers(ref: Any, style_id: int) -> bool:
    if not isinstance(ref, dict):
        return False
    rid = ref.get("id")
    return isinstance(rid, int) and not isinstance(rid, bool) and rid == style_id


@router.get("/styles/{style_id}/usage")
def style_usage(style_id: int, session: Session = Depends(get_session)) -> list[dict]:
    """Displays of the project where the style is linked or was the source of the settings."""
    s = _get(session, style_id)
    charts = session.scalars(
        select(Chart).join(Dataset, Dataset.id == Chart.dataset_id)
        .where(Dataset.project_id == s.project_id, Chart.kind == "downhole").order_by(Chart.id)
    )
    used = []
    for c in charts:
        settings = c.settings if isinstance(c.settings, dict) else {}
        refs = []
        fields: list[str] = []
        if _refers(settings.get("template"), style_id):
            refs.append(settings["template"])
        tracks = settings.get("tracks")
        for track in tracks if isinstance(tracks, list) else []:
            if not isinstance(track, dict):
                continue
            mine = [track[k] for k in ("style", "palette") if _refers(track.get(k), style_id)]
            if mine:
                refs.extend(mine)
                field = track.get("field")
                if isinstance(field, str) and field not in fields:
                    fields.append(field)
        if refs:
            used.append({"chart_id": c.id, "name": c.name, "set_id": c.dataset_id,
                         "linked": any(r.get("linked") is True for r in refs), "tracks": fields})
    return used
