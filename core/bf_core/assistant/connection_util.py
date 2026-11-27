"""Small pure helpers of `connection.py` (time formats of the secret, the efforts of a model)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bf_core.assistant.schemas_base import EFFORTS


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def unix(value: Any) -> int | None:
    """`expires_at` as unix seconds from unix seconds or an ISO 8601 string; None - no expiry known."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def iso(value: int | None) -> str | None:
    return None if value is None else datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def efforts(info: Any) -> list[str] | None:
    """The efforts the model supports (`capabilities.effort`); None when the answer does not say."""
    effort = getattr(getattr(info, "capabilities", None), "effort", None)
    if effort is None:
        return None
    if not getattr(effort, "supported", False):
        return []
    return [e for e in EFFORTS if getattr(getattr(effort, e, None), "supported", False)]
