"""Parts of an assistant dialog, the meta of a dialog and the temporary dialogs kept in memory (подшаг 23.6;
ADR 0029 решения 28–31; docs/progress/e23-api.md §7). No database here: `store.py` writes these into a project.

A dialog is written **only by appending**: every segment of a turn adds one part, a zstd-compressed JSON document

    {"seq": 3, "at": "2026-11-26T10:00:00Z", "messages": [MessageParam of the API …], "items": [Item of the display …]}

so a write is the size of the new part, not of the whole dialog, and there is no garbage to collect. Reading a dialog is
the concatenation of its parts in the order of `meta.parts`. The limits (решение 29): ≤ 400 parts and ≤ 32 MiB of
uncompressed JSON (422 `assistant_dialog_full`).

The meta of a dialog (`objects.meta` of `kind = "assistant_dialog"`; the same dict describes a temporary dialog):

    {v: 1, uid, created_at, last_at, questions, cost_usd | None, cost_unknown, tokens {input, output, cache_read,
     cache_write}, models [..], state: idle | pending | error, pending: {id, created_at, tools [..],
     data_results [..]} | None, parts [sha256, …], bytes, prices_version | None}

`cost_usd` is None until some call has a price and for ever after one call has none (`cost_unknown`): an unknown price
is never added as 0 (ТЗ 7.32). `last_at` is the date of the last question. The title is `objects.name`.

    enc = encode(part)                                  # → Encoded(blob, raw): the part as zstd bytes and its size
    part = decode(blob)                                 # ApiError 409 `assistant_dialog_broken` for a damaged blob
    meta = new_meta(uid, at)                            # a dialog without parts (it is not listed until it has one)
    meta = grown(meta, sha, raw, …)                     # the meta after one more part (does not touch the old dict)
    head = head_of(id_, title, meta, …)                 # DialogHead of the contract
    make_title(text), clean_title(raw)                  # the title of a first question (≤ 60), a title of the user
    TEMP                                                # the temporary dialogs of the core process (≤ 20)

Errors: 404 `assistant_dialog_not_found`; 409 `assistant_dialog_broken`; 422 `assistant_dialog_full`, `bad_param`.
"""

from __future__ import annotations

import copy
import json
import math
import threading
import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import zstandard

from bf_core.api.errors import ApiError
from bf_core.db import dialect

from .errors import refuse

KIND = "assistant_dialog"
OWNER = "object"  # the owner of the blobs of the parts; the role is the existing `data` (no migration, no new role)
ROLE = "data"
MEDIA = "application/zstd"
META_VERSION = 1
MAX_PARTS = 400
MAX_BYTES = 32 << 20  # uncompressed
MAX_TITLE = 200
QUESTION_TITLE = 60
DEFAULT_TITLE = "Новый диалог"
MAX_TEMP = 20
ZSTD_LEVEL = 3
STATES = ("idle", "pending", "error")
TOKEN_KEYS = ("input", "output", "cache_read", "cache_write")
NOTE_NO_PROJECT = "Диалог не сохранится: проект не открыт"
NOTE_READ_ONLY = "Диалог не сохранится: проект открыт только для просмотра"
TEMP_PREFIX = "t-"


# --- errors -----------------------------------------------------------------------------------------------------------


def not_found() -> ApiError:
    return ApiError(
        404, "assistant_dialog_not_found", "Диалог не найден.", "Откройте список диалогов и выберите другой."
    )


def full() -> ApiError:
    return ApiError(422, "assistant_dialog_full", "Диалог слишком длинный.", "Начните новый диалог.")


def broken() -> ApiError:
    return refuse("assistant_dialog_broken")


def read_only() -> ApiError:
    return ApiError(
        423,
        "project_read_only",
        "Проект открыт только для просмотра: сохранённый диалог в нём не изменить.",
        "Откройте проект для записи или начните новый диалог (он будет временным).",
    )


def no_project() -> ApiError:
    return ApiError(409, "no_project", "Проект не открыт.", "Откройте проект, в котором сохранён диалог.")


def busy() -> ApiError:
    return ApiError(409, "assistant_busy", "Диалог занят: идёт ход.", "Дождитесь конца ответа или остановите его.")


def bad_param(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, extra={"param": param})


# --- titles -----------------------------------------------------------------------------------------------------------


