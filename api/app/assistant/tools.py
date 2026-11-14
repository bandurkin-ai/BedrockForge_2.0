"""Read-only data tools for Claude.

One definition serves both entry points: the built-in assistant (Claude API tool use,
see service.py) and external Claude clients connected through the MCP server (mcp_server.py).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assay, Dataset
from app.routers import audit as audit_api
from app.routers import datasets as datasets_api
from app.routers import projects as projects_api
from app.routers import viz as viz_api

MAX_ROWS = 200


@dataclass(frozen=True)
class DataTool:
    name: str
    title: str
    description: str
    properties: dict[str, dict]
    required: tuple[str, ...]
    run: Callable[..., Any]

    def definition(self) -> dict:
        """Tool definition for the Claude Messages API."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": self.properties,
                "required": list(self.required),
                "additionalProperties": False,
            },
        }


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def _limit(value: int | None, default: int = 50) -> int:
    return max(1, min(MAX_ROWS, value or default))


def resolve_set(session: Session, project_id: int, set_id: int | None) -> int:
    """The drillhole set a tool works on: the given one (checked against the project) or the only one."""
    projects_api.get_project(session, project_id)
    sets = session.scalars(
        select(Dataset).where(Dataset.project_id == project_id, Dataset.kind == "drillholes").order_by(Dataset.id)
    ).all()
    if set_id is not None:
        if not any(d.id == set_id for d in sets):
            raise HTTPException(404, f"Набора скважин {set_id} нет в проекте")
        return set_id
    if len(sets) == 1:
        return sets[0].id
    if not sets:
        raise HTTPException(404, "В проекте нет наборов скважин")
    names = ", ".join(f"{d.id} «{d.name}»" for d in sets)
    raise HTTPException(422, f"В проекте несколько наборов скважин, укажите set_id: {names}")


def list_datasets(session: Session, project_id: int) -> list:
    """The project tree: drillhole sets with counts and audit totals, wireframes with size and volume."""
    return [_dump(d) for d in datasets_api.list_datasets(project_id, session)]


def project_summary(session: Session, project_id: int, set_id: int | None = None) -> dict:
    return _dump(projects_api.summary(resolve_set(session, project_id, set_id), session))


def list_holes(
    session: Session,
    project_id: int,
    search: str | None = None,
    with_findings_only: bool = False,
    limit: int | None = None,
    offset: int = 0,
    set_id: int | None = None,
) -> dict:
    holes = projects_api.list_holes(resolve_set(session, project_id, set_id), session)
    if search:
        needle = search.lower()
        holes = [h for h in holes if needle in h["hole_id"].lower()]
    if with_findings_only:
        holes = [h for h in holes if h["error"] + h["warning"] > 0]
    page = holes[max(0, offset): max(0, offset) + _limit(limit, 100)]
    return {"total": len(holes), "holes": page}


def get_hole(session: Session, project_id: int, hole_id: str, set_id: int | None = None) -> dict:
    return _dump(projects_api.hole_detail(resolve_set(session, project_id, set_id), hole_id, session))


def audit_summary(session: Session, project_id: int, set_id: int | None = None) -> dict:
    run = audit_api.latest_audit(resolve_set(session, project_id, set_id), session)
    if run is None:
        return {"audit": None, "note": "Аудит ещё не запускался"}
    out = _dump(run)
    # Rules that found nothing only add noise; their count is enough.
    passed = [r for r in out["rules"] if r["findings"] == 0]
    out["rules"] = [r for r in out["rules"] if r["findings"] > 0]
    out["passed_rules"] = len(passed)
    return out


def audit_findings(
    session: Session,
    project_id: int,
    severity: str | None = None,
    table: str | None = None,
    code: str | None = None,
    hole_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    set_id: int | None = None,
) -> dict:
    page = audit_api.findings(
        resolve_set(session, project_id, set_id), severity=severity, table=table, code=code, hole_id=None, hole=hole_id,
        limit=_limit(limit), offset=max(0, offset), session=session,
    )
    out = _dump(page)
    for item in out["items"]:
        examples = item["details"].get("examples") or []
        item["details"] = {"examples": examples[:5]}
    return out


def query_table(
    session: Session,
    project_id: int,
    kind: str,
    hole_id: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    set_id: int | None = None,
) -> dict:
    if kind not in ("collar", "survey", "assay"):
        raise HTTPException(422, "kind: collar, survey или assay")
    page = projects_api.table_page(
        resolve_set(session, project_id, set_id), kind, hole_id=hole_id, limit=_limit(limit), offset=max(0, offset), session=session,
    )
    return _dump(page)


