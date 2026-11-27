"""`data_tree`, `data_summary`, `data_history`: the tree, the summary and the history of the open project
(подшаг 23.7; e23-api.md §5.2; ТЗ 7.1, 7.3, 7.35 Д-35; ADR 0025).

The counters are `projects.store.summary`, the audit of a set is `audit.service.latest`, the journal of a set is
`drillholes.actions.history`, the layer versions are `lines.versions.journal`, the origin of a computed object is
`lineage.origin` / `lineage.impact`: nothing is counted again here.
"""

from __future__ import annotations

import logging
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect
from bf_core.db.schema import dh_columns, dh_holes, dh_results, dh_surveys, dh_tables, objects, project_meta

from .data_tools import DataCall, DataError, clean, next_offset, offset_of, register, require_set

log = logging.getLogger("bf_core.assistant.data")

TREE_PAGE = 300
EDITS_MAX = 50
VERSIONS_MAX = 20
ORIGIN_MAX = 30
IMPACT_NODES_MAX = 20
FIELDS_MAX = 40
#: kinds of the objects of the project in the order of the tree; the rest follow by their names
KIND_ORDER = (
    "drillhole_set",
    "wireframe",
    "line_file",
    "raster",
    "block_model",
    "implicit_model",
    "variogram_model",
    "trend",
    "estimation",
    "simulation",
    "section_series",
    "chart",
)
HIDDEN_KINDS = ("assistant_dialog",)  # the chats of the assistant are not data of the project
#: the words of a user (and of a model) for the kinds of `data_history`
KIND_ALIASES = {
    "набор": "drillhole_set",
    "набор скважин": "drillhole_set",
    "dh_set": "drillhole_set",
    "set": "drillhole_set",
    "каркас": "wireframe",
    "линия": "line_layer",
    "линии": "line_layer",
    "слой линий": "line_layer",
    "блочная модель": "block_model",
    "растр": "raster",
    "график": "chart",
    "таблица": "dh_table",
    "результат": "dh_result",
}
TABLE_KINDS = ("assay_table", "interval_table", "event_table")

TREE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "offset": {
            "type": "integer",
            "description": "Смещение для следующей страницы (next_offset предыдущего ответа).",
        }
    },
    "required": [],
    "additionalProperties": False,
}
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "set_id": {"type": "integer", "description": "Номер набора скважин; по умолчанию — активный набор."}
    },
    "required": [],
    "additionalProperties": False,
}
HISTORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "description": "Вид объекта: набор, каркас, линия и т. д."},
        "id": {"type": "integer", "description": "Номер объекта."},
    },
    "required": ["kind", "id"],
    "additionalProperties": False,
}


# --- data_tree ----------------------------------


def _folder_chain(folder: str) -> list[str]:
    parts = [p for p in folder.split("/") if p]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _lineage_kind(kind: str, registered: set[str]) -> str | None:
    """The kind of the node of the graph of dependencies for a kind of the tree (None - not in the graph)."""
    if kind == "drillhole_set":
        return "dh_set"
    if kind == "wireframe":
        return "wireframe"
    if kind in TABLE_KINDS:
        return "dh_table"
    return f"object:{kind}" if f"object:{kind}" in registered else None


