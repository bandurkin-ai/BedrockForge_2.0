"""The built-in assistant: one conversation turn with Claude.

The server keeps the conversation and runs the data tools. Interface commands are
defined by the browser (it sends their tool definitions with every request) and are
executed there: when Claude asks for one, the turn is returned as `pending` and the
browser answers with tool results.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from types import SimpleNamespace
from typing import Any

import anthropic
from sqlalchemy.orm import Session

from app.assistant import connection
from app.assistant.tools import BY_NAME, DATA_TOOLS, run_data_tool
from app.models import AssistantChat, AssistantUsage

MAX_STEPS = 24
MAX_UI_TOOLS = 64
UI_TOOL_NAME = re.compile(r"^ui_[a-zA-Z0-9_-]{1,60}$")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
Ты — ассистент геолога в BedrockForge, горно-геологической информационной системе (ГГИС) \
для работы с данными бурения: устья скважин, инклинометрия, опробование, аудит качества данных.

Ты работаешь внутри приложения, рядом с рабочей областью пользователя. У тебя два набора инструментов:
- data_* читают данные открытого проекта с сервера. Числа, номера скважин и выводы бери только из них: \
ничего не оценивай на глаз и не придумывай.
- ui_* управляют интерфейсом пользователя: открывают виды, делят рабочую область на окна, выбирают скважину, \
настраивают раскраску и разрез. Когда ответ полезно увидеть, покажи его: выбери скважину, открой подходящий вид. \
Не перестраивай интерфейс без пользы для вопроса.

К каждому сообщению пользователя приложен блок <workspace> — что сейчас открыто у него на экране \
(проект, окна, выбранная скважина, шкала, разрез). «Эта скважина», «здесь», «на разрезе» относятся к нему.

Некоторые команды меняют сохранённые данные (например, запуск аудита заменяет прошлый результат); \
приложение само спросит у пользователя подтверждение. Если он отказал, не повторяй попытку.

Отвечай по-русски, кратко и по делу: сначала вывод, затем цифры. Сравнения и перечни оформляй таблицами \
или списками. Единицы измерения указывай всегда. Если данных для ответа нет (не импортированы пробы, \
не запускался аудит) — скажи об этом и предложи следующий шаг."""


class AssistantUnavailable(Exception):
    """Claude cannot be called: no connection, or the API refused the request."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


class ConversationLost(Exception):
    """The chat id is unknown in this project (deleted, or from another project)."""


# One turn of a chat at a time: requests of one chat wait for each other.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _chat_lock(chat_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(chat_id)
        if lock is None:
            if len(_locks) > 2000:
                _locks.clear()
            lock = _locks[chat_id] = threading.Lock()
        return lock


def is_configured() -> bool:
    return connection.mode() is not None


def get_client() -> anthropic.Anthropic:
    try:
        return connection.client()
    except connection.ConnectionProblem as e:
        raise AssistantUnavailable(str(e), e.status) from e


def _load_chat(session: Session, chat_id: str | None, project_id: int, model: str) -> AssistantChat:
    if chat_id is None:
        chat = AssistantChat(id=uuid.uuid4().hex, project_id=project_id, title="Новый диалог", messages=[], items=[], pending={}, model=model)
        session.add(chat)
        session.flush()
        return chat
    chat = session.get(AssistantChat, chat_id)
    if chat is None or chat.project_id != project_id:
        raise ConversationLost()
    return chat


def clean_ui_tools(raw: list[dict]) -> list[dict]:
    """Keep only well-formed interface tools; their names can never shadow a data tool."""
    tools = []
    for tool in raw[:MAX_UI_TOOLS]:
        name, schema = tool.get("name"), tool.get("input_schema")
        if not isinstance(name, str) or not UI_TOOL_NAME.match(name) or not isinstance(schema, dict):
            continue
        if schema.get("type") != "object":
            continue
        tools.append({"name": name, "description": str(tool.get("description", ""))[:2000], "input_schema": schema})
    return sorted(tools, key=lambda t: t["name"])  # a stable order keeps the prompt cache valid


def _call_claude(messages: list[dict], tools: list[dict], model: str, effort: str) -> Any:
    extra: dict[str, Any] = {}
    info = connection.model_info(model)
    if info is None or info.fallbacks:
        # If a safety classifier declines the request, the API retries it on a fallback model.
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    try:
        return get_client().beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
            **extra,
        )
    except anthropic.AuthenticationError as e:
        raise AssistantUnavailable("Anthropic отклонил ключ или вход: подключите Claude заново (окно «Claude: подключение»)") from e
    except anthropic.PermissionDeniedError as e:
        raise AssistantUnavailable(f"У этого подключения нет доступа к модели {model}: выберите другую модель") from e
    except anthropic.NotFoundError as e:
        raise AssistantUnavailable(f"Модель {model} не найдена: выберите другую модель") from e
    except anthropic.RateLimitError as e:
        raise AssistantUnavailable("Превышен лимит запросов Claude API, повторите позже", 429) from e
    except anthropic.APIStatusError as e:
        raise AssistantUnavailable(f"Ошибка Claude API ({e.status_code}): {e.message}", 502) from e
    except anthropic.APIConnectionError as e:
        raise AssistantUnavailable("Нет связи с Claude API", 502) from e


def _tool_result(tool_use_id: str, content: str, ok: bool) -> dict:
    result = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if not ok:
        result["is_error"] = True
    return result


def _dump(value: Any) -> Any:
    """A response block as a plain request param (the history is stored as JSON)."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, SimpleNamespace):
        return {k: _dump(v) for k, v in vars(value).items() if v is not None}
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_dump(v) for v in value]
    return value


