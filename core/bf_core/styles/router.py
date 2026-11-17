"""/api/v1/styles — the style library (ТЗ 7.16, ADR 0007 §3)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

from . import files, service

router = APIRouter(prefix="/styles")
Svc = Annotated[ProjectService, Depends(projects)]
Level = Literal["palette", "layer", "display"]
DataKind = Literal["number", "text", "event", "mixed"]
Decision = Literal["replace", "keep_both", "skip"]


def _errors(fn: Any) -> Any:
    try:
        return fn()
    except service.StyleError as e:
        text = str(e)
        raise ApiError(422, "bad_style", text[:1].upper() + text[1:] + ".", "Исправьте стиль и сохраните снова.") from e
    except files.FileError as e:
        raise ApiError(422, "bad_style_file", str(e), "Выберите файл .bfstyle.json, сохранённый программой.") from e
    except service.NotFound as e:
        raise ApiError(404, "style_not_found", "Стиль не найден.", "Обновите библиотеку стилей.") from e
    except service.NameTaken as e:
        raise ApiError(
            409, "name_taken", f"Стиль «{e.name}» уже есть в библиотеке.", "Выберите другое название."
        ) from e
    except service.Conflict as e:
        raise ApiError(
            409,
            "version_conflict",
            "Стиль изменили, пока вы его правили.",
            "Стиль обновлён — повторите правку.",
            extra={"style": e.current},
        ) from e


@router.get("")
def list_all(svc: Svc, level: Level | None = None) -> dict[str, Any]:
    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        return {"styles": service.list_styles(conn, level)}


class StyleIn(BaseModel):
    name: str = Field(max_length=300)
    level: Level
    data_kind: DataKind = "number"
    body: dict[str, Any]
    default_for: list[str] = Field(default_factory=list, max_length=100)


@router.post("")
def create(svc: Svc, body: StyleIn) -> dict[str, Any]:
    project = svc.require_current(writable=True)
    from bf_core.drillholes.edit import author as who

    author = who()

    def go() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return service.create(conn, **body.model_dump(), author=author)

    return _errors(go)  # type: ignore[no-any-return]


class StylePatch(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    data_kind: DataKind | None = None
    body: dict[str, Any] | None = None
    default_for: list[str] | None = Field(default=None, max_length=100)
    expected_version: int = Field(ge=1)


@router.patch("/{style_id}")
def change(svc: Svc, style_id: int, body: StylePatch) -> dict[str, Any]:
    project = svc.require_current(writable=True)

    def go() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            changes = body.model_dump(exclude={"expected_version"}, exclude_none=True)
            return service.patch(conn, style_id, changes, body.expected_version)

    return _errors(go)  # type: ignore[no-any-return]


@router.delete("/{style_id}")
def delete(svc: Svc, style_id: int) -> dict[str, Any]:
    """Displays that used the style keep its settings as their copy (ТЗ 7.16); their ids are returned."""
    project = svc.require_current(writable=True)

    def go() -> dict[str, Any]:
        with dialect.write_tx(project.engine) as conn:
            return {"charts": service.delete(conn, style_id)}

    return _errors(go)  # type: ignore[no-any-return]


@router.get("/{style_id}/usage")
def usage(svc: Svc, style_id: int) -> dict[str, Any]:
    project = svc.require_current()

    def go() -> dict[str, Any]:
        with dialect.read_tx(project.engine) as conn:
            return {"usage": service.usage(conn, style_id)}

    return _errors(go)  # type: ignore[no-any-return]


class ExportBody(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=1000)


@router.post("/export")
def export(svc: Svc, body: ExportBody) -> dict[str, Any]:
    """The content of a .bfstyle.json file; the interface writes it through the save dialog."""
    project = svc.require_current()

    def go() -> dict[str, Any]:
        with dialect.read_tx(project.engine) as conn:
            return {"name": "styles.bfstyle.json", "file": files.export_file(conn, body.ids)}

    return _errors(go)  # type: ignore[no-any-return]


class ImportBody(BaseModel):
    file: Any
    conflicts: dict[str, Decision] = Field(default_factory=dict)
    default: Decision = "skip"


@router.post("/import")
def import_(svc: Svc, body: ImportBody, preview: Annotated[bool, Query()] = False) -> dict[str, Any]:
    """`preview=true`: only the names already in the library (to ask what to do with each)."""
    project = svc.require_current(writable=not preview)

    def go() -> dict[str, Any]:
        if preview:
            entries = files.parse(body.file)
            with dialect.read_tx(project.engine) as conn:
                have = {(s["level"], dialect.name_key(s["name"])) for s in service.list_styles(conn)}
            clash = [f"{e['level']}:{e['name']}" for e in entries if (e["level"], dialect.name_key(e["name"])) in have]
            return {"styles": len(entries), "conflicts": clash}
        with dialect.write_tx(project.engine) as conn:
            res = files.import_file(conn, body.file, body.conflicts, body.default)
            return {**res, "styles": service.list_styles(conn)}

    return _errors(go)  # type: ignore[no-any-return]
