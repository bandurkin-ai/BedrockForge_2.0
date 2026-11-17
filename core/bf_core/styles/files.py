"""`.bfstyle.json` — the exchange file of styles (ТЗ 7.16; BF format, BF2 adds `uid`; BF files are read).

    {"format": "bedrockforge-styles", "version": 1, "styles": [{uid?, name, level, data_kind, body, default_for, …}]}

A name taken at the same level is decided per style («<level>:<name>»): replace, keep_both («… (2)», a new
uid) or skip.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import styles

from . import service
from .rules import DATA_KINDS, LEVELS, MAX_EXTRA_BYTES, MAX_NAME, body_error, default_for_error, json_size

FILE_FORMAT = "bedrockforge-styles"
FILE_VERSION = 1
MAX_IMPORT_STYLES = 1000
MAX_FILE_BYTES = 4 * 1024 * 1024
DECISIONS = ("replace", "keep_both", "skip")
_ENTRY_KEYS = ("uid", "name", "level", "data_kind", "body", "default_for")
_ROW_KEYS = ("id", "project_id", "created_at", "updated_at", "extra", "version", "author")
_UID = re.compile(r"^[0-9a-f]{32}$")


class FileError(Exception):
    """422: the file is not a styles file or a style in it is wrong (Russian text names it)."""


def export_file(conn: sa.Connection, ids: list[int] | None) -> dict[str, Any]:
    rows = service.list_styles(conn)
    if ids is not None:
        missing = set(ids) - {s["id"] for s in rows}
        if missing:
            raise service.NotFound(sorted(missing)[0])
        rows = [s for s in rows if s["id"] in set(ids)]
    extras = {
        int(r.id): r.extra or {}
        for r in conn.execute(sa.select(styles.c.id, styles.c.extra).where(styles.c.id.in_([s["id"] for s in rows])))
    }
    entries = []
    for s in rows:
        entry = {k: v for k, v in extras.get(s["id"], {}).items() if k not in _ENTRY_KEYS and k not in _ROW_KEYS}
        entry.update(
            uid=s["uid"],
            name=s["name"],
            level=s["level"],
            data_kind=s["data_kind"],
            body=s["body"],
            default_for=s["default_for"],
        )
        entries.append(entry)
    return {"format": FILE_FORMAT, "version": FILE_VERSION, "styles": entries}


def parse(file: Any) -> list[dict[str, Any]]:
    if not isinstance(file, dict) or file.get("format") != FILE_FORMAT:
        raise FileError("Это не файл стилей BedrockForge.")
    if json_size(file) > MAX_FILE_BYTES:
        raise FileError("Файл стилей больше 4 МБ.")
    version = file.get("version")
    if isinstance(version, bool) or version != FILE_VERSION:
        raise FileError(f"Неизвестная версия файла стилей: {version}.")
    raw = file.get("styles")
    if not isinstance(raw, list):
        raise FileError("В файле стилей нет списка стилей.")
    if len(raw) > MAX_IMPORT_STYLES:
        raise FileError(f"В файле слишком много стилей (больше {MAX_IMPORT_STYLES}).")
    entries = []
    for i, e in enumerate(raw):
        if not isinstance(e, dict):
            raise FileError(f"Стиль {i + 1} в файле: должен быть объектом.")
        name = e.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_NAME:
            raise FileError(f"Стиль {i + 1} в файле: нужно название не длиннее {MAX_NAME} символов.")
        name = name.strip()
        if e.get("level") not in LEVELS:
            raise FileError(f"Стиль «{name}»: неизвестный уровень «{e.get('level')}».")
        data_kind = e.get("data_kind", "number")
        if data_kind not in DATA_KINDS:
            raise FileError(f"Стиль «{name}»: неизвестный вид данных «{data_kind}».")
        default_for = e.get("default_for", [])
        for err in (body_error(e.get("body")), default_for_error(default_for)):
            if err:
                raise FileError(f"Стиль «{name}»: {err}.")
        extra = {k: v for k, v in e.items() if k not in _ENTRY_KEYS and k not in _ROW_KEYS}
        if json_size(extra) > MAX_EXTRA_BYTES:
            raise FileError(f"Стиль «{name}»: слишком много дополнительных данных.")
        uid = e.get("uid")
        entries.append(
            {
                "uid": uid if isinstance(uid, str) and _UID.match(uid) else None,
                "name": name,
                "level": e["level"],
                "data_kind": data_kind,
                "body": e["body"],
                "default_for": default_for,
                "extra": extra,
            }
        )
    return entries


def _free_name(name: str, taken: set[str]) -> str:
    """«name (2)», «name (3)»…: the first one not taken (by name key)."""
    n = 2
    while True:
        suffix = f" ({n})"
        candidate = name[: MAX_NAME - len(suffix)].rstrip() + suffix
        if dialect.name_key(candidate) not in taken:
            return candidate
        n += 1


def import_file(conn: sa.Connection, file: Any, conflicts: Mapping[str, str], default: str) -> dict[str, Any]:
    entries = parse(file)
    existing = {(s["level"], dialect.name_key(s["name"])): s for s in service.list_styles(conn)}
    uids = {s["uid"] for s in existing.values()}
    created = replaced = skipped = 0
    renamed = []
    for e in entries:
        level, name = e["level"], e["name"]
        current = existing.get((level, dialect.name_key(name)))
        uid = e["uid"] if e["uid"] and e["uid"] not in uids else None
        if current is not None:
            decision = conflicts.get(f"{level}:{name}", default)
            if decision == "skip":
                skipped += 1
                continue
            if decision == "replace":
                changes = {k: e[k] for k in ("data_kind", "body", "default_for", "extra")}
                existing[(level, dialect.name_key(name))] = service.patch(
                    conn, current["id"], changes, current["version"]
                )
                replaced += 1
                continue
            taken = {k for lv, k in existing if lv == level}
            name = _free_name(name, taken)
            renamed.append({"from": e["name"], "to": name, "level": level})
            uid = None  # «оставить оба»: a new style, links of the file go to the one kept
        s = service.create(
            conn,
            name=name,
            level=level,
            data_kind=e["data_kind"],
            body=e["body"],
            default_for=e["default_for"],
            extra=e["extra"],
            uid=uid,
        )
        existing[(level, dialect.name_key(name))] = s
        uids.add(s["uid"])
        created += 1
    return {"created": created, "replaced": replaced, "skipped": skipped, "renamed": renamed}
