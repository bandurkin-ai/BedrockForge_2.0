"""Open browser tabs that can run interface commands for an external (MCP) client.

A tab registers its command list and listens on a server-sent event stream; a command is
queued for the tab and its result is awaited. Everything runs on the server's event loop.
State lives in memory: one API process, as in the rest of this single-user setup.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

MAX_SESSIONS = 50
RESULT_TIMEOUT = 120  # a command may wait for the user's confirmation


class NoBrowser(Exception):
    """No open BedrockForge tab is connected for the project."""


@dataclass
class UiSession:
    project_id: int
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    commands: list[dict] = field(default_factory=list)
    calls: dict[str, asyncio.Future] = field(default_factory=dict)
    opened: float = field(default_factory=time.monotonic)


_sessions: dict[str, UiSession] = {}


def open_session(session_id: str, project_id: int) -> UiSession:
    while len(_sessions) >= MAX_SESSIONS:
        close_session(min(_sessions, key=lambda k: _sessions[k].opened))
    session = _sessions[session_id] = UiSession(project_id)
    return session


def close_session(session_id: str) -> None:
    session = _sessions.pop(session_id, None)
    if session:
        for call in session.calls.values():
            if not call.done():
                call.set_exception(NoBrowser())


def _get(session_id: str, project_id: int) -> UiSession | None:
    session = _sessions.get(session_id)
    return session if session and session.project_id == project_id else None


def set_commands(session_id: str, project_id: int, commands: list[dict]) -> bool:
    session = _get(session_id, project_id)
    if session:
        session.commands = commands
    return session is not None


def resolve(session_id: str, project_id: int, call_id: str, content: str, ok: bool) -> bool:
    session = _get(session_id, project_id)
    call = session.calls.get(call_id) if session else None
    if call is None or call.done():
        return False
    call.set_result((content, ok))
    return True


def _latest(project_id: int) -> UiSession:
    ready = [s for s in _sessions.values() if s.project_id == project_id and s.commands]
    if not ready:
        raise NoBrowser()
    return max(ready, key=lambda s: s.opened)


def list_commands(project_id: int) -> list[dict]:
    return _latest(project_id).commands


async def run_command(project_id: int, command: str, args: dict) -> tuple[str, bool]:
    """Run a command in the most recently opened tab of the project and wait for its result."""
    session = _latest(project_id)
    if not any(c.get("id") == command for c in session.commands):
        return f"Команды «{command}» нет. Список команд: ui_list_commands.", False
    call_id = uuid.uuid4().hex
    call = session.calls[call_id] = asyncio.get_running_loop().create_future()
    await session.queue.put({"call_id": call_id, "command": command, "args": args})
    try:
        return await asyncio.wait_for(call, RESULT_TIMEOUT)
    except TimeoutError:
        return "Браузер не ответил вовремя (возможно, пользователь не подтвердил действие)", False
    finally:
        session.calls.pop(call_id, None)
