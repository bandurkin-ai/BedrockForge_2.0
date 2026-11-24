"""The request of the display of a block model, the overtaking of requests, the HEAVY slot (подшаг 17.27; e17-api §1,
§12). Used by `viz_data.py` (the shell), `viz_slice.py` (the slice, the point, the polygons) and `viz_routes.py`.

    spec = parse_view(body)          # POST /viz/blocks/{id}/shell → `ViewSpec` (422 `bad_param {param}` in Russian)
    common = parse_common(body)      # the keys of every request: filter_id, restrict, channel, seq
    out = overtaken(common, lambda tok: work(tok))       # the request as the one of its channel

Overtaking. A request with `channel` and `seq` cancels the running request of that channel with a smaller `seq`; the
cancelled one stops between the pieces (`Token.check`) and answers 409 `superseded`; a request that comes with a
smaller `seq` than the running one is refused at once. No `channel` — no overtaking. The calculations take one of the
HEAVY slots of the charts (`stats.routes_charts.SLOTS`), waiting for it in short steps so that a cancelled request
does not wait for nothing.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from bf_core.api.errors import ApiError

from .lod import DEFAULT_LIMIT

MIN_LIMIT, MAX_LIMIT = 500_000, 10_000_000
HEAVY_WAIT = 0.05  # s: a request waiting for a HEAVY slot looks at its token this often
KINDS = ("none", "scale", "classes", "categories")
MODES = ("shell", "all")


# --- errors -----------------------------------------------------------------------------------------------------------


def bad(param: str, message: str, hint: str = "Проверьте настройки показа блочной модели.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def superseded() -> ApiError:
    return ApiError(409, "superseded", "Запрос показа устарел: его сменил более новый.",
                    "Ничего делать не нужно: окно показывает последний результат.")  # fmt: skip


def number(raw: Any, param: str, what: str) -> float:
    """A finite number or a text «0,5» / «0.5»."""
    if isinstance(raw, bool):
        raise bad(param, f"{what}: нужно число.")
    if isinstance(raw, str):
        try:
            raw = float(raw.strip().replace(",", "."))
        except ValueError:
            raise bad(param, f"{what}: нужно число.", "Например 0,5.") from None
    if not isinstance(raw, int | float) or not math.isfinite(raw):
        raise bad(param, f"{what}: нужно конечное число.")
    return float(raw)


# --- overtaking, the HEAVY slot ---------------------------------------------------------------------------------------


class Token:
    """One request on its channel: `cancelled` is set by a newer request of the channel."""

    __slots__ = ("cancelled", "channel", "seq")

    def __init__(self, channel: str | None, seq: int) -> None:
        self.channel, self.seq, self.cancelled = channel, seq, False

    def check(self) -> None:
        """Called between the pieces of the work: raises 409 `superseded` for an overtaken request."""
        if self.cancelled:
            raise superseded()

    def tick(self, _fraction: float = 0.0) -> None:
        self.check()


class Overtake:
    """The running request of every channel."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: dict[str, Token] = {}

    def enter(self, channel: str | None, seq: int) -> Token:
        tok = Token(channel, seq)
        if channel is None:
            return tok
        with self._lock:
            cur = self._live.get(channel)
            if cur is not None:
                if cur.seq > seq:
                    raise superseded()  # a newer one is running: this one is already late
                cur.cancelled = True
            self._live[channel] = tok
        return tok

    def leave(self, tok: Token) -> None:
        if tok.channel is None:
            return
        with self._lock:
            if self._live.get(tok.channel) is tok:
                del self._live[tok.channel]


GATE = Overtake()


@contextmanager
def heavy(tok: Token) -> Iterator[None]:
    """One of the HEAVY slots of the charts; the wait ends with 409 when the request is overtaken meanwhile."""
    from bf_core.stats.routes_charts import SLOTS

    while not SLOTS.acquire(timeout=HEAVY_WAIT):
        tok.check()
    try:
        tok.check()
        yield
    finally:
        SLOTS.release()


def overtaken[T](common: Common, work: Callable[[Token], T]) -> T:
    """`work(token)` as the request of its channel: the answer, or 409 `superseded` if a newer request came."""
    tok = GATE.enter(common.channel, common.seq)
    try:
        out = work(tok)
        tok.check()  # overtaken at the very end: the newer answer is the one that counts
        return out
    finally:
        GATE.leave(tok)


# --- the request ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Common:
    """The keys every request of the display has (e17-api §12)."""

    filter_id: int | None
    restrict: dict[str, Any] | None
    channel: str | None
    seq: int


