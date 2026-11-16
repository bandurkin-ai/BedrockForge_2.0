"""Rows of a table as JSON lines and back, by the column types of the current schema."""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import IO, Any

import sqlalchemy as sa

from bf_core.db.dialect import UtcDateTime


def encode_row(table: sa.Table, row: dict[str, Any]) -> bytes:
    out: dict[str, Any] = {}
    for col in table.columns:
        v = row.get(col.name)
        if isinstance(v, datetime):
            v = v.isoformat()
        elif isinstance(v, uuid.UUID):
            v = v.hex
        elif isinstance(v, bytes | bytearray | memoryview):
            v = {"$b64": base64.b64encode(bytes(v)).decode()}
        out[col.name] = v
    return json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=True).encode("utf-8") + b"\n"


def decode_row(table: sa.Table, raw: dict[str, Any]) -> dict[str, Any]:
    """Only the columns the current schema knows; types restored."""
    out: dict[str, Any] = {}
    for col in table.columns:
        if col.name not in raw:
            continue
        v = raw[col.name]
        if v is not None:
            if isinstance(col.type, UtcDateTime):
                v = datetime.fromisoformat(v)
            elif isinstance(col.type, sa.Uuid):
                v = uuid.UUID(v)
            elif isinstance(col.type, sa.LargeBinary) and isinstance(v, dict):
                v = base64.b64decode(v["$b64"])
        out[col.name] = v
    return out


def read_lines(f: IO[bytes]) -> Iterator[dict[str, Any]]:
    for line in f:
        line = line.strip()
        if line:
            yield json.loads(line)
