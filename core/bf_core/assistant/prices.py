"""Price list of the assistant models (ТЗ 7.32, ADR 0029 решение 13, вопрос № 128).

US dollars per 1 million tokens. The list is versioned (`PRICES_VERSION`): the cost of a call is computed and written
at the moment of the call, so a new list never rewrites the past. An unknown model has no price: `cost()` returns
`None` ("нет цены"), never 0 — a gap is not a zero (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PRICES_VERSION = "2026-11"
CACHE_WRITE_FACTOR = 1.25  # prompt-cache write = 1.25 x input price
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"


@dataclass(frozen=True)
class LongTier:
    """Prices of a request longer than `threshold` tokens (input + cache read + cache write of one request)."""

    threshold: int
    input: float
    output: float
    cache_read: float

    @property
    def cache_write(self) -> float:
        return round(self.input * CACHE_WRITE_FACTOR, 6)


@dataclass(frozen=True)
class ModelPrice:
    id: str
    title: str
    note: str
    input: float
    output: float
    cache_read: float
    long: LongTier | None = None
    default: bool = False

    @property
    def cache_write(self) -> float:
        return round(self.input * CACHE_WRITE_FACTOR, 6)


MODELS: tuple[ModelPrice, ...] = (
    ModelPrice(
        "claude-opus-5-5",
        "Opus 5.5",
        "Самая сильная: сложные вопросы и длинные цепочки действий.",
        4.0,
        20.0,
        0.20,
        default=True,
    ),
    ModelPrice("claude-sonnet-5-5", "Sonnet 5.5", "Быстрее и дешевле Opus: повседневные вопросы.", 2.0, 10.0, 0.10),
    ModelPrice(
        "claude-haiku-5-5",
        "Haiku 5.5",
        "Самая быстрая и дешёвая: простые команды и справки.",
        0.10,
        0.50,
        0.01,
        long=LongTier(100_000, 0.50, 2.50, 0.05),
    ),
    ModelPrice("claude-fable-5-2", "Fable 5.2", "Самая дорогая: проверка и трудные случаи.", 8.0, 40.0, 0.20),
)

EFFORTS: tuple[tuple[str, str], ...] = (
    ("low", "Быстро"),
    ("medium", "Обычно"),
    ("high", "Тщательно"),
    ("xhigh", "Очень тщательно"),
    ("max", "Максимум"),
)

_BY_ID = {m.id: m for m in MODELS}
_PER_MILLION = 1_000_000


def find_model(model: str | None) -> ModelPrice | None:
    """Exact id or id followed by `-` / `@` (dated snapshots, `claude-haiku-5-5-20261101`); otherwise `None`."""
    if not isinstance(model, str):
        return None
    model = model.strip()
    exact = _BY_ID.get(model)
    if exact is not None:
        return exact
    best: ModelPrice | None = None
    for m in MODELS:
        if model.startswith((m.id + "-", m.id + "@")) and (best is None or len(m.id) > len(best.id)):
            best = m
    return best


def _check(**counts: int) -> None:
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name}: нужно целое неотрицательное число токенов, получено {value!r}")


def is_long(model: str | None, input: int, cache_read: int = 0, cache_write: int = 0) -> bool:
    """True when the request is priced by the long tier (Haiku 5.5, № 128: input + cache read + cache write > 100k)."""
    _check(input=input, cache_read=cache_read, cache_write=cache_write)
    price = find_model(model)
    return bool(price and price.long and input + cache_read + cache_write > price.long.threshold)


def cost(model: str | None, input: int, output: int, cache_read: int = 0, cache_write: int = 0) -> float | None:
    """Cost of one API call in US dollars (rounded to 1e-9); `None` for an unknown model."""
    _check(input=input, output=output, cache_read=cache_read, cache_write=cache_write)
    price = find_model(model)
    if price is None:
        return None
    tier: ModelPrice | LongTier = price
    if is_long(model, input, cache_read, cache_write):
        assert price.long is not None
        tier = price.long
    total = (
        input * tier.input + output * tier.output + cache_read * tier.cache_read + cache_write * tier.cache_write
    ) / _PER_MILLION
    return round(total, 9)


def title_of(model: str | None) -> str:
    """Display name for the usage table; an unknown model is shown by its raw id."""
    price = find_model(model)
    return price.title if price else (model or "—")


def models_payload() -> list[dict[str, Any]]:
    """`models` of `GET /status` (docs/progress/e23-api.md §2)."""
    out: list[dict[str, Any]] = []
    for m in MODELS:
        long = None
        if m.long is not None:
            long = {
                "threshold": m.long.threshold,
                "input": m.long.input,
                "output": m.long.output,
                "cache_read": m.long.cache_read,
                "cache_write": m.long.cache_write,
            }
        out.append(
            {
                "id": m.id,
                "title": m.title,
                "default": m.default,
                "note": m.note,
                "price": {
                    "input": m.input,
                    "output": m.output,
                    "cache_read": m.cache_read,
                    "cache_write": m.cache_write,
                },
                "long": long,
            }
        )
    return out


def efforts_payload() -> list[dict[str, Any]]:
    """`efforts` of `GET /status`."""
    out: list[dict[str, Any]] = []
    for effort_id, title in EFFORTS:
        item: dict[str, Any] = {"id": effort_id, "title": title}
        if effort_id == DEFAULT_EFFORT:
            item["default"] = True
        out.append(item)
    return out
