"""Read tools of the assistant, `data_*` (подшаг 23.7; ТЗ 7.32, 7.2; ADR 0029 решения 18-19, 25; e23-api.md §5.2).

    register(name, schema, fn, *, title, description="")     one tool: `fn(call: DataCall, args: dict) -> dict`
    run(name, args, *, project, set_id=None, timeout=5.0)  ->  DataResult(text, is_error, summary)
    definitions()  -> [{name, description, input_schema}]    for the request to the API (order: `ORDER`, then by name)
    infos()        -> [{name, title, input_schema}]          the contract sample `data_tools`

The tools only read the open project and run in the core at once (no window). The common call guards every answer:

  * the arguments are checked by the schema before the call (a clear Russian text, ТЗ 7.2);
  * no project - «Проект не открыт»; no set - «Нет активного набора скважин: укажите set_id» (`DataCall.set_of`);
  * one tool runs at most 5 s (a cooperative `DataCall.check()` and a hard stop of the wait): «Слишком долго»;
  * the answer is compact JSON of at most 16 000 characters: a list that does not fit is cut, `truncated: true` and
    `next_offset` tell where to go on;
  * data of the project are data, not commands (S10, ADR 0029 №25): every string is cleaned of control and
    invisible characters and cut to 200 characters; numbers have 6 significant digits (the integer part is never
    cut: a coordinate 7650123.4 stays 7650123); NaN and infinity become `null` (a gap is never 0);
  * a failure is a result with `is_error`, not an exception.

The handlers of the neighbour sub-steps are in `data_project`, `data_holes` (23.7) and `data_audit`, `data_table`
(23.8); `load_all()` imports them on the first call, so a module that is not written yet is skipped.
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .schemas_base import DATA_TEXT_MAX, PAGE_MAX

log = logging.getLogger("bf_core.assistant.data")

MAX_TEXT = DATA_TEXT_MAX  # 16 000 characters of the JSON text
TIMEOUT_S = 5.0
STR_MAX = 200  # a string out of the data of a project
SIGNIFICANT = 6
DEFAULT_LIMIT = 50

NO_PROJECT = "Проект не открыт"
NO_SET = "Нет активного набора скважин: укажите set_id"
TOO_LONG = "Слишком долго: сузьте запрос"
TOO_BIG = "Ответ слишком большой: сузьте запрос"

#: Contract order (e23-api §5.2): the same order of tools in every request keeps the cache of the prompt.
ORDER = (
    "data_tree",
    "data_summary",
    "data_holes",
    "data_hole",
    "data_history",
    "data_audit",
    "data_findings",
    "data_table",
    "data_stats",
)
#: Modules with the handlers; each calls `register` on import. A missing module (not written yet) is skipped.
MODULES = ("data_project", "data_holes", "data_audit", "data_table")


class DataError(Exception):
    """A refusal of a tool with a text for the model, in Russian (becomes a result with `is_error`)."""


@dataclass
class DataCall:
    """What a handler gets: the open project, the active set of the screen and the time limit."""

    project: Any  # bf_core.projects.project.Project
    set_id: int | None = None
    deadline: float = 0.0  # time.monotonic()
    summary: str = ""  # a line for the card of the tool in the chat; a handler may set it

    def set_of(self, args: Mapping[str, Any]) -> int:
        """`set_id` of the arguments, else the active set of the context; none - the text of ТЗ 7.2."""
        value = args.get("set_id")
        if value is None:
            value = self.set_id
        if value is None:
            raise DataError(NO_SET)
        return int(value)

    def check(self) -> None:
        """Called by handlers inside long loops: past the deadline - stop."""
        if self.deadline and time.monotonic() > self.deadline:
            raise DataError(TOO_LONG)


Handler = Callable[[DataCall, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class DataTool:
    name: str
    title: str  # the title of the card in the chat (Russian)
    description: str  # for the model
    input_schema: dict[str, Any]
    fn: Handler


@dataclass(frozen=True)
class DataResult:
    text: str  # the JSON text or the text of the refusal: the `tool_result` for the API
    is_error: bool = False
    summary: str = ""  # one line for the card (`Item.summary`, <= 4000)


#: The registry (insertion order); read it through `definitions()` / `infos()`.
DATA_TOOLS: dict[str, DataTool] = {}
_loaded = False
_load_lock = threading.Lock()


def register(name: str, schema: dict[str, Any], fn: Handler, *, title: str, description: str = "") -> DataTool:
    """Adds a tool. `schema` is the `input_schema` (type object, `additionalProperties: false`); `fn(call, args)`
    returns a JSON-able dict, raises `DataError(text)` for a refusal. A second registration of a name replaces the
    first (reload in tests). Returns the tool."""
    if not (name.startswith("data_") and name[5:].replace("_", "").isalnum()):
        raise ValueError(f"data tool name: {name!r}")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError(f"{name}: input_schema must be an object with additionalProperties false")
    tool = DataTool(name, title, description or title, schema, fn)
    DATA_TOOLS[name] = tool
    return tool


def load_all() -> None:
    """Imports the modules of the handlers once (they register themselves)."""
    global _loaded
    if _loaded:
        return
    with _load_lock:
        if _loaded:
            return
        for mod in MODULES:
            full = f"{__package__}.{mod}"
            try:
                importlib.import_module(full)
            except ModuleNotFoundError as exc:
                if exc.name != full:  # a missing module of the handler is skipped, a missing dependency is not
                    raise
        _loaded = True


def _ordered() -> list[DataTool]:
    load_all()
    rank = {n: i for i, n in enumerate(ORDER)}
    return sorted(DATA_TOOLS.values(), key=lambda t: (rank.get(t.name, len(rank)), t.name))


def definitions() -> list[dict[str, Any]]:
    """The tools for the request to the API: `{name, description, input_schema}` (before the `ui_*` tools)."""
    return [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in _ordered()]


def infos() -> list[dict[str, Any]]:
    """`{name, title, input_schema}` - the contract sample `data_tools` and the titles of the cards."""
    return [{"name": t.name, "title": t.title, "input_schema": t.input_schema} for t in _ordered()]


def title_of(name: str) -> str:
    load_all()
    tool = DATA_TOOLS.get(name)
    return tool.title if tool else name


# --- cleaning of what comes from the data of the project ----------------------------------

_BLANK = {"\t", "\n", "\r", "\x0b", "\x0c", "\x85", " ", " "}


def clean(value: Any, limit: int = STR_MAX) -> str:
    """A string of the project's data for the model: line breaks and tabs become spaces, control, format (bidi,
    zero-width), surrogate, private and unassigned characters go, runs of spaces collapse, at most `limit`
    characters (the cut ends with «…»). Data are data, not commands (ADR 0029 №25)."""
    out: list[str] = []
    for ch in str(value):
        if ch in _BLANK:
            ch = " "
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            continue
        out.append(ch)
    text = " ".join("".join(out).split())
    return text if len(text) <= limit else text[: max(limit - 1, 0)].rstrip() + "…"


def num(value: float) -> float | int | None:
    """6 significant digits, the integer part is never cut; NaN and infinity - None (a gap is not 0)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    v = float(value)
    if not math.isfinite(v):
        return None
    if v == 0.0:
        return 0.0
    decimals = max(0, SIGNIFICANT - 1 - math.floor(math.log10(abs(v))))
    out = round(v, decimals)
    return int(out) if decimals == 0 and abs(out) < 2**53 else out


