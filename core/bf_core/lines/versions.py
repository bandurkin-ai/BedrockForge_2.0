"""Versions of a line layer and their journal (ТЗ 7.22; ADR 0015 §1).

The number of the current version is `line_layers.rev` (not `objects.version`: renaming and colour raise that).
A version is the set of blobs of the layer at that moment: the row of `line_layer_versions` owns them as the
owner `line_version` with the roles of the layer (`xyz`, `offsets`, `closed`, `seq`, `lid`, `attrs`, `vattrs`, or
`pack:<n>`). Saving writes the files of the new state and moves the references of the layer to them; returning to
a version writes no files — the new version owns the blobs of the old one. Nothing is ever erased.

Version 1 is the state as it was imported (`import`), created (`create`) or built (`operation`): its row is
written by the first save, move or return — an import of 2 000 layers writes none; until then the journal shows
one computed row.

`save`, `restore`, `move_lines` take the engine: they read and build outside the write transaction and check the
base inside it (`UPDATE … WHERE rev = :base`); a stale base is a 409 `layer_changed` — the temporary files go,
the database does not change. Ported from BF `lines/edit.py` (versions, return, move) with the decisions of
ADR 0015 (own counter, blobs instead of a JSON copy, `src` and untouched lines from the base version).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import numpy as np
import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import blob_owners, line_layer_versions, line_layers, objects
from bf_core.formats.lineset import LineSet
from bf_core.storage.blobs import BlobStore, add_owners

from . import store
from .assemble import Assembled, Edit, as_lines, assemble, concat, take_lines, untouched
from .measure import layer_measures

ACTIONS = ("import", "create", "save", "restore", "move", "operation")
MAX_NOTE = 255


def _geometry_role(role: str) -> bool:
    return role in store.ARRAYS or role in store.TABLES or role.startswith("pack:")


# --- errors ------------------------------------------------------------------------------------------------


def not_editable() -> ApiError:
    return ApiError(
        422,
        "not_editable",
        "Облако точек не правится в редакторе стрингов.",
        "Постройте по облаку ЦТМ или линии и правьте их.",
    )


def _conflict(conn: sa.Connection, layer_id: int, base: int) -> ApiError:
    """409 for a stale base; 404 when the layer (or its file) was deleted since it was read."""
    rev = conn.execute(sa.select(line_layers.c.rev).where(line_layers.c.object_id == layer_id)).scalar()
    if rev is None:
        return store._not_found()
    row = conn.execute(
        sa.select(line_layer_versions.c.author, line_layer_versions.c.created_at).where(
            line_layer_versions.c.layer_id == layer_id, line_layer_versions.c.version == rev
        )
    ).first()
    author = row.author if row is not None else None
    at = row.created_at.isoformat() if row is not None else None
    who = f", {author}" if author else ""
    return ApiError(
        409,
        "layer_changed",
        f"Слой сохранён после начала правки (версия {rev}{who}). Ничего не записано.",
        "Перечитайте слой или сохраните правку как новый слой.",
        extra={"layer_id": layer_id, "version": int(rev), "base_version": int(base), "author": author, "at": at},
    )


# --- reading -----------------------------------------------------------------------------------------------


def layer_head(conn: sa.Connection, layer_id: int) -> dict[str, Any]:
    """The layer as `store.layer_info` with `rev`, `next_lid` (effective), `meta` and the format of its file."""
    info = store.layer_info(conn, layer_id)
    row = conn.execute(
        sa.select(objects.c.meta, objects.c.updated_at, objects.c.author).where(objects.c.id == layer_id)
    ).one()
    fmt = conn.execute(sa.select(objects.c.meta).where(objects.c.id == info["file_id"])).scalar_one()
    info["meta"] = row.meta or {}
    info["updated_at"] = row.updated_at
    info["author"] = row.author
    info["file_format"] = (fmt or {}).get("format")
    if info["next_lid"] is None:
        info["next_lid"] = int(info["line_count"] if not info["points_only"] else info["point_count"])
    return info


def check_editable(info: Mapping[str, Any]) -> None:
    """A cloud is not edited (ADR 0014); a layer still being built is not either."""
    if info["meta"].get("cloud") is not None:
        raise not_editable()
    if info["state"] != "ready":
        raise ApiError(409, "layer_busy", "Слой линий ещё строится.", "Дождитесь окончания задания.")


def editable_head(conn: sa.Connection, layer_id: int) -> dict[str, Any]:
    info = layer_head(conn, layer_id)
    check_editable(info)
    return info


def read_files(conn: sa.Connection, bs: BlobStore, shas: Mapping[str, str], head: Mapping[str, Any]) -> LineSet:
    """The layer stored in the blobs `shas` (its current ones, or those of a version)."""
    from bf_core.formats.lineset import AttrField

    files = store.layer_files(bs, shas)
    xyz = files.array("xyz")
    if xyz is None or not files.has("seq") or (not head["points_only"] and not files.has("offsets")):
        raise store.broken()
    units = {f.name: f.unit for f in (AttrField.from_dict(d) for d in head["attr_schema"] or []) if f.unit}
    return LineSet(
        head["name"], xyz.reshape(-1, 3), files.array("offsets"), files.array("closed"), files.array("seq"),
        files.table("attrs"), files.table("vattrs"), head["color"], units,
    )  # fmt: skip


def lids_of(bs: BlobStore, shas: Mapping[str, str], lines: int) -> np.ndarray:
    """The stable numbers of the lines: the `lid` blob, or the positions when the layer has none."""
    lid = store.layer_files(bs, shas).array("lid")
    if lid is None:
        return np.arange(lines, dtype=np.uint32)
    if len(lid) != lines:
        raise store.broken()
    return lid


def read_current(conn: sa.Connection, bs: BlobStore, layer_id: int) -> tuple[dict[str, Any], LineSet, np.ndarray]:
    head = layer_head(conn, layer_id)
    shas = {r: s for r, s in store.layer_blobs(conn, layer_id).items() if _geometry_role(r)}
    ls = read_files(conn, bs, shas, head)
    return head, ls, lids_of(bs, shas, len(as_lines(ls)[1]))


def version_blobs(conn: sa.Connection, version_id: int) -> dict[str, str]:
    rows = conn.execute(
        sa.select(blob_owners.c.role, blob_owners.c.sha256).where(
            blob_owners.c.owner_kind == store.VERSION_OWNER, blob_owners.c.owner_id == version_id
        )
    ).all()
    return {str(r.role): str(r.sha256) for r in rows}


def _version_row(conn: sa.Connection, layer_id: int, version: int) -> sa.Row[Any] | None:
    return conn.execute(
        sa.select(line_layer_versions).where(
            line_layer_versions.c.layer_id == layer_id, line_layer_versions.c.version == version
        )
    ).first()


def _first_action(head: Mapping[str, Any]) -> str:
    built = (head["meta"] or {}).get("built")
    if head["file_format"] == "operation" or (isinstance(built, Mapping) and built.get("kind") == "operation"):
        return "operation"
    return "create" if head["file_format"] == "editor" else "import"


def journal(conn: sa.Connection, layer_id: int) -> list[dict[str, Any]]:
    """Rows of the journal, newest first; a layer never saved shows one computed row (version 1)."""
    head = layer_head(conn, layer_id)
    rows = conn.execute(
        sa.select(line_layer_versions)
        .where(line_layer_versions.c.layer_id == layer_id)
        .order_by(line_layer_versions.c.version.desc())
    ).mappings()
    out = [
        {
            "version": int(r["version"]),
            "action": r["action"],
            "note": r["note"] or "",
            "author": r["author"],
            "at": r["created_at"].isoformat(),
            "line_count": r["line_count"],
            "point_count": r["point_count"],
            "prev_line_count": r["prev_line_count"],
            "prev_point_count": r["prev_point_count"],
            "stats": r["stats"],
            "current": int(r["version"]) == int(head["rev"]),
        }  # fmt: skip
        for r in rows
    ]
    if not out:
        out.append(
            {
                "version": int(head["rev"]),
                "action": _first_action(head),
                "note": "",
                "author": head["author"],
                "at": head["updated_at"].isoformat(),
                "line_count": head["line_count"],
                "point_count": head["point_count"],
                "prev_line_count": 0,
                "prev_point_count": 0,
                "stats": None,
                "current": True,
            }  # fmt: skip
        )
    return out


def read_version(
    conn: sa.Connection, bs: BlobStore, layer_id: int, version: int
) -> tuple[LineSet, np.ndarray, dict[str, str]]:
    """The geometry of a version (the current one also when it has no journal row yet)."""
    head = layer_head(conn, layer_id)
    row = _version_row(conn, layer_id, version)
    if row is None:
        if version != head["rev"]:
            raise _no_version(version)
        _h, ls, lid = read_current(conn, bs, layer_id)
        return ls, lid, {r: s for r, s in store.layer_blobs(conn, layer_id).items() if _geometry_role(r)}
    shas = version_blobs(conn, int(row.id))
    st = row.stats or {}
    vhead = {**head, "points_only": st.get("points_only", False), "attr_schema": st.get("attr_schema", [])}
    ls = read_files(conn, bs, shas, vhead)
    return ls, lids_of(bs, shas, len(as_lines(ls)[1])), shas


def _no_version(version: int) -> ApiError:
    return ApiError(404, "version_not_found", f"Версии {version} у слоя нет.", "Обновите журнал версий.")


# --- writing -----------------------------------------------------------------------------------------------


def stats_of(ls: LineSet, next_lid: int) -> dict[str, Any]:
    offsets, closed = (None, None) if ls.points_only else (ls.offsets, ls.closed)
    m = layer_measures(ls.xyz, offsets, closed)
    m.update(
        flat=ls.flat(), extent=ls.extent(), origin=ls.origin(), attr_schema=ls.attr_schema(),
        points_only=ls.points_only, next_lid=int(next_lid), length3d=float(ls.lengths().sum()),
    )  # fmt: skip
    return m


class Prepared:
    """A new state of a layer in temporary files (outside the write transaction)."""

    def __init__(self, bs: BlobStore, built: Assembled) -> None:
        self.built = built
        ls = built.lineset
        self.staged = store.stage_layer(bs, ls)
        self.lid_file = None
        lines = ls.line_count if not ls.points_only else ls.point_count
        if not np.array_equal(built.lid, np.arange(lines)):
            self.lid_file = bs.stage_bytes(np.ascontiguousarray(built.lid, dtype="<u4").tobytes())
        self.stats = stats_of(ls, built.next_lid)

    def discard(self) -> None:
        self.staged.discard()
        if self.lid_file is not None:
            self.lid_file[0].unlink(missing_ok=True)

    def commit(self, conn: sa.Connection, bs: BlobStore, layer_id: int) -> dict[str, str]:
        """The files into place as the blobs of the layer; returns {role: sha}."""
        out: dict[str, str] = {}
        for role, tmp in list(self.staged.files.items()):
            media = store.PARQUET_MEDIA if role in store.TABLES else store.ARRAY_MEDIA
            out[role] = bs.commit_staged(
                conn, tmp, media_type=media, owner_kind=store.OWNER, owner_id=layer_id, role=role
            )
            del self.staged.files[role]
        if self.lid_file is not None:
            out["lid"] = bs.commit_staged(
                conn, self.lid_file, media_type=store.ARRAY_MEDIA, owner_kind=store.OWNER, owner_id=layer_id, role="lid"
            )
            self.lid_file = None
        return out


@contextmanager
def _discarding(*prepared: Prepared) -> Iterator[None]:
    try:
        yield
    finally:
        for p in prepared:
            p.discard()


def _journal_row(
    conn: sa.Connection, layer_id: int, version: int, action: str, stats: Mapping[str, Any], *, note: str = "",
    author: str | None = None, prev: tuple[int, int] = (0, 0), shas: Mapping[str, str],
) -> int:  # fmt: skip
    vid = conn.execute(
        sa.insert(line_layer_versions).values(
            layer_id=layer_id,
            version=version,
            action=action,
            note=(note or None) and note[:MAX_NOTE],
            author=author,
            created_at=dialect.utcnow(),
            line_count=int(stats["lines"]),
            point_count=int(stats["vertices"]),
            prev_line_count=prev[0],
            prev_point_count=prev[1],
            stats=dict(stats),
        )  # fmt: skip
    ).inserted_primary_key[0]  # type: ignore[index]
    add_owners(conn, [(sha, store.VERSION_OWNER, int(vid), role) for role, sha in sorted(shas.items())])
    return int(vid)


def _has_journal(conn: sa.Connection, layer_id: int) -> bool:
    row = conn.execute(
        sa.select(line_layer_versions.c.id).where(line_layer_versions.c.layer_id == layer_id).limit(1)
    ).first()
    return row is not None


def _apply(
    conn: sa.Connection,
    bs: BlobStore,
    head: Mapping[str, Any],
    base: int,
    base_stats: Mapping[str, Any] | None,
    new: Prepared | tuple[Mapping[str, str], Mapping[str, Any]],
    *,
    action: str,
    note: str,
    author: str | None,
) -> int:
    """Inside the write transaction: check the base, keep version 1, move the layer to the new blobs, write the
    journal row N + 1, the counters and the extent. Returns the new version."""
    layer_id = int(head["id"])
    n = conn.execute(
        sa.update(line_layers)
        .where(line_layers.c.object_id == layer_id, line_layers.c.rev == base)
        .values(rev=base + 1)
    ).rowcount
    if n != 1:
        raise _conflict(conn, layer_id, base)
    old = {r: s for r, s in store.layer_blobs(conn, layer_id).items() if _geometry_role(r)}
    if not _has_journal(conn, layer_id):
        assert base_stats is not None, "the first row needs the measures of the base"
        _journal_row(conn, layer_id, base, _first_action(head), base_stats, author=head["author"], shas=old)
    conn.execute(
        sa.delete(blob_owners).where(
            blob_owners.c.owner_kind == store.OWNER,
            blob_owners.c.owner_id == layer_id,
            blob_owners.c.role.in_(list(old)),
        )
    )
    if isinstance(new, Prepared):
        shas, stats = new.commit(conn, bs, layer_id), new.stats
    else:
        shas, stats = dict(new[0]), dict(new[1])
        add_owners(conn, [(sha, store.OWNER, layer_id, role) for role, sha in sorted(shas.items())])
    prev = (int(head["line_count"]), int(head["point_count"]))
    _journal_row(conn, layer_id, base + 1, action, stats, note=note, author=author, prev=prev, shas=shas)
    conn.execute(
        sa.update(line_layers)
        .where(line_layers.c.object_id == layer_id)
        .values(
            line_count=int(stats["lines"]),
            point_count=int(stats["vertices"]),
            length=float(stats["length3d"]),
            closed_count=int(stats["closed"]),
            flat=bool(stats["flat"]),
            attr_schema=stats["attr_schema"],
            points_only=bool(stats["points_only"]),
            origin=stats["origin"],
            next_lid=int(stats["next_lid"]),
        )
    )
    conn.execute(
        sa.update(objects)
        .where(objects.c.id == layer_id)
        .values(version=objects.c.version + 1, extent=stats["extent"], updated_at=dialect.utcnow())
    )
    parent = conn.execute(sa.select(objects.c.extent).where(objects.c.id == head["file_id"])).scalar_one()
    grown = store._union(parent, stats["extent"])
    if grown != parent:
        conn.execute(sa.update(objects).where(objects.c.id == head["file_id"]).values(extent=grown))
    return base + 1


def save(
    engine: sa.Engine,
    bs: BlobStore,
    layer_id: int,
    edit: Edit,
    *,
    base_version: int,
    note: str = "",
    author: str | None = None,
) -> int:
    """A save of the editor: the new version N + 1 (returned). Stale base → 409, bad geometry → 422."""
    with dialect.read_tx(engine) as conn:
        head, ls, lid = read_current(conn, bs, layer_id)
        check_editable(head)
        if int(head["rev"]) != base_version:
            raise _conflict(conn, layer_id, base_version)
        first = not _has_journal(conn, layer_id)  # rows are written only with a new rev: a stale read is a 409
    base_stats = stats_of(ls, head["next_lid"]) if first else None
    prepared = Prepared(bs, assemble(ls, lid, head["next_lid"], edit))
    with _discarding(prepared), dialect.write_tx(engine) as conn:
        return _apply(conn, bs, head, base_version, base_stats, prepared, action="save", note=note, author=author)


def restore(
    engine: sa.Engine, bs: BlobStore, layer_id: int, version: int, *, expected_version: int, author: str | None = None
) -> int:
    """Version N + 1 with the blobs of `version`; no file is written, nothing is erased."""
    with dialect.read_tx(engine) as conn:
        head = editable_head(conn, layer_id)
        if int(head["rev"]) != expected_version:
            raise _conflict(conn, layer_id, expected_version)
        if version == int(head["rev"]):
            raise ApiError(
                422, "version_current", f"Версия {version} — текущая.", "Выберите в журнале более раннюю версию."
            )
        row = _version_row(conn, layer_id, version)
        if row is None:
            raise _no_version(version)
        shas = version_blobs(conn, int(row.id))
    stats = dict(row.stats or {})
    stats["next_lid"] = max(int(stats.get("next_lid") or 0), int(head["next_lid"]))  # numbers are never reused
    with dialect.write_tx(engine) as conn:
        return _apply(
            conn, bs, head, expected_version, None, (shas, stats), action="restore",
            note=f"Возврат к версии {version}", author=author,
        )  # fmt: skip


def move_lines(
    engine: sa.Engine,
    bs: BlobStore,
    source_id: int,
    source_edit: Edit,
    moved: Edit,
    target_id: int,
    *,
    source_base: int,
    target_base: int,
    author: str | None = None,
) -> tuple[int, int]:
    """`lines.move_selected`: the source saved without the lines (`source_edit`), the lines (`moved`: `src` — their
    `lid` in the source; their geometry, touched or not) appended to the target with their attributes. One
    transaction, both bases checked; both layers get a version `move`. Returns (source version, target version)."""
    if source_id == target_id:
        raise ApiError(422, "same_layer", "Линии уже в этом слое.", "Выберите другой слой.")
    with dialect.read_tx(engine) as conn:
        shead, sls, slid = read_current(conn, bs, source_id)
        thead, tls, tlid = read_current(conn, bs, target_id)
        for head, base in ((shead, source_base), (thead, target_base)):
            check_editable(head)
            if int(head["rev"]) != base:
                raise _conflict(conn, int(head["id"]), base)
        s_first, t_first = (not _has_journal(conn, i) for i in (source_id, target_id))
    pos = {int(v): i for i, v in enumerate(slid.tolist())}
    if np.any(moved.src < 0) or any(int(s) not in pos for s in moved.src.tolist()):
        raise ApiError(422, "bad_geometry", "Переносимых линий в слое нет: слой изменился.", "Перечитайте слой.")
    kept = set(moved.src.tolist()) & set(source_edit.src.tolist())
    if kept:
        raise ApiError(
            422, "bad_geometry", f"Линия {min(kept)} осталась в исходном слое: перенос стал бы копированием.",
            "Выберите линии для переноса заново.",
        )  # fmt: skip
    m = len(moved.src)
    first = int(thead["next_lid"])
    virtual = concat(tls, take_lines(sls, [pos[int(s)] for s in moved.src.tolist()]))
    vlid = np.concatenate([tlid.astype(np.int64), np.arange(first, first + m)])
    toff, tclosed = as_lines(tls)
    keep = untouched(tlid, tclosed, np.diff(toff))
    target_edit = Edit(
        np.concatenate([keep.src, np.arange(first, first + m)]),
        np.concatenate([keep.touched, moved.touched]),
        np.concatenate([keep.closed, moved.closed]),
        np.concatenate([keep.count, moved.count]),
        moved.xyz,
        {len(keep.src) + int(k): v for k, v in moved.new_attrs.items()},
    )
    s_stats = stats_of(sls, shead["next_lid"]) if s_first else None
    t_stats = stats_of(tls, thead["next_lid"]) if t_first else None
    sp = Prepared(bs, assemble(sls, slid, shead["next_lid"], source_edit))
    with _discarding(sp):
        tp = Prepared(bs, assemble(virtual, vlid, first + m, target_edit, name=tls.name))
        with _discarding(tp), dialect.write_tx(engine) as conn:
            sv = _apply(
                conn, bs, shead, source_base, s_stats, sp, action="move",
                note=f"Перенос линий ({m}) в слой «{thead['name']}»", author=author,
            )  # fmt: skip
            tv = _apply(
                conn, bs, thead, target_base, t_stats, tp, action="move",
                note=f"Перенос линий ({m}) из слоя «{shead['name']}»", author=author,
            )  # fmt: skip
    return sv, tv


# --- new layer ---------------------------------------------------------------------------------------------


def _free_name(conn: sa.Connection, name: str, where: sa.ColumnElement[bool]) -> str:
    taken = {str(k) for k in conn.execute(sa.select(objects.c.name_key).where(where)).scalars()}
    out, n = name, 2
    while dialect.name_key(out) in taken:
        out = f"{name} ({n})"
        n += 1
    return out


def create_layer(
    conn: sa.Connection,
    bs: BlobStore,
    *,
    name: str,
    file_id: int | None = None,
    color: str | None = None,
    author: str | None = None,
) -> int:
    """`lines.new_layer`: an empty layer in a new file (`meta.format = "editor"`) or in the file given. The name
    is the code of the string in STR; a name taken in the file gets « (2)», « (3)»…"""
    name = " ".join(str(name or "").split())[:200]
    if not name:
        raise ApiError(422, "name_empty", "Название слоя не может быть пустым.", "Введите название слоя.")
    if file_id is None:
        fname = _free_name(conn, name, (objects.c.kind == store.LINE_FILE) & objects.c.parent_id.is_(None))
        file_id = store.create_file(conn, bs, name=fname, meta={"format": "editor"}, author=author)
    else:
        row = conn.execute(
            sa.select(objects.c.state, objects.c.meta).where(objects.c.id == file_id, objects.c.kind == store.LINE_FILE)
        ).first()
        if row is None:
            raise store._not_found("Файл линий")
        if row.state != "ready":
            raise ApiError(409, "file_busy", "Файл линий ещё загружается.", "Дождитесь окончания импорта.")
    lname = _free_name(conn, name, (objects.c.kind == store.LINE_LAYER) & (objects.c.parent_id == file_id))
    sort = conn.execute(
        sa.select(sa.func.max(objects.c.sort_key)).where(
            objects.c.parent_id == file_id, objects.c.kind == store.LINE_LAYER
        )
    ).scalar()
    empty = LineSet(lname, np.zeros((0, 3)), np.zeros(1, dtype=np.int64), np.zeros(0, dtype=np.uint8), color=color)
    return store.put_layer(
        conn, bs, empty, file_id=file_id, sort_key=0.0 if sort is None else float(sort) + 1, author=author
    )


def lines_of(ls: LineSet, lid: np.ndarray, keep: Sequence[int]) -> Edit:
    """The body that keeps only the lines `keep` (lids) of a layer untouched: the source of a move in tests."""
    offsets, closed = as_lines(ls)
    pos = {int(v): i for i, v in enumerate(lid.tolist())}
    idx = np.array([pos[int(k)] for k in keep], dtype=np.int64)
    return Edit(lid[idx].astype(np.int64), np.zeros(len(idx)), closed[idx], np.diff(offsets)[idx], np.zeros((0, 3)))
