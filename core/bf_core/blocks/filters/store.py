"""Stored filters (ТЗ 7.35 Д-02; ADR 0023 §8, decisions 11–12; подшаг 17.7): `objects(kind="filter", parent_id=NULL)`,
names unique among the filters (`name_key`), no files; `meta` — `schema.meta_of`.

References. A filter that names a wireframe or a surface keeps `object_refs(src=filter, dst=wireframe,
role="filter_src")`: the wireframe is not deleted while a filter names it (its own check lists the filters). The
users of a filter are the rows `object_refs(dst=filter, role="filter")` (the consumers of Э17–Э19: a calculator
field, a report, an estimation…) and the view settings of the block models: `block_styles["<model id>"]
.filter_id` of the project view and of the saved views (`viewstate/keys.py`). A filter with a user is not
deleted: 422 `filter_in_use {usage}`. `usage` is `[{kind: "object" | "view", id, name, role}]`; for a view of the
project `id` is the model, `name` — «Вид проекта · ‹модель›», `role` — `block_style`; for a saved view `id` is the
view, `role` — `block_style`.

Versions: a `PUT` without `expected_version` or with another one — 409 `version_conflict {version}`. A wireframe
the tree names must exist (422 `wireframe_not_found {param}`), except one the stored filter already named and the
project has lost since (an import without it): the leaf stays «недоступен».
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import object_refs, objects, settings

from . import schema as fs

KIND = "filter"
ROLE_USE = "filter"  # object_refs(src=consumer, dst=filter)
ROLE_SRC = "filter_src"  # object_refs(src=filter, dst=wireframe)
VIEW_KEY = "block_styles"
MAX_NAME = 200
_CHUNK = 500


def not_found() -> ApiError:
    return ApiError(404, "filter_not_found", "Фильтр не найден.", "Обновите список фильтров.")


def conflict(version: int) -> ApiError:
    return ApiError(409, "version_conflict", "Фильтр изменён в другом окне.",
                    "Перечитайте фильтр или сохраните свой как новый.", {"version": version})  # fmt: skip


def bad_param(param: str, message: str, hint: str) -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


# --- names ------------------------------------------------------------------------------------------------------------


def clean_name(name: Any) -> str:
    text = " ".join(name.split())[:MAX_NAME].rstrip() if isinstance(name, str) else ""
    if not text:
        raise ApiError(422, "name_empty", "Название фильтра не может быть пустым.", "Введите название фильтра.",
                       {"param": "name"})  # fmt: skip
    return text


def _taken(conn: sa.Connection, name: str, but: int | None = None) -> bool:
    q = sa.select(objects.c.id).where(objects.c.kind == KIND, objects.c.name_key == dialect.name_key(name))
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def _final_name(conn: sa.Connection, name: Any, but: int | None = None) -> str:
    final = clean_name(name)
    if _taken(conn, final, but):
        raise ApiError(409, "name_taken", f"Фильтр «{final}» уже есть.", "Введите другое название.",
                       {"param": "name"})  # fmt: skip
    return final


def clean_note(note: Any) -> str | None:
    if note is None:
        return None
    if not isinstance(note, str) or len(note) > fs.MAX_NOTE:
        raise bad_param("note", "Примечание — текст до 2000 символов.", "Сократите примечание.")
    return note.strip() or None


# --- rows -------------------------------------------------------------------------------------------------------------


def _row(conn: sa.Connection, filter_id: int) -> Any:
    r = conn.execute(sa.select(objects).where(objects.c.id == filter_id, objects.c.kind == KIND)).first()
    if r is None:
        raise not_found()
    return r


def _existing(conn: sa.Connection, ids: list[int]) -> dict[int, str]:
    """The wireframes among `ids`: id → name."""
    out: dict[int, str] = {}
    for i in range(0, len(ids), _CHUNK):
        rows = conn.execute(
            sa.select(objects.c.id, objects.c.name).where(objects.c.kind == "wireframe",
                                                          objects.c.id.in_(ids[i : i + _CHUNK]))
        )  # fmt: skip
        out.update({int(r.id): str(r.name) for r in rows})
    return out


def _check_objects(conn: sa.Connection, rules: Mapping[str, Any], tolerated: set[int]) -> dict[int, str]:
    """The names of the wireframes of the tree; a new one that does not exist — 422 `wireframe_not_found`."""
    names = _existing(conn, fs.object_ids(rules))
    for path, kind, body in fs.leaves(rules):
        oid = body.get("id")
        if oid is not None and int(oid) not in names and int(oid) not in tolerated:
            what = "Каркас" if kind == "wireframe" else "Поверхность"
            raise ApiError(422, "wireframe_not_found", f"{what} №{oid} не найден в проекте.",
                           "Выберите каркас из списка проекта.", {"param": path})  # fmt: skip
    return names


def _sync_refs(conn: sa.Connection, filter_id: int, names: Mapping[int, str]) -> None:
    conn.execute(sa.delete(object_refs).where(object_refs.c.src_id == filter_id, object_refs.c.role == ROLE_SRC))
    for wf_id in sorted(names):
        conn.execute(sa.insert(object_refs).values(src_id=filter_id, dst_id=wf_id, role=ROLE_SRC))


def create(conn: sa.Connection, raw: Mapping[str, Any], author: str | None = None) -> int:
    """A filter (in the caller's write transaction); `raw` — `{name, rules | expr, note?, folder?}`. Returns its id."""
    from bf_core.projects.catalog import norm_folder

    final = _final_name(conn, raw.get("name"))
    rules = fs.rules_of_body(raw, required=True)
    assert rules is not None
    names = _check_objects(conn, rules, set())
    folder = raw.get("folder")
    if folder is not None and not isinstance(folder, str):
        raise bad_param("folder", "Папка — текст.", "Укажите папку текстом.")
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, folder=norm_folder(folder),
            sort_key=0.0, state="ready", version=1, meta=fs.meta_of(rules, clean_note(raw.get("note"))),
            author=author, created_at=now, updated_at=now,
        )
    )  # fmt: skip
    fid = int(res.inserted_primary_key[0])  # type: ignore[index]
    _sync_refs(conn, fid, names)
    return fid


