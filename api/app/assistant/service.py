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
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import anthropic
from sqlalchemy.orm import Session

from app.assistant.tools import BY_NAME, DATA_TOOLS, run_data_tool
from app.config import settings

MAX_CONVERSATIONS = 200
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
    """Claude cannot be called: no credentials, or the API refused the request."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


class ConversationLost(Exception):
    """The conversation id is unknown (the server was restarted or the conversation expired)."""


@dataclass
class Conversation:
    project_id: int
    messages: list[dict] = field(default_factory=list)
    # tool_use id -> result for the data tools of a turn that is waiting for the browser.
    partial_results: list[dict] = field(default_factory=list)
    pending_ids: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


_conversations: OrderedDict[str, Conversation] = OrderedDict()
_registry_lock = threading.Lock()
_client: anthropic.Anthropic | None = None


def is_configured() -> bool:
    return settings.assistant_configured


def get_client() -> anthropic.Anthropic:
    global _client
    if not is_configured():
        raise AssistantUnavailable("Claude не подключён: на сервере не задан ANTHROPIC_API_KEY")
    if _client is None:
        _client = anthropic.Anthropic()  # credentials come from the environment
    return _client


def _conversation(conversation_id: str | None, project_id: int) -> tuple[str, Conversation]:
    with _registry_lock:
        if conversation_id is None:
            conversation_id = uuid.uuid4().hex
            _conversations[conversation_id] = Conversation(project_id)
            while len(_conversations) > MAX_CONVERSATIONS:
                _conversations.popitem(last=False)
        conversation = _conversations.get(conversation_id)
        if conversation is None or conversation.project_id != project_id:
            raise ConversationLost()
        _conversations.move_to_end(conversation_id)
        return conversation_id, conversation


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


def _call_claude(messages: list[dict], tools: list[dict]) -> Any:
    try:
        return get_client().beta.messages.create(
            model=settings.assistant_model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.assistant_effort},
            cache_control={"type": "ephemeral"},
            # If a safety classifier declines the request, the API retries it on a fallback model.
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise AssistantUnavailable("Claude API отклонил ключ: проверьте ANTHROPIC_API_KEY") from e
    except anthropic.PermissionDeniedError as e:
        raise AssistantUnavailable(f"У ключа Claude API нет доступа к модели {settings.assistant_model}") from e
    except anthropic.NotFoundError as e:
        raise AssistantUnavailable(f"Модель {settings.assistant_model} не найдена (ASSISTANT_MODEL)") from e
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
    conversation_id, conversation = _conversation(conversation_id, project_id)
    with conversation.lock:
        snapshot = (list(conversation.messages), list(conversation.partial_results), list(conversation.pending_ids))
        try:
            return _run(session, project_id, conversation_id, conversation, text, context, tool_results, ui_tools)
        except Exception:
            # A failed request must not leave half a turn behind: the user can simply retry.
            conversation.messages, conversation.partial_results, conversation.pending_ids = snapshot
            raise


def _run(
    session: Session,
    project_id: int,
    conversation_id: str,
    conversation: Conversation,
    text: str | None,
    context: dict | None,
    tool_results: list[dict] | None,
    ui_tools: list[dict],
) -> dict:
    messages = conversation.messages
    content: list[dict] = []

    if conversation.pending_ids:
        # Every tool call of the waiting turn needs a result: the browser's answers,
        # or "cancelled" when the user moved on with a new message instead.
        answered = {r["tool_use_id"]: r for r in tool_results or []}
        content += conversation.partial_results
        for tool_use_id in conversation.pending_ids:
            r = answered.get(tool_use_id)
            if r is None:
                content.append(_tool_result(tool_use_id, "Отменено пользователем", False))
            else:
                content.append(_tool_result(tool_use_id, str(r.get("content", "")), not r.get("is_error", False)))
        conversation.partial_results, conversation.pending_ids = [], []

    if text:
        content.append({"type": "text", "text": text})
        if context:
            workspace = json.dumps(context, ensure_ascii=False, sort_keys=True)
            content.append({"type": "text", "text": f"<workspace>{workspace}</workspace>"})
    if not content:
        raise ValueError("Пустой запрос")
    messages.append({"role": "user", "content": content})

    ui = clean_ui_tools(ui_tools)
    ui_names = {t["name"] for t in ui}
    tools = [t.definition() for t in DATA_TOOLS] + ui
    blocks: list[dict] = []

    def out(pending: list[dict], notice: str | None = None) -> dict:
        return {
            "conversation_id": conversation_id, "blocks": blocks, "pending": pending,
            "done": not pending, "notice": notice,
        }

    for _ in range(MAX_STEPS):
        response = _call_claude(messages, tools)
        # The content goes back unchanged (thinking blocks included), as the API requires.
        messages.append({"role": "assistant", "content": response.content})
        for block in response.content:
            if block.type == "text" and block.text.strip():
                blocks.append({"type": "text", "text": block.text})

        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason == "refusal":
            return out([], "Claude отклонил этот запрос по соображениям безопасности.")
        if response.stop_reason == "max_tokens":
            return out([], "Ответ обрезан по длине. Попросите продолжить или сузьте вопрос.")
        if response.stop_reason != "tool_use":
            return out([])

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
            elif block.name in ui_names:
                pending.append({"id": block.id, "name": block.name, "input": args})
            else:
                results.append(_tool_result(block.id, f"Инструмента {block.name} нет", False))

        if pending:
            conversation.partial_results = results
            conversation.pending_ids = [p["id"] for p in pending]
            return out(pending)
        messages.append({"role": "user", "content": results})

    return out([], "Слишком длинная цепочка действий: остановлено. Уточните задачу.")