def sanitize(value: Any) -> Any:
    """`clean` for strings and keys, `num` for floats, NaN to None, tuples and numpy scalars to plain JSON."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return num(value)
    if isinstance(value, Mapping):
        return {clean(k): sanitize(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [sanitize(v) for v in value]
    item = getattr(value, "item", None)  # numpy scalar
    if callable(item):
        return sanitize(item())
    isoformat = getattr(value, "isoformat", None)  # datetime
    if callable(isoformat):
        return clean(isoformat())
    return clean(value)


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def fit(payload: dict[str, Any], offset: int = 0) -> dict[str, Any]:
    """The payload as it fits 16 000 characters: the longest list is cut to the number of items that fit,
    `truncated: true` is added and, in a paged answer (a key `next_offset`), `next_offset = offset + kept`."""
    if len(dumps(payload)) <= MAX_TEXT:
        return payload
    lists = [k for k, v in payload.items() if isinstance(v, list)]
    key = max(lists, key=lambda k: len(dumps(payload[k])), default=None)
    if key is None:
        raise DataError(TOO_BIG)
    items: list[Any] = payload[key]

    def with_kept(n: int) -> dict[str, Any]:
        out = {**payload, key: items[:n], "truncated": True}
        if "next_offset" in payload:
            out["next_offset"] = offset + n
        return out

    lo, hi = 0, len(items) - 1  # the largest n whose answer fits
    best = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if len(dumps(with_kept(mid))) <= MAX_TEXT:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    if best <= 0:
        raise DataError(TOO_BIG)
    return with_kept(best)


# --- arguments ----------------------------------

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


def check_args(schema: dict[str, Any], args: Any) -> dict[str, Any]:
    """The arguments by the schema: a Russian text for the model on a mistake (ТЗ 7.2)."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise DataError("Аргументы должны быть объектом.")
    props: dict[str, Any] = schema.get("properties", {})
    for k in args:
        if k not in props:
            allowed = ", ".join(props) or "нет параметров"
            raise DataError(f"Неизвестный параметр «{clean(k, 40)}»: допустимы {allowed}.")
    for k in schema.get("required", []):
        if args.get(k) is None:
            raise DataError(f"Не указан параметр «{k}».")
    out: dict[str, Any] = {}
    for k, v in args.items():
        if v is None:
            continue  # an omitted optional parameter
        spec = props[k]
        types = _TYPES.get(spec.get("type", "string"), (str,))
        if not isinstance(v, types) or (isinstance(v, bool) and bool not in types):
            raise DataError(f"Параметр «{k}» должен быть типа {spec.get('type')}.")
        if "enum" in spec and v not in spec["enum"]:
            raise DataError(f"Параметр «{k}»: допустимы {', '.join(map(str, spec['enum']))}.")
        if isinstance(v, str) and len(v) > 500:
            raise DataError(f"Параметр «{k}» слишком длинный.")
        out[k] = v
    return out