def _nodes(call: DataCall, conn: sa.Connection) -> list[dict[str, Any]]:
    """Every node of the tree in the order of the panel: folders, objects, the tables of a set under it."""
    rows = conn.execute(
        sa.select(objects.c.id, objects.c.kind, objects.c.name, objects.c.folder, objects.c.parent_id, objects.c.state)
        .where(objects.c.kind.not_in(HIDDEN_KINDS))
        .order_by(objects.c.id)
    ).all()
    holes = {
        int(s): int(n)
        for s, n in conn.execute(sa.select(dh_holes.c.set_id, sa.func.count()).group_by(dh_holes.c.set_id))
    }
    tables: dict[int, list[Any]] = {}
    for t in conn.execute(
        sa.select(dh_tables.c.id, dh_tables.c.set_id, dh_tables.c.name, dh_tables.c.kind, dh_tables.c.row_count)
        .where(dh_tables.c.state == "ready")
        .order_by(dh_tables.c.id)
    ):
        tables.setdefault(int(t.set_id), []).append(t)
    rank = {k: i for i, k in enumerate(KIND_ORDER)}
    children: dict[int, list[Any]] = {}
    top: list[Any] = []
    for r in rows:
        call.check()
        (children.setdefault(int(r.parent_id), []) if r.parent_id is not None else top).append(r)

    def key(r: Any) -> tuple[str, int, str, str]:
        return (dialect.name_key(r.folder or ""), rank.get(r.kind, len(rank)), r.kind, dialect.name_key(r.name))

    top.sort(key=key)
    folders: dict[str, int] = {}
    for r in top:
        for chain in _folder_chain(r.folder or ""):
            folders[chain] = folders.get(chain, 0) + 1
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(r: Any, base: str) -> None:
        call.check()
        path = f"{base}/{r.name}" if base else r.name
        node: dict[str, Any] = {"path": path, "kind": r.kind, "id": int(r.id), "name": r.name}
        if r.kind == "drillhole_set":
            node["count"] = holes.get(int(r.id), 0)
        if r.state != "ready":
            node["state"] = r.state
        out.append(node)
        for child in sorted(children.get(int(r.id), []), key=key):
            add(child, path)
        for t in tables.get(int(r.id), []):
            out.append(
                {
                    "path": f"{path}/{t.name}",
                    "kind": f"{t.kind}_table",
                    "id": int(t.id),
                    "name": t.name,
                    "count": int(t.row_count),
                }  # fmt: skip
            )

    for r in top:
        for chain in _folder_chain(r.folder or ""):
            if chain not in seen:
                seen.add(chain)
                out.append({"path": chain, "kind": "folder", "id": None, "name": chain.rsplit("/", 1)[-1],
                            "count": folders[chain]})  # fmt: skip
        add(r, r.folder or "")
    return out


def _freshness(call: DataCall, conn: sa.Connection, page: list[dict[str, Any]]) -> None:
    """`state` of the nodes of a page that are not current (stale, source lost, pinned): the graph of ADR 0025."""
    try:
        from bf_core import lineage
        from bf_core.storage.blobs import BlobStore

        registered = set(lineage.providers.registered())
        wanted: dict[Any, dict[str, Any]] = {}
        for n in page:
            lk = _lineage_kind(n["kind"], registered)
            if lk and n.get("id") is not None:
                wanted[lineage.Node(lk, int(n["id"]))] = n
        if not wanted:
            return
        got = lineage.status(conn, list(wanted), store=BlobStore(call.project.folder))
        for node, n in wanted.items():
            st = got.get(node)
            if st is not None and st.state != lineage.CURRENT and "state" not in n:
                n["state"] = st.state
    except Exception:  # noqa: BLE001 - the freshness is a convenience: the tree is answered without it
        log.warning("data_tree: lineage status failed", exc_info=True)


def tree(call: DataCall, args: dict[str, Any]) -> dict[str, Any]:
    offset = offset_of(args)
    with dialect.read_tx(call.project.engine) as conn:
        nodes = _nodes(call, conn)
        page = nodes[offset : offset + TREE_PAGE]
        _freshness(call, conn, page)
    total = len(nodes)
    call.summary = f"Объектов в дереве: {total}"
    return {"nodes": page, "total": total, "next_offset": next_offset(offset, len(page), total)}


# --- data_summary ----------------------------------


def _union(boxes: list[Any]) -> list[float | None] | None:
    """The box [xmin, ymin, zmin, xmax, ymax, zmax] around boxes whose numbers may be missing (a raster has no z)."""
    lo: list[float | None] = [None, None, None]
    hi: list[float | None] = [None, None, None]
    found = False
    for b in boxes:
        if not b or len(b) != 6:
            continue
        found = True
        for i in range(3):
            if b[i] is not None and (lo[i] is None or b[i] < lo[i]):  # type: ignore[operator]
                lo[i] = b[i]
            if b[i + 3] is not None and (hi[i] is None or b[i + 3] > hi[i]):  # type: ignore[operator]
                hi[i] = b[i + 3]
    return [*lo, *hi] if found else None


