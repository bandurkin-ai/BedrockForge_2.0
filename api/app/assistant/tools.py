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

from app.models import Assay
from app.routers import audit as audit_api
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


def project_summary(session: Session, project_id: int) -> dict:
    return _dump(projects_api.summary(project_id, session))


def list_holes(
    session: Session,
    project_id: int,
    search: str | None = None,
    with_findings_only: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    holes = projects_api.list_holes(project_id, session)
    if search:
        needle = search.lower()
        holes = [h for h in holes if needle in h["hole_id"].lower()]
    if with_findings_only:
        holes = [h for h in holes if h["error"] + h["warning"] > 0]
    page = holes[max(0, offset): max(0, offset) + _limit(limit, 100)]
    return {"total": len(holes), "holes": page}


def get_hole(session: Session, project_id: int, hole_id: str) -> dict:
    return _dump(projects_api.hole_detail(project_id, hole_id, session))


def audit_summary(session: Session, project_id: int) -> dict:
    run = audit_api.latest_audit(project_id, session)
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
) -> dict:
    page = audit_api.findings(
        project_id, severity=severity, table=table, code=code, hole_id=None, hole=hole_id,
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
) -> dict:
    if kind not in ("collar", "survey", "assay"):
        raise HTTPException(422, "kind: collar, survey или assay")
    page = projects_api.table_page(
        project_id, kind, hole_id=hole_id, limit=_limit(limit), offset=max(0, offset), session=session,
    )
    return _dump(page)


def component_stats(session: Session, project_id: int, component: str, hole_id: str | None = None) -> dict:
    """Distribution of one assay component, length-weighted mean included."""
    projects_api.get_project(session, project_id)
    conditions = [Assay.project_id == project_id]
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
_LIMIT = {"type": "integer", "description": f"Сколько строк вернуть (до {MAX_ROWS})"}
_OFFSET = {"type": "integer", "description": "Сколько строк пропустить"}

DATA_TOOLS: list[DataTool] = [
    DataTool(
        "data_project_summary", "Сводка проекта",
        "Число устьев, замеров инклинометрии и проб, список компонентов с единицами, история импорта.",
        {}, (), project_summary,
    ),
    DataTool(
        "data_list_holes", "Список скважин",
        "Скважины проекта: координаты устья, глубина, статус, число замеров и проб, число замечаний аудита "
        "(error/warning/info). search — подстрока номера; with_findings_only — только с ошибками и предупреждениями.",
        {"search": {"type": "string"}, "with_findings_only": {"type": "boolean"}, "limit": _LIMIT, "offset": _OFFSET},
        (), list_holes,
    ),
    DataTool(
        "data_get_hole", "Карточка скважины",
        "Все данные устья одной скважины, дополнительные атрибуты и её замечания аудита.",
        {"hole_id": _HOLE}, ("hole_id",), get_hole,
    ),
    DataTool(
        "data_audit_summary", "Итоги аудита",
        "Последний запуск аудита: число находок по важности, сработавшие правила с описанием, признак устаревания.",
        {}, (), audit_summary,
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
        },
        (), audit_findings,
    ),
    DataTool(
        "data_query_table", "Строки таблицы",
        "Строки исходных данных: collar — устья, survey — инклинометрия, assay — пробы с содержаниями. "
        "Для survey и assay задавайте hole_id: таблицы большие.",
        {"kind": {"type": "string", "enum": ["collar", "survey", "assay"]}, "hole_id": _HOLE, "limit": _LIMIT, "offset": _OFFSET},
        ("kind",), query_table,
    ),
    DataTool(
        "data_component_stats", "Статистика компонента",
        "Распределение содержаний компонента по пробам (минимум, максимум, перцентили, среднее, "
        "средневзвешенное по длине) по проекту или по одной скважине.",
        {"component": {"type": "string", "description": "Имя компонента, например AU"}, "hole_id": _HOLE},
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