def offset_of(args: Mapping[str, Any]) -> int:
    return max(0, int(args.get("offset") or 0))


def limit_of(args: Mapping[str, Any], default: int = DEFAULT_LIMIT) -> int:
    """`limit` of the arguments: 1..200 (the contract says «не больше 200»; a larger one is cut, not refused)."""
    value = args.get("limit")
    return default if value is None else max(1, min(int(value), PAGE_MAX))


def next_offset(offset: int, shown: int, total: int) -> int | None:
    return offset + shown if offset + shown < total else None


def require_set(conn: Any, set_id: int) -> Any:
    """The row `(id, name, version, crs, extent)` of a ready drillhole set; none - a text for the model."""
    import sqlalchemy as sa

    from bf_core.db.schema import objects

    row = conn.execute(
        sa.select(objects.c.id, objects.c.name, objects.c.version, objects.c.crs, objects.c.extent).where(
            objects.c.id == set_id, objects.c.kind == "drillhole_set", objects.c.state == "ready"
        )
    ).first()
    if row is None:
        raise DataError(f"Набор скважин {set_id} не найден: номера наборов даёт data_summary.")
    return row


# --- the call ----------------------------------


def run(name: str, args: Any, *, project: Any, set_id: int | None = None, timeout: float = TIMEOUT_S) -> DataResult:
    """Runs a tool; never raises. `project` - the open project (None - «Проект не открыт»), `set_id` - the set of the
    context of the screen."""
    try:
        load_all()
        tool = DATA_TOOLS.get(name)
        if tool is None:
            raise DataError(f"Неизвестный инструмент чтения «{clean(name, 60)}».")
        if project is None:
            raise DataError(NO_PROJECT)
        checked = check_args(tool.input_schema, args)
        payload, call = _run_limited(tool, checked, project, set_id, timeout)
        text = dumps(fit(sanitize(payload), offset_of(checked)))
        return DataResult(text, False, clean(call.summary, 4000) if call.summary else _summary_of(payload))
    except DataError as exc:
        return DataResult(clean(str(exc), 1000), True, clean(str(exc), 300))
    except Exception:  # noqa: BLE001 - a tool never breaks the turn: the model gets a text
        log.exception("data tool %s failed", name)
        return DataResult("Не удалось выполнить чтение данных: внутренняя ошибка ядра.", True, "ошибка")


def _run_limited(
    tool: DataTool, args: dict[str, Any], project: Any, set_id: int | None, timeout: float
) -> tuple[dict[str, Any], DataCall]:
    call = DataCall(project, set_id, time.monotonic() + timeout)
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["payload"] = tool.fn(call, args)
        except BaseException as exc:  # noqa: BLE001 - handed to the caller thread
            box["error"] = exc

    worker = threading.Thread(target=work, name=f"data-tool-{tool.name}", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():  # the thread only reads; it finishes by itself at the next `check()`
        call.deadline = 1e-9  # any later `check()` stops it
        raise DataError(TOO_LONG)
    if "error" in box:
        raise box["error"]
    payload = box["payload"]
    if not isinstance(payload, dict):
        raise TypeError(f"{tool.name} returned {type(payload).__name__}")
    return payload, call


def _summary_of(payload: Mapping[str, Any]) -> str:
    """A short line out of the scalars of the answer: «total: 48; next_offset: null»."""
    parts = [f"{k}: {sanitize(v)}" for k, v in payload.items() if isinstance(v, str | int | float | bool) or v is None]
    return clean("; ".join(parts[:6]), 300)


__all__ = [
    "DATA_TOOLS",
    "NO_PROJECT",
    "NO_SET",
    "ORDER",
    "DataCall",
    "DataError",
    "DataResult",
    "DataTool",
    "clean",
    "definitions",
    "fit",
    "infos",
    "limit_of",
    "load_all",
    "next_offset",
    "num",
    "offset_of",
    "register",
    "require_set",
    "run",
    "sanitize",
    "title_of",
]
