"""The tools of a request to Claude (подшаг 23.9; ТЗ 7.32; ADR 0029 decisions 17-18; e23-api.md §5).

    validate_ui_tools(raw) -> list[ToolDefinition]      the `tools` of the body of a turn, §5.1; ApiError 422
                                                        `assistant_bad_tools {detail}` for every violation
    build_tools(ui_tools, data_tools=()) -> list[dict]  the list for `tools=` of the API: `data_*` of the core, then
                                                        `ui_*` of the window; each group sorted by name (stable prefix
                                                        for the prompt cache)
    with_cache_control(tools) -> list[dict]             a copy with the cache mark on the last tool (the prompt cache
                                                        covers everything up to and including it)
    tools_size(tools) -> (bytes, tokens)                size of the list as sent (tokens: characters / 3)

`data_tools` is a parameter, not an import: the registry `DATA_TOOLS` of `data_tools.py` (подшаг 23.7) is passed by the
caller. An entry is a mapping or an object with `name`, `input_schema` and `description` (or, as in the samples of the
contract, `title`). The tools of the window are only checked and ordered here; their text is never shortened unless
sub-step 23.9б says so (`shorten_description`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import field_validator

from .errors import refuse
from .schemas import ToolDefinition, check_tools, parse_body
from .schemas_base import Body

DATA_PREFIX = "data_"
UI_PREFIX = "ui_"
DATA_NAME_RE = re.compile(r"^data_[a-z0-9_]{1,60}$")
CACHE_CONTROL = {"type": "ephemeral"}
#: Descriptions longer than this are shortened for Claude in 23.9б (the full text stays with the palette).
SHORT_DESCRIPTION = 600


class _ToolsBody(Body):
    """Only `tools`: the validators of `ToolDefinition` and `check_tools` of the contract do the checking."""

    tools: list[ToolDefinition]

    @field_validator("tools")
    @classmethod
    def _tools(cls, value: list[ToolDefinition]) -> list[ToolDefinition]:
        return check_tools(value)  # repeated name, count, size: the same rule as in the bodies of the turns


def validate_ui_tools(raw: Any) -> list[ToolDefinition]:
    """The tools of the window checked by §5.1: a list; each name `^ui_[a-z0-9_]{1,60}$`; no repeats; at most 400; each
    description at most 2000 characters; each schema an object of properties of string / number / integer / boolean
    (+ null) with `required` inside and `additionalProperties: false`; all of it at most 256 KB of JSON.
    Raises ApiError 422 `assistant_bad_tools {detail}` (the detail names the tool and the rule, never its text)."""
    if not isinstance(raw, list):
        raise refuse("assistant_bad_tools", detail="tools: a list of tools")
    return parse_body(_ToolsBody, {"tools": raw}).tools


def _field(entry: Any, key: str) -> Any:
    return entry.get(key) if isinstance(entry, Mapping) else getattr(entry, key, None)


def data_tool_definition(entry: Any) -> dict[str, Any]:
    """An entry of `DATA_TOOLS` as a tool of the API: `{name, description, input_schema}` (the title of the card is not
    sent to the model; a data tool without a `description` is described by its title)."""
    name = _field(entry, "name")
    schema = _field(entry, "input_schema")
    if not isinstance(name, str) or not DATA_NAME_RE.match(name) or not isinstance(schema, dict):
        raise ValueError(f"bad data tool: {str(name)[:64]}")  # a bug of the registry, not of a request
    description = _field(entry, "description") or _field(entry, "title") or name
    return {"name": name, "description": str(description), "input_schema": json.loads(json.dumps(schema))}


def ui_tool_definition(tool: ToolDefinition) -> dict[str, Any]:
    schema = json.loads(json.dumps(tool.input_schema))  # a deep copy: the request must not share it with the body
    return {"name": tool.name, "description": tool.description, "input_schema": schema}


def build_tools(
    ui_tools: Iterable[ToolDefinition | Mapping[str, Any]],
    data_tools: Iterable[Any] = (),
    *,
    description_limit: int | None = None,
    param_limit: int | None = None,
) -> list[dict[str, Any]]:
    """The `tools` of a request to the API: `data_*` of the core by name, then `ui_*` of the window by name.

    The order depends only on the set of names, so the same set of tools gives the same bytes (the prompt cache);
    a name that is in both groups cannot happen (prefixes), a repeat inside a group is `assistant_bad_tools`.
    `ui_tools` are already validated (`validate_ui_tools` or the body of a turn); mappings are validated here.
    `description_limit` / `param_limit` - shorten the description of a tool / of a parameter for Claude (23.9б; off by
    default: the measure of 23.9 decides whether and how much)."""
    ui = [t if isinstance(t, ToolDefinition) else _ui_from_mapping(t) for t in ui_tools]
    data = [data_tool_definition(e) for e in data_tools]
    names = [d["name"] for d in data] + [t.name for t in ui]
    if len(set(names)) != len(names):
        raise refuse("assistant_bad_tools", detail="tools: repeated name")
    out = sorted(data, key=lambda d: d["name"]) + sorted((ui_tool_definition(t) for t in ui), key=lambda d: d["name"])
    if description_limit is not None or param_limit is not None:
        for tool in out:
            _shorten(tool, description_limit, param_limit)
    return out


def _shorten(tool: dict[str, Any], description_limit: int | None, param_limit: int | None) -> None:
    if description_limit is not None:
        tool["description"] = shorten_description(tool["description"], description_limit)
    if param_limit is not None:
        for prop in tool["input_schema"].get("properties", {}).values():
            if isinstance(prop.get("description"), str):
                prop["description"] = shorten_description(prop["description"], param_limit)


def _ui_from_mapping(raw: Mapping[str, Any]) -> ToolDefinition:
    return parse_body(_ToolsBody, {"tools": [dict(raw)]}).tools[0]


def shorten_description(text: str, limit: int = SHORT_DESCRIPTION) -> str:
    """Cut a description for Claude at a sentence end (or a word) within `limit` characters; a short one is unchanged.
    Only for 23.9б, when the measure of 23.9 shows more than 160 KB."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("; "))
    if end >= limit // 2:
        return cut[: end + 1]
    return cut[: max(cut.rfind(" "), limit // 2)].rstrip(" ,;:") + "…"


def with_cache_control(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A copy of `tools` with `cache_control` on the last one; an empty list stays empty."""
    if not tools:
        return []
    marked = [dict(t) for t in tools]
    marked[-1] = {**marked[-1], "cache_control": dict(CACHE_CONTROL)}
    return marked


def tools_size(tools: list[dict[str, Any]]) -> tuple[int, int]:
    """(bytes of the JSON, estimate of tokens = characters / 3, rounded up) of the list as it goes to the API."""
    text = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
    return len(text.encode("utf-8")), -(-len(text) // 3)


def known_ui_names(tools: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """Names of the `ui_*` tools of a request: a call of another `ui_*` is answered with «Нет такой команды»."""
    return frozenset(t["name"] for t in tools if str(t.get("name", "")).startswith(UI_PREFIX))


__all__ = [
    "CACHE_CONTROL",
    "SHORT_DESCRIPTION",
    "build_tools",
    "data_tool_definition",
    "known_ui_names",
    "shorten_description",
    "tools_size",
    "validate_ui_tools",
    "with_cache_control",
]