def _set_extent(conn: sa.Connection, set_id: int) -> list[float | None] | None:
    h = dh_holes
    r = conn.execute(
        sa.select(
            sa.func.min(h.c.x), sa.func.min(h.c.y), sa.func.min(h.c.z),
            sa.func.max(h.c.x), sa.func.max(h.c.y), sa.func.max(h.c.z),
        ).where(h.c.set_id == set_id, h.c.has_collar.is_(True))
    ).first()  # fmt: skip
    return None if r is None or r[0] is None else [*r]


def _audit(conn: sa.Connection, set_id: int) -> dict[str, Any] | None:
    from bf_core.audit.service import latest

    last = latest(conn, set_id)
    if last is None:
        return None
    t = last["totals"] or {}
    return {"e": t.get("error", 0), "w": t.get("warning", 0), "i": t.get("info", 0), "stale": bool(last["stale"])}


def _project_summary(call: DataCall, conn: sa.Connection) -> dict[str, Any]:
    from bf_core.projects.store import summary

    meta = conn.execute(sa.select(project_meta.c.name, project_meta.c.crs, project_meta.c.description)).one()
    counts = summary(conn)
    counts.pop("chats", None)  # the dialogs are not data of the project
    sets = conn.execute(
        sa.select(objects.c.id, objects.c.name, objects.c.extent)
        .where(objects.c.kind == "drillhole_set", objects.c.state == "ready")
        .order_by(objects.c.id)
    ).all()
    per_set = {
        int(s): int(n)
        for s, n in conn.execute(sa.select(dh_holes.c.set_id, sa.func.count()).group_by(dh_holes.c.set_id))
    }
    boxes = [r.extent for r in conn.execute(sa.select(objects.c.extent).where(objects.c.state == "ready"))]
    for s in sets:
        call.check()
        boxes.append(_set_extent(conn, int(s.id)))
    return {
        "name": meta.name,
        "description": {k: v for k, v in (meta.description or {}).items() if v},
        "crs": meta.crs,
        "counts": counts,
        "extent": _union(boxes),
        "sets": [{"id": int(s.id), "name": s.name, "holes": per_set.get(int(s.id), 0)} for s in sets],
    }


def _set_summary(call: DataCall, conn: sa.Connection, set_id: int) -> dict[str, Any]:
    s = require_set(conn, set_id)
    h = dh_holes
    n_holes, n_collars, metres, no_depth = conn.execute(
        sa.select(
            sa.func.count(),
            sa.func.sum(sa.case((h.c.has_collar.is_(True), 1), else_=0)),
            sa.func.sum(h.c.td),
            sa.func.sum(sa.case((h.c.td.is_(None), 1), else_=0)),
        ).where(h.c.set_id == set_id)
    ).one()
    n_surveys = int(conn.execute(sa.select(sa.func.count()).where(dh_surveys.c.set_id == set_id)).scalar_one())
    tabs: list[dict[str, Any]] = [
        {"name": "Устья", "kind": "collar", "rows": int(n_collars or 0),
         "fields": ["hole", "x", "y", "z", "td", "azimuth", "dip"]},
        {"name": "Инклинометрия", "kind": "survey", "rows": n_surveys, "fields": ["hole", "depth", "azimuth", "dip"]},
    ]  # fmt: skip
    cols: dict[int, list[Any]] = {}
    components: dict[tuple[str, str | None], None] = {}
    for c in conn.execute(
        sa.select(dh_columns.c.table_id, dh_columns.c.name, dh_columns.c.unit, dh_columns.c.role, dh_columns.c.kind)
        .join(dh_tables, dh_tables.c.id == dh_columns.c.table_id)
        .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready", dh_columns.c.deleted.is_(False))
        .order_by(dh_columns.c.table_id, dh_columns.c.position, dh_columns.c.id)
    ):
        call.check()
        cols.setdefault(int(c.table_id), []).append(c)
        if c.role == "component":
            components[(c.name, c.unit)] = None
    kind_order = sa.case((dh_tables.c.kind == "assay", 0), (dh_tables.c.kind == "interval", 1), else_=2)
    for t in conn.execute(
        sa.select(dh_tables.c.id, dh_tables.c.name, dh_tables.c.kind, dh_tables.c.row_count)
        .where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
        .order_by(kind_order, dh_tables.c.id)
    ):
        call.check()
        names = [c.name for c in cols.get(int(t.id), [])]
        tabs.append({"name": t.name, "kind": t.kind, "rows": int(t.row_count), "fields": names[:FIELDS_MAX]})
    results = int(conn.execute(sa.select(sa.func.count()).where(dh_results.c.set_id == set_id)).scalar_one())
    return {
        "id": set_id,
        "name": s.name,
        "crs": s.crs,
        "holes": int(n_holes),
        "metres": None if metres is None else float(metres),  # the sum of the known depths only ...
        "holes_without_depth": int(no_depth or 0),  # ... these holes have none (a gap is not 0 m)
        "tables": tabs,
        "components": [{"name": n, "unit": u} for n, u in components],
        "processing_results": results,
        "extent": _set_extent(conn, set_id),
        "audit": _audit(conn, set_id),
    }