@dataclass(frozen=True)
class Colour:
    field: str | None
    kind: str
    lo: float | None
    hi: float | None
    log: bool
    breaks: tuple[float, ...]
    hidden: tuple[int, ...]


@dataclass(frozen=True)
class ViewSpec:
    common: Common
    colour: Colour
    mode: str
    extent: tuple[float, ...] | None
    limit: int


def body_of(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise ApiError(422, "bad_request", "Неверные данные запроса.", "Повторите действие в окне блочной модели.")
    return raw


def parse_common(body: Mapping[str, Any]) -> Common:
    fid = body.get("filter_id")
    if fid is not None and (isinstance(fid, bool) or not isinstance(fid, int)):
        raise bad("filter_id", "Номер фильтра — целое число.")
    quick = body.get("restrict")
    if quick is not None and not isinstance(quick, dict):
        raise bad("restrict", "Быстрый ограничитель: нужен объект.")
    channel = body.get("channel")
    if channel is not None and (not isinstance(channel, str) or not 1 <= len(channel) <= 64):
        raise bad("channel", "Канал запроса — строка от 1 до 64 знаков.")
    seq = body.get("seq", 0)
    if seq is None:
        seq = 0
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise bad("seq", "Номер запроса — целое число, не меньше нуля.")
    return Common(fid, quick, channel, seq)


def _numbers(raw: Any, param: str, what: str) -> list[float]:
    if not isinstance(raw, list):
        raise bad(param, f"{what}: нужен список чисел.")
    return [number(x, f"{param}.{i}", what) for i, x in enumerate(raw)]


def parse_colour(raw: Any) -> Colour:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise bad("colour", "Раскраска: нужен объект.")
    kind = raw.get("kind") or "none"
    if kind not in KINDS:
        raise bad("colour.kind", "Раскраска: «none», «scale», «classes» или «categories».")
    name = raw.get("field")
    if kind != "none" and (not isinstance(name, str) or not name.strip()):
        raise bad("colour.field", "Для раскраски нужно поле.", "Выберите поле в окне блочной модели.")
    lo = None if raw.get("from") is None else number(raw["from"], "colour.from", "Граница шкалы «от»")
    hi = None if raw.get("to") is None else number(raw["to"], "colour.to", "Граница шкалы «до»")
    log = raw.get("log", False)
    if not isinstance(log, bool):
        raise bad("colour.log", "Логарифмическая шкала: да или нет.")
    breaks = tuple(_numbers(raw.get("breaks") or [], "colour.breaks", "Границы градаций"))
    ascending = all(x < y for x, y in zip(breaks, breaks[1:], strict=False))
    if kind == "classes" and (not 1 <= len(breaks) <= 255 or not ascending):
        raise bad("colour.breaks", "Границы градаций: от 1 до 255 чисел строго по возрастанию.",
                  "Например [0,5; 1; 2].")  # fmt: skip
    hidden_raw = raw.get("hidden") or []
    if not isinstance(hidden_raw, list) or not all(isinstance(h, int) and not isinstance(h, bool) for h in hidden_raw):
        raise bad("colour.hidden", "Скрытые категории: список кодов.")
    if kind == "scale" and lo is not None and hi is not None and hi <= lo:
        raise bad("colour.to", "Граница шкалы «до» должна быть больше границы «от».")
    if kind == "scale" and log and lo is not None and lo <= 0:
        raise bad("colour.from", "Логарифмическая шкала требует границы «от» больше нуля.",
                  "Задайте границу больше нуля или выключите логарифмическую шкалу.")  # fmt: skip
    return Colour(None if kind == "none" else str(name).strip(), kind, lo, hi, log, breaks,
                  tuple(sorted(set(hidden_raw))))  # fmt: skip


def parse_view(raw: Any) -> ViewSpec:
    body = body_of(raw)
    mode = body.get("mode", "shell")
    if mode not in MODES:
        raise bad("mode", "Режим показа: «shell» (оболочка) или «all» (все блоки).")
    limit = body.get("limit")
    if limit is None:
        limit = DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int) or not MIN_LIMIT <= limit <= MAX_LIMIT:
        raise bad("limit", "Лимит граней: от 500 000 до 10 000 000.", "Задайте число граней в этих пределах.")
    extent = body.get("extent")
    box: tuple[float, ...] | None = None
    if extent is not None:
        vals = _numbers(extent, "extent", "Охват")
        if len(vals) != 6 or any(vals[a] > vals[a + 3] for a in range(3)):
            raise bad("extent", "Охват: шесть чисел xmin, ymin, zmin, xmax, ymax, zmax.")
        box = tuple(vals)
    return ViewSpec(parse_common(body), parse_colour(body.get("colour")), mode, box, limit)
