"""MCP server: lets an external Claude client (claude.ai, Claude Desktop, Claude Code) work with BedrockForge.

Two groups of tools:
- data tools read the projects straight from the database (the same functions the built-in assistant uses);
- ui tools drive the application open in the user's browser through app.ui_bridge.

Served over streamable HTTP at /mcp of the API.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select

from app import db, ui_bridge
from app.assistant import tools
from app.config import settings
from app.models import Project

mcp = MCPServer(
    "BedrockForge",
    instructions=(
        "BedrockForge — ГГИС для данных бурения: устья, инклинометрия, пробы, аудит качества данных. "
        "Начните с list_projects, затем передавайте project_id в остальные инструменты. "
        "Инструменты data_* читают данные. Инструменты ui_* управляют приложением, открытым у пользователя "
        "в браузере: сначала ui_list_commands, затем ui_run_command."
    ),
)


async def _data(name: str, project_id: int, **args: Any) -> str:
    """Run a data tool in a worker thread with its own database session."""

    def call() -> str:
        with db.SessionLocal() as session:
            text, _ = tools.run_data_tool(session, project_id, name, {k: v for k, v in args.items() if v is not None})
            return text

    return await anyio.to_thread.run_sync(call)


@mcp.tool()
async def list_projects() -> str:
    """Проекты BedrockForge: id и название. С этого начинается работа."""

    def call() -> str:
        with db.SessionLocal() as session:
            projects = session.scalars(select(Project).order_by(Project.id)).all()
            return json.dumps([{"id": p.id, "name": p.name} for p in projects], ensure_ascii=False)

    return await anyio.to_thread.run_sync(call)


@mcp.tool()
async def data_project_summary(project_id: int) -> str:
    """Число устьев, замеров инклинометрии и проб, список компонентов с единицами, история импорта."""
    return await _data("data_project_summary", project_id)


@mcp.tool()
async def data_list_holes(
    project_id: int, search: str | None = None, with_findings_only: bool = False, limit: int = 100, offset: int = 0
) -> str:
    """Скважины проекта: координаты устья, глубина, статус, число замеров и проб, число замечаний аудита.

    search — подстрока номера; with_findings_only — только скважины с ошибками и предупреждениями аудита.
    """
    return await _data(
        "data_list_holes", project_id, search=search, with_findings_only=with_findings_only, limit=limit, offset=offset
    )


@mcp.tool()
async def data_get_hole(project_id: int, hole_id: str) -> str:
    """Все данные устья одной скважины, дополнительные атрибуты и её замечания аудита."""
    return await _data("data_get_hole", project_id, hole_id=hole_id)


@mcp.tool()
async def data_audit_summary(project_id: int) -> str:
    """Последний запуск аудита: число находок по важности, сработавшие правила, признак устаревания."""
    return await _data("data_audit_summary", project_id)


@mcp.tool()
async def data_audit_findings(
    project_id: int,
    severity: Literal["error", "warning", "info"] | None = None,
    table: Literal["collar", "survey", "assay"] | None = None,
    code: str | None = None,
    hole_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Находки последнего аудита с примерами строк. code — код правила из data_audit_summary."""
    return await _data(
        "data_audit_findings", project_id,
        severity=severity, table=table, code=code, hole_id=hole_id, limit=limit, offset=offset,
    )


@mcp.tool()
async def data_query_table(
    project_id: int, kind: Literal["collar", "survey", "assay"], hole_id: str | None = None, limit: int = 50, offset: int = 0
) -> str:
    """Строки исходных данных: collar — устья, survey — инклинометрия, assay — пробы с содержаниями.

    Для survey и assay задавайте hole_id: таблицы большие.
    """
    return await _data("data_query_table", project_id, kind=kind, hole_id=hole_id, limit=limit, offset=offset)


@mcp.tool()
async def data_component_stats(project_id: int, component: str, hole_id: str | None = None) -> str:
    """Распределение содержаний компонента по пробам: перцентили, среднее, средневзвешенное по длине."""
    return await _data("data_component_stats", project_id, component=component, hole_id=hole_id)


_NO_BROWSER = (
    "Приложение BedrockForge с этим проектом не открыто в браузере. "
    "Попросите пользователя открыть его и выбрать проект, затем повторите."
)


@mcp.tool()
async def ui_list_commands(project_id: int) -> str:
    """Команды интерфейса, доступные в открытом у пользователя приложении: id, описание, параметры.

    Команды с "confirm": true меняют данные — приложение попросит пользователя подтвердить их.
    """
    try:
        return json.dumps(ui_bridge.list_commands(project_id), ensure_ascii=False)
    except ui_bridge.NoBrowser:
        return _NO_BROWSER


@mcp.tool()
async def ui_run_command(project_id: int, command: str, args: dict[str, Any] | None = None) -> str:
    """Выполняет команду интерфейса в браузере пользователя и возвращает её результат.

    command — id из ui_list_commands (например "view.open", "hole.select", "layout.set"),
    args — её параметры. Текущее состояние экрана возвращает команда "workspace.state".
    """
    try:
        content, ok = await ui_bridge.run_command(project_id, command, args or {})
    except ui_bridge.NoBrowser:
        return _NO_BROWSER
    return content if ok else f"Ошибка: {content}"


def http_app():
    """The ASGI app for /mcp. Its session manager must be running: see the lifespan in app.main."""
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.mcp_allowed_hosts),
        ),
    )