def summary_of(call: DataCall, args: dict[str, Any]) -> dict[str, Any]:
    """Without `set_id` - the project (its sets with their numbers), with it - that set."""
    with dialect.read_tx(call.project.engine) as conn:
        if args.get("set_id") is None:
            out = _project_summary(call, conn)
            call.summary = f"Проект «{clean(out['name'], 80)}»: наборов {len(out['sets'])}"
            return out
        out = _set_summary(call, conn, int(args["set_id"]))
    call.summary = f"Набор «{clean(out['name'], 80)}»: скважин {out['holes']}"
    return out


# --- data_history ----------------------------------


def _object_row(conn: sa.Connection, kind: str, ident: int) -> dict[str, Any]:
    """`(kind, id, name, created_at, updated_at, set_id)` of the object; a text for the model if there is none."""
    if kind == "dh_table":
        q = sa.select(dh_tables.c.name, dh_tables.c.created_at, dh_tables.c.updated_at, dh_tables.c.set_id)
        r = conn.execute(q.where(dh_tables.c.id == ident)).first()
    elif kind == "dh_result":
        q = sa.select(dh_results.c.name, dh_results.c.created_at, dh_results.c.created_at, dh_results.c.set_id)
        r = conn.execute(q.where(dh_results.c.id == ident)).first()
    else:
        q = sa.select(objects.c.name, objects.c.created_at, objects.c.updated_at, objects.c.id)
        r = conn.execute(q.where(objects.c.kind == kind, objects.c.id == ident)).first()
    if r is None:
        raise DataError(f"Объект «{clean(kind, 40)}» с номером {ident} не найден: номера даёт data_tree.")
    return {"kind": kind, "id": ident, "name": r.name, "created_at": r[1].isoformat(), "updated_at": r[2].isoformat(),
            "set_id": int(r[3]) if kind in ("dh_table", "dh_result") else None}  # fmt: skip


def _lineage_node(kind: str, ident: int) -> Any:
    from bf_core import lineage

    if kind in ("dh_table", "dh_result"):
        return lineage.Node(kind, ident)
    return lineage.Node({"drillhole_set": "dh_set", "wireframe": "wireframe"}.get(kind, f"object:{kind}"), ident)


def _edit_summary(e: dict[str, Any], tables: dict[str, str]) -> str:
    table = tables.get(str(e.get("table")), e.get("table"))
    fields = ", ".join(sorted(e["changes"])[:6]) if isinstance(e.get("changes"), dict) else ""
    parts = [f"таблица {table}" if table else "", f"скважина {e['hole']}" if e.get("hole") else "", fields]
    text = ", ".join(p for p in parts if p) or "правка"
    return text + (" (отменена)" if e.get("undone") else "")


