"""Scenario files of the Anthropic mock: loading and matching a request to a step (stage E23, ADR 0029 decision 36)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "data" / "assistant" / "scenarios"


def resolve_scenario(name_or_path: str | os.PathLike[str], directory: Path = SCENARIO_DIR) -> Path:
    """`summary` -> `<scenarios>/summary.json`; an existing `.json` path is taken as is."""
    p = Path(name_or_path)
    if p.suffix == ".json" and p.is_file():
        return p
    q = directory / (p.name if p.suffix == ".json" else p.name + ".json")
    if not q.is_file():
        raise FileNotFoundError(f"scenario not found: {name_or_path} (looked in {directory})")
    return q


def load_scenario(*names: str | os.PathLike[str], directory: Path = SCENARIO_DIR) -> dict[str, Any]:
    """The steps of several scenario files, one file after another."""
    import json

    steps: list[dict[str, Any]] = []
    for n in names:
        doc = json.loads(resolve_scenario(n, directory).read_text(encoding="utf-8"))
        steps.extend(doc["steps"])
    return {"steps": steps}


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def request_facts(body: dict[str, Any]) -> dict[str, Any]:
    """What a step can match: text of the last user message, the tool results in it, the model.

    A user message that carries tool results has no text for `last_user_text` (a continuation is not a new question);
    the `<workspace>` block is cut out of the text (it repeats the screen state, e.g. the selected hole)."""
    messages = [m for m in body.get("messages") or [] if isinstance(m, dict)]
    last = messages[-1] if messages else {}
    blocks = _blocks(last.get("content")) if last.get("role") == "user" else []
    names: dict[str, str] = {}
    for m in messages:
        if m.get("role") == "assistant":
            for b in _blocks(m.get("content")):
                if b.get("type") == "tool_use":
                    names[str(b.get("id"))] = str(b.get("name"))
    results = [b for b in blocks if b.get("type") == "tool_result"]
    text = ""
    if not results:
        raw = "\n".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
        text = re.sub(r"<workspace>.*?</workspace>", "", raw, flags=re.S)
    return {
        "text": text,
        "results": [(names.get(str(b.get("tool_use_id")), ""), bool(b.get("is_error"))) for b in results],
        "model": body.get("model"),
    }


def step_fits(match: dict[str, Any], facts: dict[str, Any], nth: int) -> bool:
    """All keys of `match` hold: last_user_text (substring), tool_result_for, tool_result_error, model, nth."""
    if "last_user_text" in match and str(match["last_user_text"]) not in facts["text"]:
        return False
    if "model" in match and match["model"] != facts["model"]:
        return False
    if "nth" in match and match["nth"] != nth:
        return False
    if "tool_result_for" in match or "tool_result_error" in match:
        want, err = match.get("tool_result_for"), match.get("tool_result_error")
        if not any((want is None or n == want) and (err is None or e == err) for n, e in facts["results"]):
            return False
    return True
