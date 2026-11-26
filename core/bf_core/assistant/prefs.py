"""Model and effort of the assistant (ADR 0029 решение 15, e23-api.md §2).

`<data>/assistant/prefs.json` = {"model", "effort"} of the user. Order: the window (prefs.json) -> `ASSISTANT_MODEL` /
`ASSISTANT_EFFORT` of the environment -> defaults (`claude-opus-5-5`, `medium`). A wrong environment value (or a
hand-edited file) falls through to the next source with one line in the log; a wrong value from the window is
refused with 422 `bad_param {param}`.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bf_core.api.errors import ApiError
from bf_core.assistant import prices
from bf_core.assistant.usage import assistant_dir

log = logging.getLogger("bf_core.assistant")

PREFS_FILE = "prefs.json"
ENV_MODEL = "ASSISTANT_MODEL"
ENV_EFFORT = "ASSISTANT_EFFORT"

_MODEL_IDS = tuple(m.id for m in prices.MODELS)
_EFFORT_IDS = tuple(e for e, _ in prices.EFFORTS)
_warned: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class Resolved:
    model: str
    model_source: str  # "prefs" | "env" | "default"
    effort: str
    effort_source: str


def prefs_path(data_dir: str | os.PathLike[str] | None = None) -> Path:
    return assistant_dir(data_dir) / PREFS_FILE


def _warn_once(source: str, value: str, what: str) -> None:
    if (source, value) not in _warned:
        _warned.add((source, value))
        log.warning("assistant: %s %r is not valid for %s, the next source is used", source, value[:80], what)


def load(data_dir: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Raw `prefs.json` (only string values of known keys); a missing or damaged file is an empty one."""
    try:
        data = json.loads(prefs_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if k in ("model", "effort") and isinstance(v, str)}


def _pick(candidates: list[tuple[str, Any]], valid: tuple[str, ...], default: str, what: str) -> tuple[str, str]:
    for source, value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if text in valid:
            return text, source
        _warn_once(source, text, what)
    return default, "default"


def resolve(data_dir: str | os.PathLike[str] | None = None, env: Mapping[str, str] | None = None) -> Resolved:
    """Model and effort of a turn with the source of each (`prefs` | `env` | `default`)."""
    env = os.environ if env is None else env
    stored = load(data_dir)
    model, model_source = _pick(
        [("prefs", stored.get("model")), ("env", env.get(ENV_MODEL))], _MODEL_IDS, prices.DEFAULT_MODEL, "model"
    )
    effort, effort_source = _pick(
        [("prefs", stored.get("effort")), ("env", env.get(ENV_EFFORT))], _EFFORT_IDS, prices.DEFAULT_EFFORT, "effort"
    )
    return Resolved(model, model_source, effort, effort_source)


def _bad(param: str, value: Any) -> ApiError:
    if param == "model":
        names = ", ".join(m.title for m in prices.MODELS)
        return ApiError(
            422,
            "bad_param",
            f"Неизвестная модель Claude: «{str(value)[:60]}».",
            f"Выберите одну из моделей списка: {names}.",
            {"param": "model"},
        )
    names = ", ".join(title for _, title in prices.EFFORTS)
    return ApiError(
        422,
        "bad_param",
        f"Неизвестная глубина рассуждений: «{str(value)[:60]}».",
        f"Выберите один из уровней: {names}.",
        {"param": "effort"},
    )


def set_prefs(data_dir: str | os.PathLike[str] | None, model: str | None = None, effort: str | None = None) -> Resolved:
    """`PUT /prefs`: validates both values first (nothing is written on an error), merges with the file, writes it
    atomically and returns the new resolution. `None` leaves a value as it is."""
    if model is not None and (not isinstance(model, str) or model.strip() not in _MODEL_IDS):
        raise _bad("model", model)
    if effort is not None and (not isinstance(effort, str) or effort.strip() not in _EFFORT_IDS):
        raise _bad("effort", effort)
    data = load(data_dir)
    if model is not None:
        data["model"] = model.strip()
    if effort is not None:
        data["effort"] = effort.strip()
    path = prefs_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return resolve(data_dir)