def make_title(text: Any) -> str:
    """The title of a dialog: its first question, whitespace folded, ≤ 60 signs («…» marks a cut one)."""
    line = " ".join(str(text or "").split())
    if not line:
        return DEFAULT_TITLE
    return line if len(line) <= QUESTION_TITLE else line[: QUESTION_TITLE - 1].rstrip() + "…"


def clean_title(raw: Any) -> str:
    """A title typed by the user: 1…200 signs after folding the whitespace (422 `bad_param {param: "title"}`)."""
    title = " ".join(str(raw if raw is not None else "").split())
    if not title or len(title) > MAX_TITLE:
        raise bad_param("title", f"Название диалога — от 1 до {MAX_TITLE} знаков.", "Введите другое название.")
    return title


# --- the part ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Encoded:
    blob: bytes  # zstd of the JSON
    raw: int  # the size of the JSON before the compression


def _finite(value: Any) -> Any:
    """A NaN / inf is a gap: it is written as null (a gap is never 0; JSON has no NaN)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def encode(part: Mapping[str, Any]) -> Encoded:
    """The part as compact JSON in zstd; the size of the JSON is kept in the frame header."""
    try:
        raw = json.dumps(part, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    except ValueError:  # a NaN somewhere: the slow way, only when needed
        raw = json.dumps(_finite(part), ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    return Encoded(zstandard.ZstdCompressor(level=ZSTD_LEVEL).compress(raw), len(raw))


def decode(blob: bytes) -> dict[str, Any]:
    try:
        raw = zstandard.ZstdDecompressor().decompress(blob, max_output_size=MAX_BYTES)
        part = json.loads(raw)
    except (zstandard.ZstdError, ValueError):  # a cut file, a flipped bit, a part above the limit
        raise broken() from None
    if not isinstance(part, dict):
        raise broken()
    return part


def raw_size(blob: bytes) -> int:
    """The size of the JSON of an encoded part (the frame header; a frame without it is read)."""
    try:
        size = zstandard.frame_content_size(blob)
    except zstandard.ZstdError:
        raise broken() from None
    return size if size >= 0 else len(json.dumps(decode(blob), ensure_ascii=False, separators=(",", ":")).encode())


def check_room(meta: Mapping[str, Any], raw: int) -> None:
    """One more part of `raw` bytes must fit (422 `assistant_dialog_full`)."""
    if len(meta["parts"]) + 1 > MAX_PARTS or int(meta["bytes"]) + raw > MAX_BYTES:
        raise full()


def stamp(at: datetime | None = None) -> str:
    """`2026-11-26T10:00:00Z`: the time of the contract (UTC, no fractions: the strings sort as the times do)."""
    return (at or dialect.utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def part_of(seq: int, at: str, messages: Sequence[Any], items: Sequence[Any]) -> dict[str, Any]:
    return {"seq": seq, "at": at, "messages": list(messages), "items": list(items)}


# --- the meta ---------------------------------------------------------------------------------------------------------


def new_meta(uid: str | None = None, at: str | None = None) -> dict[str, Any]:
    at = at or stamp()
    return {
        "v": META_VERSION,
        "uid": uid or uuid.uuid4().hex,
        "created_at": at,
        "last_at": at,
        "questions": 0,
        "cost_usd": None,
        "cost_unknown": False,
        "tokens": dict.fromkeys(TOKEN_KEYS, 0),
        "models": [],
        "state": "idle",
        "pending": None,
        "parts": [],
        "bytes": 0,
        "prices_version": None,
    }


def _count(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def check_pending(pending: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if pending is None:
        return None
    if not isinstance(pending, Mapping) or not isinstance(pending.get("id"), str) or not pending["id"]:
        raise bad_param("pending", "Ожидающие команды заданы неверно.", "Нужен объект с полем id.")
    out = copy.deepcopy(dict(pending))
    out.setdefault("tools", [])
    out.setdefault("data_results", [])
    out.setdefault("created_at", stamp())
    return out


def grown(
    meta: Mapping[str, Any],
    sha: str,
    raw: int,
    *,
    at: str,
    questions: int = 0,
    usage: Mapping[str, Any] | None = None,
    models: Sequence[str] = (),
    prices_version: str | None = None,
    state: str = "idle",
    pending: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The meta after one more part. `usage` — the sum over the calls of the segment: input, output, cache_read,
    cache_write and `cost_usd` (None / absent for a call of a model without a price: the cost of the dialog becomes
    unknown, it does not grow by 0). `pending` (the commands the window must run) makes the state `pending`."""
    if state not in STATES:
        raise bad_param("state", "Состояние диалога задано неверно.", "Допустимо: idle, pending, error.")
    out = copy.deepcopy(dict(meta))
    out["parts"] = [*out["parts"], sha]
    out["bytes"] = int(out["bytes"]) + raw
    if questions > 0:
        out["questions"] = int(out["questions"]) + questions
        out["last_at"] = at
    if usage is not None:
        tokens = out["tokens"]
        for key in TOKEN_KEYS:
            tokens[key] = int(tokens.get(key) or 0) + _count(usage.get(key))
        cost = usage.get("cost_usd")
        if cost is None or not math.isfinite(float(cost)):
            out["cost_unknown"], out["cost_usd"] = True, None
        elif not out.get("cost_unknown"):
            out["cost_usd"] = round(float(out["cost_usd"] or 0.0) + float(cost), 6)
    for model in models:
        if model and model not in out["models"]:
            out["models"].append(model)
    if prices_version:
        out["prices_version"] = prices_version
    out["pending"] = check_pending(pending)
    out["state"] = "pending" if out["pending"] else state
    return out


