"""Claude inside the application: the chat endpoint and the bridge to the browser's commands."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import ui_bridge
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.assistant import connection, service
from app.db import get_session
from app.models import AssistantChat, AssistantUsage
from app.routers.projects import get_project

router = APIRouter(prefix="/api", tags=["assistant"])


class ToolResultIn(BaseModel):
    tool_use_id: str
    content: str = Field(max_length=100_000)
    is_error: bool = False
    # how the chat history shows the command
    title: str | None = Field(None, max_length=200)
    detail: str | None = Field(None, max_length=2000)


class ChatIn(BaseModel):
    conversation_id: str | None = None
    text: str | None = Field(None, max_length=20_000)
    context: dict | None = None
    tool_results: list[ToolResultIn] | None = None
    ui_tools: list[dict] = []


class ChatOut(BaseModel):
    conversation_id: str
    title: str
    blocks: list[dict]
    pending: list[dict]
    done: bool
    notice: str | None
    usage: dict


def _problem(e: connection.ConnectionProblem) -> HTTPException:
    return HTTPException(e.status, str(e))


# ---- the connection to Claude (one for the installation)

@router.get("/assistant/status")
def status() -> dict:
    return connection.status()


class KeyIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=400)


class PreferencesIn(BaseModel):
    model: str | None = None
    effort: str | None = None


class LoginFinishIn(BaseModel):
    login_id: str = Field(max_length=64)
    code: str = Field(min_length=1, max_length=4096)


@router.post("/assistant/connect/key")
def connect_key(body: KeyIn) -> dict:
    try:
        name = connection.connect_key(body.api_key)
    except connection.ConnectionProblem as e:
        raise _problem(e) from e
    return {**connection.status(), "message": f"Подключено по ключу. Модель: {name}"}


@router.post("/assistant/connect/account/start")
def account_start() -> dict:
    try:
        return connection.login_start()
    except connection.ConnectionProblem as e:
        raise _problem(e) from e


@router.post("/assistant/connect/account/finish")
def account_finish(body: LoginFinishIn) -> dict:
    try:
        message, ok = connection.login_finish(body.login_id, body.code)
    except connection.ConnectionProblem as e:
        raise _problem(e) from e
    return {**connection.status(), "message": message, "warning": not ok}


@router.post("/assistant/connect/account/cancel", status_code=204)
def account_cancel() -> None:
    connection.login_cancel()


@router.post("/assistant/disconnect")
def disconnect() -> dict:
    connection.disconnect()
    return connection.status()


@router.post("/assistant/check")
def check() -> dict:
    try:
        name = connection.check()
    except connection.ConnectionProblem as e:
        raise _problem(e) from e
    return {"ok": True, "message": f"Связь есть. Модель: {name}"}


@router.put("/assistant/preferences")
def preferences(body: PreferencesIn) -> dict:
    try:
        connection.set_preferences(body.model, body.effort)
    except connection.ConnectionProblem as e:
        raise _problem(e) from e
    return connection.status()


@router.get("/assistant/usage")
def usage(project_id: int | None = None, session: Session = Depends(get_session)) -> dict:
    """Tokens and estimated cost: today, the last 30 days and all time, by model."""
    now = datetime.now(timezone.utc)
    periods = {"today": now.replace(hour=0, minute=0, second=0, microsecond=0), "month": now - timedelta(days=30), "all": None}
    out: dict = {"currency": "USD", "estimate": True}
    for name, since in periods.items():
        q = select(
            AssistantUsage.model,
            func.count(), func.coalesce(func.sum(AssistantUsage.input_tokens), 0), func.coalesce(func.sum(AssistantUsage.output_tokens), 0),
            func.coalesce(func.sum(AssistantUsage.cost_usd), 0.0),
        ).group_by(AssistantUsage.model)
        if since is not None:
            q = q.where(AssistantUsage.created_at >= since)
        if project_id is not None:
            q = q.where(AssistantUsage.project_id == project_id)
        rows = [{"model": m, "requests": n, "input_tokens": int(i), "output_tokens": int(o), "cost_usd": round(float(c), 4)} for m, n, i, o, c in session.execute(q)]
        out[name] = {
            "requests": sum(r["requests"] for r in rows), "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows), "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "by_model": sorted(rows, key=lambda r: -r["cost_usd"]),
        }
    return out


# ---- chats of a project

def _chat_out(c: AssistantChat, full: bool = False) -> dict:
    out = {
        "id": c.id, "title": c.title, "model": c.model, "messages": sum(1 for i in (c.items or []) if i.get("kind") == "user"),
        "input_tokens": c.input_tokens, "output_tokens": c.output_tokens, "cost_usd": round(c.cost_usd or 0.0, 6),
        "created_at": c.created_at.isoformat() if c.created_at else None, "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
    if full:
        out["items"] = c.items or []
        out["waiting"] = bool((c.pending or {}).get("ids"))
    return out


@router.get("/projects/{project_id}/assistant/chats")
def list_chats(project_id: int, session: Session = Depends(get_session)) -> list[dict]:
    get_project(session, project_id)
    chats = session.scalars(select(AssistantChat).where(AssistantChat.project_id == project_id).order_by(AssistantChat.updated_at.desc()).limit(200))
    return [_chat_out(c) for c in chats]


def _get_chat(session: Session, chat_id: str) -> AssistantChat:
    c = session.get(AssistantChat, chat_id)
    if c is None:
        raise HTTPException(404, "Диалог не найден")
    return c


@router.get("/assistant/chats/{chat_id}")
def get_chat(chat_id: str, session: Session = Depends(get_session)) -> dict:
    return _chat_out(_get_chat(session, chat_id), full=True)


class ChatPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)


@router.patch("/assistant/chats/{chat_id}")
def rename_chat(chat_id: str, body: ChatPatch, session: Session = Depends(get_session)) -> dict:
    c = _get_chat(session, chat_id)
    c.title = body.title.strip() or c.title
    session.commit()
    return _chat_out(c)


@router.delete("/assistant/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: str, session: Session = Depends(get_session)) -> None:
    session.delete(_get_chat(session, chat_id))
    session.commit()


@router.post("/projects/{project_id}/assistant/chat", response_model=ChatOut)
def chat(project_id: int, body: ChatIn, session: Session = Depends(get_session)):
    get_project(session, project_id)
    try:
        return service.chat_turn(
            session,
            project_id,
            conversation_id=body.conversation_id,
            text=body.text,
            context=body.context,
            tool_results=[r.model_dump() for r in body.tool_results] if body.tool_results else None,
            ui_tools=body.ui_tools,
        )
    except service.AssistantUnavailable as e:
        raise HTTPException(e.status, str(e)) from e
    except service.ConversationLost as e:
        raise HTTPException(409, "Диалог не найден (удалён или из другого проекта). Начните новый диалог.") from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


# ---- Browser sessions: how MCP clients reach the open application ----

class CommandsIn(BaseModel):
    commands: list[dict] = Field(max_length=200)


class ResultIn(BaseModel):
    call_id: str
    content: str = Field(max_length=100_000)
    ok: bool = True


@router.get("/projects/{project_id}/ui/sessions/{session_id}/events")
async def ui_events(project_id: int, session_id: str, request: Request):
    """Server-sent events with the commands this browser tab has to run."""
    ui = ui_bridge.open_session(session_id, project_id)

    async def stream():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    call = await asyncio.wait_for(ui.queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"event: command\ndata: {json.dumps(call, ensure_ascii=False)}\n\n"
        finally:
            ui_bridge.close_session(session_id)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/projects/{project_id}/ui/sessions/{session_id}/commands", status_code=204)
async def ui_commands(project_id: int, session_id: str, body: CommandsIn) -> None:
    if not ui_bridge.set_commands(session_id, project_id, body.commands):
        raise HTTPException(404, "Сессия интерфейса не найдена")


@router.post("/projects/{project_id}/ui/sessions/{session_id}/results", status_code=204)
async def ui_results(project_id: int, session_id: str, body: ResultIn) -> None:
    if not ui_bridge.resolve(session_id, project_id, body.call_id, body.content, body.ok):
        raise HTTPException(404, "Вызов не найден")