def update(conn: sa.Connection, filter_id: int, raw: Mapping[str, Any]) -> int:
    """The fields given replace those stored; returns the new version. `expected_version` is required: none, or
    another one than the stored — 409 `version_conflict {version}`."""
    row = _row(conn, filter_id)
    version = raw.get("expected_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != int(row.version):
        raise conflict(int(row.version))
    meta = dict(row.meta or {})
    rules = fs.rules_of_body(raw, required=False)
    names: Mapping[int, str] | None = None
    if rules is not None:
        old = fs.object_ids(meta.get("rules") or {})
        alive = _existing(conn, old)
        names = _check_objects(conn, rules, {i for i in old if i not in alive})
    else:
        rules = meta.get("rules") or {}
    note = clean_note(raw["note"]) if "note" in raw else meta.get("note")
    final = str(row.name) if raw.get("name") is None else _final_name(conn, raw["name"], filter_id)
    n = conn.execute(
        sa.update(objects).where(objects.c.id == filter_id, objects.c.version == version).values(
            name=final, name_key=dialect.name_key(final), meta=fs.meta_of(rules, note), version=version + 1,
            updated_at=dialect.utcnow(),
        )
    ).rowcount  # fmt: skip
    if n != 1:
        raise conflict(int(_row(conn, filter_id).version))
    if names is not None:
        _sync_refs(conn, filter_id, names)
    return version + 1


# --- users ------------------------------------------------------------------------------------------------------------


def _styles_of(value: Any) -> dict[str, Any]:
    from bf_core.viewstate.keys import normalize

    try:
        out = normalize(VIEW_KEY, value, strict=False)
    except ValueError:
        return {}
    return dict(out) if isinstance(out, dict) else {}


def _names_of(conn: sa.Connection, ids: set[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    listed = sorted(ids)
    for i in range(0, len(listed), _CHUNK):
        rows = conn.execute(sa.select(objects.c.id, objects.c.name).where(objects.c.id.in_(listed[i : i + _CHUNK])))
        out.update({int(r.id): str(r.name) for r in rows})
    return out


def usage(conn: sa.Connection, filter_id: int) -> list[dict[str, Any]]:
    """`[{kind, id, name, role}]`: the objects that refer to the filter (every role: each forbids deleting), then the
    view settings of the block models that name it (the project view, then the saved views)."""
    from bf_core.viewstate.keys import SCOPE
    from bf_core.viewstate.views import KIND as VIEW

    _row(conn, filter_id)
    rows = conn.execute(
        sa.select(objects.c.id, objects.c.name, object_refs.c.role)
        .join(objects, objects.c.id == object_refs.c.src_id)
        .where(object_refs.c.dst_id == filter_id)
        .order_by(objects.c.name_key, objects.c.id, object_refs.c.role)
    ).all()
    out: list[dict[str, Any]] = [{"kind": "object", "id": int(r.id), "name": str(r.name), "role": str(r.role)}
                                 for r in rows]  # fmt: skip
    current = conn.execute(
        sa.select(settings.c.value).where(settings.c.scope == SCOPE, settings.c.key == VIEW_KEY)
    ).scalar()
    models = sorted(
        int(k) for k, s in _styles_of(current).items() if isinstance(s, dict) and s.get("filter_id") == filter_id
    )
    names = _names_of(conn, set(models))
    out += [{"kind": "view", "id": m, "name": f"Вид проекта · {names[m]}", "role": "block_style"}
            for m in models if m in names]  # fmt: skip
    saved = conn.execute(sa.select(objects.c.id, objects.c.name, objects.c.meta).where(objects.c.kind == VIEW)
                         .order_by(objects.c.name_key, objects.c.id)).all()  # fmt: skip
    for r in saved:
        styles = _styles_of(((r.meta or {}).get("keys") or {}).get(VIEW_KEY))
        if any(isinstance(s, dict) and s.get("filter_id") == filter_id for s in styles.values()):
            out.append({"kind": "view", "id": int(r.id), "name": str(r.name), "role": "block_style"})
    return out


def delete(conn: sa.Connection, filter_id: int) -> None:
    used = usage(conn, filter_id)
    if used:
        names = ", ".join(f"«{u['name']}»" for u in used[:5]) + (" и другие" if len(used) > 5 else "")
        raise ApiError(422, "filter_in_use", f"Фильтр используется: {names}.",
                       "Выберите другой фильтр в этих местах или удалите их, затем удалите фильтр.",
                       {"usage": used})  # fmt: skip
    conn.execute(sa.delete(objects).where(objects.c.id == filter_id))  # its `filter_src` rows go by cascade


# --- reading ----------------------------------------------------------------------------------------------------------


def rules_of(conn: sa.Connection, filter_id: int) -> dict[str, Any]:
    """The tree of a stored filter (`restrict.py`, the consumers); 404 `filter_not_found`."""
    return dict((_row(conn, filter_id).meta or {}).get("rules") or {})


def _form(row: Any, names: Mapping[int, str], *, full: bool) -> dict[str, Any]:
    meta = row.meta or {}
    rules = meta.get("rules") or {}
    out: dict[str, Any] = {
        "id": int(row.id), "name": row.name, "folder": row.folder, "version": int(row.version),
        "text": meta.get("text"), "title": fs.title_of(rules, names) if rules else "",
        "fields": meta.get("fields") or [], "note": meta.get("note"), "created_at": row.created_at,
        "updated_at": row.updated_at,
    }  # fmt: skip
    if full:
        listed = [{"id": body.get("id"), "name": names.get(int(body["id"])) if body.get("id") is not None else None,
                   "available": body.get("id") is not None and int(body["id"]) in names}
                  for _, _, body in fs.leaves(rules)]  # fmt: skip
        out["rules"] = rules
        once: dict[Any, dict[str, Any]] = {}
        for w in listed:
            once.setdefault(w["id"], w)  # once each, in the order of the tree
        out["wireframes"] = list(once.values())
    return out


def list_filters(conn: sa.Connection) -> list[dict[str, Any]]:
    q = sa.select(objects).where(objects.c.kind == KIND).order_by(objects.c.name_key, objects.c.id)
    rows = conn.execute(q).all()
    ids = sorted({i for r in rows for i in fs.object_ids((r.meta or {}).get("rules") or {})})
    names = _existing(conn, ids)
    return [_form(r, names, full=False) for r in rows]


def get(conn: sa.Connection, filter_id: int) -> dict[str, Any]:
    row = _row(conn, filter_id)
    return _form(row, _existing(conn, fs.object_ids((row.meta or {}).get("rules") or {})), full=True)
