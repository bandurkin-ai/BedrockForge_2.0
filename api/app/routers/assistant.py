"""Claude inside the application: the chat endpoint and the bridge to the browser's commands."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import ui_bridge
from app.assistant import service
from app.config import settings
from app.db import get_session
from app.routers.projects import get_project

router = APIRouter(prefix="/api", tags=["assistant"])


class StatusOut(BaseModel):
    configured: bool
    model: str


class ToolResultIn(BaseModel):
    tool_use_id: str
    content: str = Field(max_length=100_000)
    is_error: bool = False


class ChatIn(BaseModel):
    conversation_id: str | None = None
    text: str | None = Field(None, max_length=20_000)
    context: dict | None = None
    tool_results: list[ToolResultIn] | None = None
    ui_tools: list[dict] = []


class ChatOut(BaseModel):
    conversation_id: str
    blocks: list[dict]
    pending: list[dict]
    done: bool
    notice: str | None


@router.get("/assistant/status", response_model=StatusOut)
def status() -> StatusOut:
    return StatusOut(configured=service.is_configured(), model=settings.assistant_model)


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
        raise HTTPException(409, "Диалог не найден на сервере (он был перезапущен). Начните новый диалог.") from e
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