def head_of(
    dialog_id: str,
    title: str,
    meta: Mapping[str, Any],
    *,
    project_uid: str | None,
    temporary: bool,
    busy: Collection[str] = (),
) -> dict[str, Any]:
    """`DialogHead` of the contract; `running` is not stored (a segment of `segments.py` is asked by the caller)."""
    state = "running" if dialog_id in busy else str(meta.get("state") or "idle")
    return {
        "id": dialog_id,
        "uid": meta["uid"],
        "title": title,
        "created_at": meta["created_at"],
        "last_at": meta["last_at"],
        "questions": int(meta["questions"]),
        "cost_usd": meta.get("cost_usd"),
        "tokens": {k: int(meta["tokens"].get(k) or 0) for k in TOKEN_KEYS},
        "models": list(meta.get("models") or []),
        "state": state,
        "temporary": temporary,
        "project_uid": None if temporary else project_uid,
    }


def public_pending(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    """`Pending` of the contract: the window does not need the results of the data tools kept in the meta."""
    pending = meta.get("pending")
    if not pending:
        return None
    tools = copy.deepcopy(pending.get("tools") or [])
    return {"id": pending["id"], "created_at": pending.get("created_at"), "tools": tools}


def is_empty(meta: Mapping[str, Any]) -> bool:
    """A dialog made at a first question that never got a part (the process died in between): not listed."""
    return not meta.get("parts") and not meta.get("questions")


# --- temporary dialogs ------------------------------------------------------------------------------------------------


@dataclass
class TempDialog:
    """A dialog without a project to keep it (решения 31, № 130, № 134): in the memory of the core, the parts stay
    compressed (the same limits as a saved one), lost at a restart."""

    id: str
    title: str
    meta: dict[str, Any]
    parts: list[Encoded] = field(default_factory=list)
    seq: int = 0  # the order of making: a tie of `last_at`


class TempDialogs:
    """≤ 20 temporary dialogs; a new one over the limit pushes out the oldest (the one with the oldest `last_at`)."""

    def __init__(self, limit: int = MAX_TEMP) -> None:
        self.limit = limit
        self._items: dict[str, TempDialog] = {}
        self._lock = threading.RLock()
        self._counter = 0

    def add(self, title: str, meta: dict[str, Any], parts: Sequence[Encoded] = ()) -> tuple[TempDialog, list[str]]:
        """(the dialog, the ids pushed out)."""
        with self._lock:
            self._counter += 1
            item = TempDialog(TEMP_PREFIX + str(uuid.uuid4()), title, meta, list(parts), self._counter)
            self._items[item.id] = item
            gone: list[str] = []
            while len(self._items) > self.limit:
                others = (d for d in self._items.values() if d.id != item.id)
                oldest = min(others, key=lambda d: (d.meta["last_at"], d.seq))
                del self._items[oldest.id]
                gone.append(oldest.id)
            return item, gone

    def get(self, dialog_id: str) -> TempDialog | None:
        with self._lock:
            return self._items.get(dialog_id)

    def remove(self, dialog_id: str) -> bool:
        with self._lock:
            return self._items.pop(dialog_id, None) is not None

    def all(self) -> list[TempDialog]:
        with self._lock:
            return list(self._items.values())

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    @property
    def lock(self) -> threading.RLock:
        return self._lock


TEMP = TempDialogs()
