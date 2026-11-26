"""Shapes of the Claude assistant API (подшаг 23.1; ТЗ 7.32; ADR 0029; e23-api.md §2-§10 - the contract wins).

Pydantic models of the bodies of requests (`*Body`, extra keys ignored), of the answers and of the events of the stream
(extra keys forbidden: the answer is exactly what the contract says). A gap is `null`, never 0. No database, no SDK.
Here: the status, the connection, the usage, the tools, the context, the bodies of the turns, `parse_body`. The limits,
`EFFORTS` and the scalar types - `schemas_base.py`; the dialogs, the items, `Pending`, the events (`parse_event`,
`EVENT_MODELS`, `ITEM`) - `schemas_dialog.py`.

    body = parse_body(TurnBody, raw)       # ApiError 422 `bad_param {param}` (the first key that failed),
                                           # `assistant_bad_key_format`, `assistant_bad_tools {detail}`

The text of a refusal is in `errors.py`; the value of a secret is never part of an error.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from bf_core.api.errors import ApiError

from .errors import Refuse, bad_param, refuse
from .schemas_base import (
    CODE_MAX,
    CONTEXT_MAX,
    CONTEXT_WINDOWS_MAX,
    DIALOG_ID_RE,
    JSON_TYPES,
    KEY_RE,
    PENDING_ID_RE,
    RESULTS_MAX,
    TEXT_MAX,
    TITLE_MAX,
    TOOL_DESCRIPTION_MAX,
    TOOL_NAME_RE,
    TOOL_TEXT_MAX,
    TOOL_USE_ID_RE,
    TOOLS_BYTES_MAX,
    TOOLS_MAX,
    Body,
    Count,
    Effort,
    Iso,
    Kind,
    Out,
    SecretKind,
    Source,
    State,
    Str200,
    Tokens,
    ViewMode,
)

# --- §2 status, prefs -------------------------------------------------------------------------------------------------


class Ref(Out):  # organization, workspace
    id: str
    name: str | None


class Price(Out):  # USD per 1 million tokens
    input: float
    output: float
    cache_read: float
    cache_write: float


class LongPrice(Price):  # a request above `threshold` input tokens (№ 128)
    threshold: int


class ModelInfo(Out):
    id: str
    title: str
    default: bool = False
    note: str = ""
    price: Price
    long: LongPrice | None = None


class EffortInfo(Out):
    id: Effort
    title: str
    default: bool = False


class Status(Out):
    state: State
    kind: Kind | None
    mask: str | None
    organization: Ref | None
    workspace: Ref | None
    online: bool | None  # the result of the last call of the API; null - there was none
    checked_at: Iso | None
    model: str
    model_source: Source
    effort: Effort
    effort_source: Source
    ant_available: bool
    secure_storage: bool
    seal_needed: bool
    subscription_note: str
    models: list[ModelInfo]
    efforts: list[EffortInfo]
    prices_version: str


class PrefsBody(Body):
    """`PUT /prefs`. Whether the model is known is decided by the route (the table of prices): `bad_param {model}`."""

    model: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")] | None = None
    effort: Effort | None = None


# --- §3 connection ----------------------------------------------------------------------------------------------------


def _key(value: str) -> str:
    value = value.strip()
    if not KEY_RE.match(value):
        raise Refuse("assistant_bad_key_format")
    return value


class KeyBody(Body):
    """`POST /connection/key`: the format is checked here (`assistant_bad_key_format`), the key - by the API."""

    key: str

    @field_validator("key")
    @classmethod
    def _format(cls, value: str) -> str:
        return _key(value)


class ConnectionTest(Out):
    ok: bool
    latency_ms: Count
    model: str
    model_available: bool
    effort_supported: list[Effort] | None
    organization_id: str | None
    message: str


class ApiKeySecret(Body):
    key: str

    @field_validator("key")
    @classmethod
    def _format(cls, value: str) -> str:
        return _key(value)


class AccountSecret(Body):
    access_token: Annotated[str, StringConstraints(min_length=1)]
    expires_at: Iso | None = None
    refresh_token: Annotated[str, StringConstraints(min_length=1)]
    client_id: Annotated[str, StringConstraints(min_length=1)]
    organization_id: str | None = None
    workspace_id: str | None = None
    base_url: str | None = None


class SecretPutBody(Body):
    """`PUT /connection/secret` (shell only). The secret is checked by the kind and kept as a plain dict."""

    kind: SecretKind
    secret: dict[str, Any]
    secure_storage: bool

    @field_validator("secret")
    @classmethod
    def _by_kind(cls, value: dict[str, Any], info: Any) -> dict[str, Any]:
        kind = info.data.get("kind")
        if kind is None:
            return value
        try:
            model: BaseModel = (ApiKeySecret if kind == "api_key" else AccountSecret).model_validate(value)
        except Refuse:
            raise
        except ValidationError:
            raise PydanticCustomError("secret", "bad secret") from None  # no input in the error
        return model.model_dump(exclude_none=True)


class SecretPutAnswer(Out):
    ok: bool
    kind: SecretKind
    mask: str | None


class SecretGetAnswer(Out):  # `GET /connection/secret` (shell only); 204 - nothing to keep
    kind: SecretKind
    secret: dict[str, Any]
    revision: Count


class SealedBody(Body):  # `POST /connection/sealed`
    revision: Count


class LoginStart(Out):
    login_id: str
    url: str
    expires_at: Iso


class LoginFinishBody(Body):
    code: Annotated[str, StringConstraints(min_length=1, max_length=CODE_MAX)]

    @field_validator("code")
    @classmethod
    def _plain(cls, value: str) -> str:
        if not value.strip() or any(c in value for c in "\r\n\0"):
            raise ValueError("code")
        return value


# --- §4 usage ---------------------------------------------------------------------------------------------------------


class ModelUsage(Out):
    model: str
    title: str
    requests: Count
    tokens: Tokens
    cost_usd: float | None  # null - the model has no price; never 0


class Period(Out):
    requests: Count
    tokens: Tokens
    cost_usd: float | None  # the sum of the known; null - there is nothing to sum
    unpriced_requests: Count = 0  # calls of a model that has no price
    by_model: list[ModelUsage]


class Usage(Out):
    today: Period
    days30: Period
    all: Period
    prices_version: str
    since: Iso | None


class UsageLine(Out):  # one line of `usage.jsonl`
    at: Iso
    model: str
    input: Count
    output: Count
    cache_read: Count
    cache_write: Count
    cost_usd: float | None
    long: bool
    dialog_uid: str | None
    project_uid: str | None
    prices_version: str


# --- §5 tools ---------------------------------------------------------------------------------------------------------


def _bad_tool(detail: str) -> Refuse:
    return Refuse("assistant_bad_tools", detail=detail)


def _check_schema(schema: dict[str, Any]) -> None:
    """The `input_schema` of `commandTools()` (§5.1): an object of properties of the four types, `required` inside."""
    props = schema.get("properties")
    if schema.get("type") != "object" or not isinstance(props, dict) or schema.get("additionalProperties") is not False:
        raise _bad_tool("input_schema: object with properties and additionalProperties false")
    for key, prop in props.items():
        if not isinstance(key, str) or not 1 <= len(key) <= 64 or not isinstance(prop, dict):
            raise _bad_tool(f"property {str(key)[:64]}: bad name or schema")
        t = prop.get("type")
        if not (t in JSON_TYPES or (isinstance(t, list) and len(t) == 2 and t[0] in JSON_TYPES and t[1] == "null")):
            raise _bad_tool(f"property {key}: type")
        if "enum" in prop and not isinstance(prop["enum"], list):
            raise _bad_tool(f"property {key}: enum")
        if "description" in prop and not isinstance(prop["description"], str):
            raise _bad_tool(f"property {key}: description")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(r not in props for r in required):
        raise _bad_tool("required: names of properties")


class ToolDefinition(Body):
    """A tool of the window (§5.1): the shape of `commandTools()` of the registry."""

    name: str
    description: Annotated[str, StringConstraints(max_length=TOOL_DESCRIPTION_MAX)]
    input_schema: dict[str, Any]

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not TOOL_NAME_RE.match(value):
            raise _bad_tool("name: ui_<a-z0-9_>")
        return value

    @field_validator("input_schema")
    @classmethod
    def _schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        _check_schema(value)
        return value


def check_tools(tools: list[ToolDefinition]) -> list[ToolDefinition]:
    """No repeated name, at most 400, at most 256 KB of JSON (otherwise 422 `assistant_bad_tools {detail}`)."""
    if len(tools) > TOOLS_MAX:
        raise _bad_tool(f"more than {TOOLS_MAX} tools")
    names = [t.name for t in tools]
    if len(set(names)) != len(names):
        raise _bad_tool("repeated name")
    if len(json.dumps([t.model_dump() for t in tools], ensure_ascii=False).encode()) > TOOLS_BYTES_MAX:
        raise _bad_tool("more than 256 KB")
    return tools


class DataToolInfo(Out):
    """A data tool of the core (§5.2): `DATA_TOOLS` of `data_tools.py` and the sample `data_tools`."""

    name: str
    title: str
    input_schema: dict[str, Any]


# --- §6 context -------------------------------------------------------------------------------------------------------


class ProjectRef(Out):
    uid: Str200
    name: Str200


class SetRef(Out):
    id: int
    name: Str200
    holes: Count


class HoleRef(Out):
    id: Str200
    set_id: int


class ViewRef(Out):
    mode: ViewMode
    perspective: bool
    vexag: float


class SectionRef(Out):
    x0: float
    y0: float
    az: float
    length: float
    towards: float
    away: float


class SelectionRef(Out):
    kind: Str200
    id: int
    name: Str200


class Context(Body):
    """The context of the screen (§6): <= 4 KB of JSON, strings <= 200, windows <= 20 -> `bad_param {context}`."""

    project: ProjectRef | None = None
    set: SetRef | None = None
    hole: HoleRef | None = None
    panels: list[Str200] = Field(default_factory=list)
    windows: list[Str200] = Field(default_factory=list, max_length=CONTEXT_WINDOWS_MAX)
    view: ViewRef | None = None
    section: SectionRef | None = None
    selection: SelectionRef | None = None
    audit: Str200 | None = None

    @model_validator(mode="before")
    @classmethod
    def _size(cls, raw: Any) -> Any:
        if isinstance(raw, dict) and len(json.dumps(raw, ensure_ascii=False, default=str).encode()) > CONTEXT_MAX:
            raise ValueError("context")
        return raw


# --- §8-§9 turn -------------------------------------------------------------------------------------------------------


class TurnBody(Body):
    """`POST /turns` (SSE). `dialog_id: null` - a new dialog."""

    dialog_id: Annotated[str, StringConstraints(pattern=DIALOG_ID_RE.pattern)] | None = None
    text: Annotated[str, StringConstraints(min_length=1, max_length=TEXT_MAX)]
    context: Context
    tools: list[ToolDefinition]

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text")
        return value

    @field_validator("tools")
    @classmethod
    def _tools(cls, value: list[ToolDefinition]) -> list[ToolDefinition]:
        return check_tools(value)


class ToolResult(Body):
    tool_use_id: Annotated[str, StringConstraints(pattern=TOOL_USE_ID_RE.pattern)]
    ok: bool
    text: str
    title: Annotated[str, StringConstraints(max_length=TITLE_MAX)] | None = None

    @field_validator("text")
    @classmethod
    def _cut(cls, value: str) -> str:
        return value if len(value) <= TOOL_TEXT_MAX else value[: TOOL_TEXT_MAX - 1] + "…"


class ToolResultsBody(Body):
    """`POST /dialogs/{id}/tool_results` (SSE): the results of all the `ui` calls of the `pending`, once each."""

    pending_id: Annotated[str, StringConstraints(pattern=PENDING_ID_RE.pattern)]
    results: Annotated[list[ToolResult], Field(min_length=1, max_length=RESULTS_MAX)]
    context: Context
    tools: list[ToolDefinition]

    @field_validator("results")
    @classmethod
    def _once(cls, value: list[ToolResult]) -> list[ToolResult]:
        ids = [r.tool_use_id for r in value]
        if len(set(ids)) != len(ids):
            raise ValueError("results")
        return value

    @field_validator("tools")
    @classmethod
    def _tools(cls, value: list[ToolDefinition]) -> list[ToolDefinition]:
        return check_tools(value)


# --- the parser of the bodies -----------------------------------------------------------------------------------------


def _where(loc: tuple[Any, ...]) -> str:
    return ".".join(str(p) for p in loc) or "body"


def parse_body[B: BaseModel](model: type[B], raw: Any) -> B:
    """The body of a request as `model`; an unfit one - ApiError 422: `assistant_bad_tools {detail}` for `tools`,
    the code of a `Refuse` of a validator, otherwise `bad_param {param}` (the first key of the body that failed). The
    error never carries the value (it may be a key or a code)."""
    if not isinstance(raw, dict):
        raise bad_param("body")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise _refusal(exc) from None


def _refusal(exc: ValidationError) -> ApiError:
    err = exc.errors(include_input=False, include_url=False)[0]
    loc = tuple(err.get("loc", ()))
    cause = (err.get("ctx") or {}).get("error")
    if isinstance(cause, Refuse):
        extra = dict(cause.extra)
        if cause.code == "assistant_bad_tools":
            extra["detail"] = f"{_where(loc)}: {extra.get('detail', '')}".rstrip(": ")
        return refuse(cause.code, cause.message, cause.hint, **extra)
    if loc and loc[0] == "tools":
        return refuse("assistant_bad_tools", detail=f"{_where(loc)}: {err.get('type', 'invalid')}")
    return bad_param(str(loc[0]) if loc else "body")