def component_stats(
    session: Session, project_id: int, component: str, hole_id: str | None = None, set_id: int | None = None
) -> dict:
    """Distribution of one assay component, length-weighted mean included."""
    conditions = [Assay.set_id == resolve_set(session, project_id, set_id)]
    if hole_id:
        conditions.append(Assay.hole_id == hole_id)
    rows = session.execute(select(Assay.hole_id, Assay.from_m, Assay.to_m, Assay.analytes).where(*conditions)).all()
    values, lengths, holes = [], [], set()
    for hole, start, end, analytes in rows:
        value = analytes.get(component)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not np.isfinite(value):
            continue
        values.append(float(value))
        lengths.append(float(end - start) if start is not None and end is not None and end > start else 0.0)
        holes.add(hole)
    if not values:
        return {"component": component, "count": 0, "note": "Нет значений этого компонента"}
    v, w = np.array(values), np.array(lengths)
    stats = viz_api._stats(v).model_dump()
    stats.update(
        component=component,
        intervals_total=len(rows),
        holes=len(holes),
        mean=round(float(v.mean()), 6),
        length_weighted_mean=round(float((v * w).sum() / w.sum()), 6) if w.sum() > 0 else None,
        sampled_metres=round(float(w.sum()), 2),
    )
    return stats


_HOLE = {"type": "string", "description": "Точный номер скважины"}
_SET = {"type": "integer", "description": "Набор скважин (id из data_list_datasets); можно не задавать, если набор один"}
_LIMIT = {"type": "integer", "description": f"Сколько строк вернуть (до {MAX_ROWS})"}
_OFFSET = {"type": "integer", "description": "Сколько строк пропустить"}

DATA_TOOLS: list[DataTool] = [
    DataTool(
        "data_list_datasets", "Дерево проекта",
        "Все объекты проекта: наборы скважин (id, число устьев, замеров и проб, компоненты, итоги аудита) "
        "и каркасы (имя, число треугольников, площадь, объём, замкнутость, охват). Наборы скважин независимы: "
        "id набора передаётся в остальные инструменты как set_id.",
        {}, (), list_datasets,
    ),
    DataTool(
        "data_project_summary", "Сводка проекта",
        "Набор скважин: число устьев, замеров инклинометрии и проб, компоненты с единицами, история импорта.",
        {"set_id": _SET}, (), project_summary,
    ),
    DataTool(
        "data_list_holes", "Список скважин",
        "Скважины проекта: координаты устья, глубина, статус, число замеров и проб, число замечаний аудита "
        "(error/warning/info). search — подстрока номера; with_findings_only — только с ошибками и предупреждениями.",
        {"search": {"type": "string"}, "with_findings_only": {"type": "boolean"}, "limit": _LIMIT, "offset": _OFFSET, "set_id": _SET},
        (), list_holes,
    ),
    DataTool(
        "data_get_hole", "Карточка скважины",
        "Все данные устья одной скважины, дополнительные атрибуты и её замечания аудита.",
        {"hole_id": _HOLE, "set_id": _SET}, ("hole_id",), get_hole,
    ),
    DataTool(
        "data_audit_summary", "Итоги аудита",
        "Последний запуск аудита: число находок по важности, сработавшие правила с описанием, признак устаревания.",
        {"set_id": _SET}, (), audit_summary,
    ),
    DataTool(
        "data_audit_findings", "Находки аудита",
        "Находки последнего аудита с примерами строк. Фильтры: severity (error/warning/info), "
        "table (collar/survey/assay), code — код правила из итогов аудита, hole_id.",
        {
            "severity": {"type": "string", "enum": ["error", "warning", "info"]},
            "table": {"type": "string", "enum": ["collar", "survey", "assay"]},
            "code": {"type": "string"},
            "hole_id": _HOLE,
            "limit": _LIMIT,
            "offset": _OFFSET,
            "set_id": _SET,
        },
        (), audit_findings,
    ),
    DataTool(
        "data_query_table", "Строки таблицы",
        "Строки исходных данных: collar — устья, survey — инклинометрия, assay — пробы с содержаниями. "
        "Для survey и assay задавайте hole_id: таблицы большие.",
        {"kind": {"type": "string", "enum": ["collar", "survey", "assay"]}, "hole_id": _HOLE, "limit": _LIMIT, "offset": _OFFSET, "set_id": _SET},
        ("kind",), query_table,
    ),
    DataTool(
        "data_component_stats", "Статистика компонента",
        "Распределение содержаний компонента по пробам (минимум, максимум, перцентили, среднее, "
        "средневзвешенное по длине) по проекту или по одной скважине.",
        {"component": {"type": "string", "description": "Имя компонента, например AU"}, "hole_id": _HOLE, "set_id": _SET},
        ("component",), component_stats,
    ),
]

BY_NAME = {tool.name: tool for tool in DATA_TOOLS}


def run_data_tool(session: Session, project_id: int, name: str, args: dict) -> tuple[str, bool]:
    """Run a tool for Claude. Returns (JSON text, ok); failures come back as text so Claude can react."""
    tool = BY_NAME[name]
    unknown = set(args) - set(tool.properties)
    missing = set(tool.required) - set(args)
    if unknown or missing:
        problems = [f"неизвестные параметры: {', '.join(sorted(unknown))}"] if unknown else []
        problems += [f"не заданы: {', '.join(sorted(missing))}"] if missing else []
        return "Ошибка в аргументах — " + "; ".join(problems), False
    try:
        result = tool.run(session, project_id, **args)
    except HTTPException as e:
        return f"Ошибка: {e.detail}", False
    except (TypeError, ValueError) as e:
        return f"Ошибка в аргументах: {e}", False
    return json.dumps(result, ensure_ascii=False, default=str), True
