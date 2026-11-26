"""Limits, scalar types and the two base classes of the models of the assistant API (подшаг 23.1; e23-api.md).

`Out` - an answer, an event, a stored form: exactly the keys of the contract. `Body` - the body of a request: a key the
contract does not know is ignored. Both are strict: "5" is not 5."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# --- limits of the contract -------------------------------------------------------------------------------------------

TEXT_MAX = 20_000  # §8: the question
TITLE_MAX = 200  # §7: the title of a dialog
CODE_MAX = 4096  # §3: the code of the login
TOOL_TEXT_MAX = 4000  # §9: the text of a result of a tool, a summary of a card
CONTEXT_MAX = 4096  # §6: the context, bytes of JSON
CONTEXT_STR_MAX = 200
CONTEXT_WINDOWS_MAX = 20
TOOLS_MAX = 400  # §5.1
TOOLS_BYTES_MAX = 256 * 1024
TOOL_DESCRIPTION_MAX = 2000
RESULTS_MAX = 64
DATA_TEXT_MAX = 16_000  # §5.2: the answer of a data tool, characters
PAGE_MAX = 200

KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,512}$")
TOOL_NAME_RE = re.compile(r"^ui_[a-z0-9_]{1,60}$")
DIALOG_ID_RE = re.compile(r"^(\d{1,12}|t-[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$")
PENDING_ID_RE = re.compile(r"^p-[A-Za-z0-9_-]{1,64}$")
TOOL_USE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ISO_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$"
JSON_TYPES = ("string", "number", "integer", "boolean")

Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
State = Literal["none", "connected", "login"]
Kind = Literal["api_key", "env", "account"]
SecretKind = Literal["api_key", "account"]
Source = Literal["prefs", "env", "default"]
Side = Literal["data", "ui"]
ToolStatus = Literal["ok", "error", "declined", "waiting"]
DialogState = Literal["idle", "running", "pending", "error"]
TurnState = Literal["idle", "pending", "error", "cancelled"]
ViewMode = Literal["iso", "top", "front", "side", "section"]

Iso = Annotated[str, StringConstraints(pattern=ISO_RE)]  # "2026-11-26T10:00:00Z"
Name = Annotated[str, StringConstraints(min_length=1, max_length=200)]
Count = Annotated[int, Field(ge=0)]
Str200 = Annotated[str, StringConstraints(max_length=CONTEXT_STR_MAX)]


class Out(BaseModel):
    """An answer / an event / a stored form: exactly the keys of the contract."""

    model_config = ConfigDict(extra="forbid", strict=True, protected_namespaces=())


class Body(BaseModel):
    """A body of a request: a key the contract does not know is ignored; types are not coerced ("5" is not 5)."""

    model_config = ConfigDict(extra="ignore", strict=True, protected_namespaces=())


class Tokens(Out):
    input: Count
    output: Count
    cache_read: Count
    cache_write: Count