def _edits(call: DataCall, conn: sa.Connection, kind: str, ident: int, set_id: int | None) -> list[dict[str, Any]]:
    from bf_core.drillholes import actions

    if kind == "drillhole_set":
        sid, table = ident, None
    elif kind == "dh_table" and set_id is not None:
        sid, table = set_id, str(ident)
    else:
        return []
    names = {str(t.id): t.name for t in conn.execute(sa.select(dh_tables.c.id, dh_tables.c.name))}
    call.check()
    rows = actions.history(call.project, sid, table=table, limit=EDITS_MAX)
    return [{"at": e["at"], "action": e["action"], "summary": _edit_summary(e, names)} for e in rows]


def _versions(conn: sa.Connection, kind: str, ident: int) -> list[dict[str, Any]]:
    if kind != "line_layer":
        return []
    from bf_core.lines import versions

    return [
        {
            "version": v["version"],
            "action": v["action"],
            "note": v["note"],
            "at": v["at"],
            "current": v["current"],
            "lines": v["line_count"],
            "points": v["point_count"],
        }  # fmt: skip
        for v in versions.journal(conn, ident)[:VERSIONS_MAX]
    ]


def history(call: DataCall, args: dict[str, Any]) -> dict[str, Any]:
    from bf_core import lineage
    from bf_core.storage.blobs import BlobStore

    raw = str(args["kind"]).strip()
    kind = KIND_ALIASES.get(raw.casefold(), raw)
    ident = int(args["id"])
    with dialect.read_tx(call.project.engine) as conn:
        obj = _object_row(conn, kind, ident)
        set_id = obj.pop("set_id")
        node = _lineage_node(kind, ident)
        store = BlobStore(call.project.folder)
        state = lineage.status(conn, [node], store=store)[node]
        origin = [
            {"kind": o["kind"], "id": o["id"], "name": o["name"], "state": o["state"], "reason": o["reason"]}
            for o in lineage.origin(conn, node, store=store)[:ORIGIN_MAX]
        ]
        used = lineage.impact(conn, node)
        edits = _edits(call, conn, kind, ident, set_id)
        versions = _versions(conn, kind, ident)
    call.summary = f"{clean(obj['name'], 80)}: состояние {state.state}, правок {len(edits)}"
    return {
        "object": {**obj, "state": state.state, "reason": state.reason},
        "origin": origin,
        "used_by": {
            "total": used["total"],
            "by_kind": used["by_kind"],
            "nodes": [
                {"kind": n["kind"], "id": n["id"], "name": n["name"], "depth": n["depth"]}
                for n in used["nodes"][:IMPACT_NODES_MAX]
            ],
        },
        "edits": edits,
        "versions": versions,
    }


register(
    "data_tree",
    TREE_SCHEMA,
    tree,
    title="Дерево проекта",
    description=(
        "Дерево открытого проекта: папки, наборы скважин с их таблицами (пробы, интервальные таблицы, события), "
        "каркасы, ЦТМ, линии, растры, блочные модели, графики, отчёты. У каждого узла — путь, вид, номер, имя; "
        "state — если объект строится, устарел или потерял источник. Не более 300 узлов за вызов: "
        "next_offset подставьте в offset."
    ),
)
register(
    "data_summary",
    SUMMARY_SCHEMA,
    summary_of,
    title="Сводка проекта / набора",
    description=(
        "Без set_id — сводка проекта: название, система координат, охват, счётчики (наборы, скважины, пробы, "
        "каркасы…) и список наборов с номерами. С set_id — сводка набора скважин: число скважин и метры, таблицы "
        "с полями и числом строк, компоненты, охват, итог аудита (e/w/i и признак «устарел»)."
    ),
)
register(
    "data_history",
    HISTORY_SCHEMA,
    history,
    title="История объекта",
    description=(
        "История объекта проекта по графу зависимостей: из чего он построен (origin) и его состояние (current, "
        "stale, source_lost, pinned), что построено из него (used_by), правки набора или таблицы (edits, не более "
        "50) и версии слоя линий. kind — вид (набор, каркас, линия, блочная модель…, либо drillhole_set, wireframe, "
        "line_layer, block_model, dh_table), id — номер из data_tree."
    ),
)