def _usage(response: Any) -> dict[str, int]:
    u = getattr(response, "usage", None)
    return {
        k: int(getattr(u, k, 0) or 0)
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    }


def chat_turn(
    session: Session,
    project_id: int,
    *,
    conversation_id: str | None,
    text: str | None,
    context: dict | None,
    tool_results: list[dict] | None,
    ui_tools: list[dict],
) -> dict:
    model, effort = connection.model(), connection.effort()
    if not is_configured():
        raise AssistantUnavailable("Claude не подключён: войдите в аккаунт Anthropic или введите API-ключ (окно «Claude: подключение»)")
    chat = _load_chat(session, conversation_id, project_id, model)
    with _chat_lock(chat.id):
        if conversation_id:
            session.refresh(chat)  # another request of this chat may have just finished
        try:
            out = _run(session, project_id, chat, text, context, tool_results, ui_tools, model, effort)
        except Exception:
            # A failed request leaves nothing behind: the user can simply retry.
            session.rollback()
            raise
        session.commit()
        return out


def _run(
    session: Session,
    project_id: int,
    chat: AssistantChat,
    text: str | None,
    context: dict | None,
    tool_results: list[dict] | None,
    ui_tools: list[dict],
    model: str,
    effort: str,
) -> dict:
    messages: list[dict] = list(chat.messages or [])
    items: list[dict] = list(chat.items or [])
    pending_state: dict = dict(chat.pending or {})
    content: list[dict] = []

    if pending_state.get("ids"):
        # Every tool call of the waiting turn needs a result: the browser's answers,
        # or "cancelled" when the user moved on with a new message instead.
        answered = {r["tool_use_id"]: r for r in tool_results or []}
        content += pending_state.get("results", [])
        for tool_use_id in pending_state["ids"]:
            r = answered.get(tool_use_id)
            if r is None:
                content.append(_tool_result(tool_use_id, "Отменено пользователем", False))
            else:
                ok = not r.get("is_error", False)
                content.append(_tool_result(tool_use_id, str(r.get("content", "")), ok))
                items.append({"kind": "tool", "title": str(r.get("title") or "Команда интерфейса")[:200], "detail": str(r.get("detail") or "")[:500], "ok": ok})
        pending_state = {}

    if text:
        content.append({"type": "text", "text": text})
        if context:
            workspace = json.dumps(context, ensure_ascii=False, sort_keys=True)
            content.append({"type": "text", "text": f"<workspace>{workspace}</workspace>"})
        items.append({"kind": "user", "text": text})
        if chat.title == "Новый диалог":
            first = " ".join(text.split())
            chat.title = first if len(first) <= 60 else first[:59] + "…"
    if not content:
        raise ValueError("Пустой запрос")
    messages.append({"role": "user", "content": content})

    ui = clean_ui_tools(ui_tools)
    ui_names = {t["name"] for t in ui}
    tools = [t.definition() for t in DATA_TOOLS] + ui
    blocks: list[dict] = []
    turn_usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    def finish(pending: list[dict], notice: str | None = None) -> dict:
        if notice:
            items.append({"kind": "notice", "text": notice})
        chat.messages, chat.items, chat.pending, chat.model = messages, items, pending_state, model
        chat.input_tokens = (chat.input_tokens or 0) + turn_usage["input_tokens"]
        chat.output_tokens = (chat.output_tokens or 0) + turn_usage["output_tokens"]
        chat.cost_usd = (chat.cost_usd or 0.0) + turn_usage["cost_usd"]
        return {
            "conversation_id": chat.id, "title": chat.title, "blocks": blocks, "pending": pending,
            "done": not pending, "notice": notice,
            "usage": {"input_tokens": chat.input_tokens, "output_tokens": chat.output_tokens, "cost_usd": round(chat.cost_usd, 6)},
        }

    for _ in range(MAX_STEPS):
        response = _call_claude(messages, tools, model, effort)
        used = _usage(response)
        served = str(getattr(response, "model", None) or model)
        cost = connection.cost_usd(served, used)
        session.add(AssistantUsage(
            project_id=project_id, chat_id=chat.id, model=served,
            input_tokens=used["input_tokens"] + used["cache_read_input_tokens"] + used["cache_creation_input_tokens"],
            output_tokens=used["output_tokens"], cache_read_tokens=used["cache_read_input_tokens"],
            cache_write_tokens=used["cache_creation_input_tokens"], cost_usd=cost,
        ))
        turn_usage["input_tokens"] += used["input_tokens"] + used["cache_read_input_tokens"] + used["cache_creation_input_tokens"]
        turn_usage["output_tokens"] += used["output_tokens"]
        turn_usage["cost_usd"] += cost or 0.0
        # The content goes back unchanged (thinking blocks included), as the API requires.
        messages.append({"role": "assistant", "content": [_dump(b) for b in response.content]})
        for block in response.content:
            if block.type == "text" and block.text.strip():
                blocks.append({"type": "text", "text": block.text})
                items.append({"kind": "assistant", "text": block.text})

        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason == "refusal":
            return finish([], "Claude отклонил этот запрос по соображениям безопасности.")
        if response.stop_reason == "max_tokens":
            return finish([], "Ответ обрезан по длине. Попросите продолжить или сузьте вопрос.")
        if response.stop_reason != "tool_use":
            return finish([])

        results: list[dict] = []
        pending: list[dict] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            args = block.input if isinstance(block.input, dict) else {}
            if block.name in BY_NAME:
                text_result, ok = run_data_tool(session, project_id, block.name, args)
                results.append(_tool_result(block.id, text_result, ok))
                blocks.append({"type": "tool", "name": block.name, "title": BY_NAME[block.name].title, "input": args, "ok": ok})
                items.append({"kind": "tool", "title": BY_NAME[block.name].title, "detail": _describe(args), "ok": ok})
            elif block.name in ui_names:
                pending.append({"id": block.id, "name": block.name, "input": args})
            else:
                results.append(_tool_result(block.id, f"Инструмента {block.name} нет", False))

        if pending:
            pending_state = {"results": results, "ids": [p["id"] for p in pending]}
            return finish(pending)
        messages.append({"role": "user", "content": results})

    return finish([], "Слишком длинная цепочка действий: остановлено. Уточните задачу.")


def _describe(args: dict) -> str:
    return ", ".join(f"{k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}" for k, v in args.items())[:500]
