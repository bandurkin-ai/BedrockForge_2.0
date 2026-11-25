"""Stored trends (ТЗ 7.35 Д-15; ADR 0022 §10, §12; подшаг 16.16): `objects(kind="trend", parent_id=NULL)`, names
unique among the trends (`name_key`), no files.

`meta`: {v: 1, kind: "global", ang1, ang2, ang3, ratios: [1, r2, r3], size, centre: [x, y, z] | null, color,
origin: {type: "manual" | "model" | "ellipsoid", model_id?, structure?}}. A trend of a model is a copy (вопрос № 56):
deleting or editing the model leaves the trend as it is; on reading, `origin.model_id` of a model gone is null
(«Обновить из модели» unavailable). The model is not an `object_refs` target of the trend — that would forbid
deleting the model (`ON DELETE RESTRICT`).

A structural trend (Э19, ADR 0026 решение 27, `trend_struct.py`): {v: 1, kind: "structural", source: {type:
"measurements", set_id, table (id as text), alpha, dipdir, depth?} | {type: "mesh", wireframe_id, r_n?}, radius
(R_тр, m), ratios, size, color, centre: null, origin: {type: "manual"}}; alpha, dipdir, depth are names of the
fields of the table — the trend reads the data when it is used (it is not a copy).

«Где используется» — the rows `object_refs(dst_id = trend)` of the consumers (the estimation wizard of Э18, RBF of
Э19: role `trend`); a trend with such rows is not deleted — 409 `in_use {usage}`. The `.bfggis` import renumbers
`origin.model_id` (`geostat/remap.py`); the references are rows of `object_refs` and come with the archive.

`get` is the form for the estimation of 8.8 and refuses a structural trend (422 `trend_structural`, вопрос № 99);
`form` (the routes) gives a trend of any kind; `meta_of` — its stored `meta` for RBF (Э19).

Errors: 404 `trend_not_found`; 409 `name_taken` {param: "name"}, `in_use` {usage}, `version_conflict` {version};
422 `bad_param` {param}, `trend_structural`; the model of `model_id` — 404 `model_not_found`; a structural source —
404 `set_not_found`, `table_not_found`, `wireframe_not_found`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, object_refs, objects

from .variogram import store as ms

if TYPE_CHECKING:
    from . import trend as tr  # numpy: imported in the functions (nothing heavy before BF-READY)

KIND = "trend"
ROLE = "trend"  # the role of `object_refs` of a consumer (Э18, Э19)
ORIGINS = ("manual", "model", "ellipsoid")
DEFAULT_NAME = "Тренд"
STRUCT_NAME = "Структурный тренд"
DEFAULT_SIZE = 100.0  # m: the major axis shown of a trend made by hand
DEFAULT_COLOR = "#f46a1a"  # Copper Orange (docs/brand, ADR 0006): the trends apart from the models (Cobalt)
FIELDS = ("name", "kind", "ang1", "ang2", "ang3", "ratios", "size", "centre", "color", "model_id", "structure",
          "origin")  # fmt: skip


def not_found() -> ApiError:
    return ApiError(404, "trend_not_found", "Тренд не найден.", "Обновите список трендов.")


# --- names ------------------------------------------------------------------------------------------------------------


def _taken(conn: sa.Connection, name: str, but: int | None = None) -> bool:
    q = sa.select(objects.c.id).where(objects.c.kind == KIND, objects.c.name_key == dialect.name_key(name))
    if but is not None:
        q = q.where(objects.c.id != but)
    return conn.execute(q).first() is not None


def clean_name(name: Any) -> str:
    text = " ".join(str(name or "").split())[: ms.MAX_NAME].rstrip()
    if not text:
        raise ApiError(422, "name_empty", "Название не может быть пустым.", "Введите название тренда.",
                       {"param": "name"})  # fmt: skip
    return text


def free_name(conn: sa.Connection, name: str) -> str:
    candidate, n = name, 1
    while _taken(conn, candidate):
        n += 1
        tail = f" ({n})"
        candidate = name[: ms.MAX_NAME - len(tail)].rstrip() + tail
    return candidate


def _final_name(conn: sa.Connection, name: Any, default: str, but: int | None = None) -> str:
    if name is None or (isinstance(name, str) and not name.strip()):
        return free_name(conn, clean_name(default))
    final = clean_name(name)
    if _taken(conn, final, but):
        raise ApiError(409, "name_taken", f"Тренд «{final}» уже есть.", "Введите другое название.", {"param": "name"})
    return final


def clean_color(color: Any) -> str:
    if color is None or color == "":
        return DEFAULT_COLOR
    try:
        return ms.clean_color(color)
    except ApiError as e:
        raise ApiError(422, "bad_param", "Цвет тренда задан неверно.", "Ожидается цвет вида #f46a1a.",
                       {"param": "color"}) from e  # fmt: skip


def _origin_given(raw: Any) -> dict[str, Any] | None:
    """`origin` of a request without `model_id`: `{type: "manual" | "ellipsoid"}` (a model — only by `model_id`)."""
    if raw is None:
        return None
    kind = raw.get("type") if isinstance(raw, Mapping) else None
    if kind not in ("manual", "ellipsoid"):
        raise ApiError(422, "bad_param", "Происхождение тренда задано неверно.",
                       "Тренд из модели создаётся по номеру модели (model_id).", {"param": "origin"})  # fmt: skip
    return {"type": kind}


# --- the shape of a request -------------------------------------------------------------------------------------------


def _of_model(conn: sa.Connection, model_id: Any, structure: Any) -> tuple[tr.Shape, float, dict[str, Any], str]:
    from . import trend as tr

    if isinstance(model_id, bool) or not isinstance(model_id, int):
        raise ApiError(422, "bad_param", "Номер модели задан неверно.", "Выберите модель вариограммы из списка.",
                       {"param": "model_id"})  # fmt: skip
    name, meta = ms.meta_of(conn, model_id)  # 404 model_not_found
    structures = [s for s in meta.get("structures") or [] if isinstance(s, Mapping)]
    n = tr.pick_structure(structures, structure)
    s, size = tr.from_structure(structures[n - 1])
    return s, size, {"type": "model", "model_id": model_id, "structure": n}, name


def _fields(conn: sa.Connection, raw: Mapping[str, Any], old: Mapping[str, Any] | None) -> tuple[dict[str, Any], str]:
    """The `meta` of a trend of a request (over `old` of a `PUT`) and the default name."""
    from . import trend as tr

    base = dict(old or {})
    kind = tr.check_kind(raw.get("kind", base.get("kind")))
    if kind != base.get("kind", tr.GLOBAL):
        base = {}  # another kind: nothing of the old one is kept
    if kind == tr.STRUCTURAL:
        return _structural_fields(conn, raw, base)
    default = DEFAULT_NAME
    if raw.get("model_id") is not None:
        s, size, origin, model_name = _of_model(conn, raw["model_id"], raw.get("structure"))
        default = f"{DEFAULT_NAME} · {model_name}"
        size = tr.size_of(raw.get("size"), size)
    else:
        if raw.get("structure") is not None:
            raise ApiError(422, "bad_param", "Номер структуры задаётся вместе с моделью.",
                           "Выберите модель вариограммы или очистите номер структуры.",
                           {"param": "structure"})  # fmt: skip
        zero = {"ang2": 0.0, "ang3": 0.0}
        pick = {k: raw[k] if k in raw else base.get(k, zero.get(k)) for k in ("ang1", "ang2", "ang3", "ratios")}
        s = tr.shape(pick["ang1"], pick["ang2"], pick["ang3"], pick["ratios"])
        size = tr.size_of(raw.get("size"), float(base.get("size") or DEFAULT_SIZE))
        origin = _origin_given(raw.get("origin")) or dict(base.get("origin") or {"type": "manual"})
    centre = tr.centre_of(raw["centre"]) if "centre" in raw else base.get("centre")
    color = clean_color(raw["color"]) if "color" in raw else base.get("color") or DEFAULT_COLOR
    meta = {"v": 1, "kind": tr.GLOBAL, **s.as_dict(), "size": size, "centre": centre, "color": color,
            "origin": origin}  # fmt: skip
    return meta, default


# --- a structural trend: the source ------------------------------------------------------------------------------


def _bad(param: str, message: str, hint: str, status: int = 422, code: str = "bad_param") -> ApiError:
    return ApiError(status, code, message, hint, {"param": param})


def _text(raw: Mapping[str, Any], key: str, label: str, hint: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _bad(f"source.{key}", f"{label}: поле не выбрано.", hint)
    return value.strip()


def _field(infos: list[Any], name: str, key: str, label: str) -> str:
    """The stored name of a numeric field of the table (the case aside); 422 `bad_param` {param: "source.<key>"}."""
    hit = next((f for f in infos if f.name.casefold() == name.casefold() and f.role != "length"), None)
    if hit is None:
        raise _bad(f"source.{key}", f"{label}: в таблице нет поля «{name[:80]}».", "Выберите поле из списка колонок.")
    if hit.kind != "number":
        raise _bad(f"source.{key}", f"{label}: поле «{hit.name}» не числовое.", "Выберите числовое поле.")
    return str(hit.name)


def _measurements_source(conn: sa.Connection, raw: Mapping[str, Any]) -> dict[str, Any]:
    from bf_core.stats import source as sc

    set_id = raw.get("set_id")
    if isinstance(set_id, bool) or not isinstance(set_id, int):
        raise _bad("source.set_id", "Набор скважин с замерами не выбран.", "Выберите набор и таблицу замеров.")
    if raw.get("table") in (None, ""):
        raise _bad("source.table", "Таблица замеров не выбрана.", "Выберите таблицу набора с замерами.")
    table = sc.find_table(conn, set_id, raw["table"])  # 404 set_not_found | table_not_found
    infos = sc.fields_of(conn, table)
    out: dict[str, Any] = {
        "type": "measurements", "set_id": set_id, "table": str(table.id),
        "alpha": _field(infos, _text(raw, "alpha", "Угол падения α", "Выберите поле угла падения."), "alpha",
                        "Угол падения α"),
        "dipdir": _field(infos, _text(raw, "dipdir", "Азимут падения Aп", "Выберите поле азимута падения."),
                         "dipdir", "Азимут падения Aп"),
    }  # fmt: skip
    if raw.get("depth") not in (None, ""):  # without it — the middle of FROM–TO
        out["depth"] = _field(infos, str(raw["depth"]).strip(), "depth", "Глубина замера")
    return out


def _mesh_source(conn: sa.Connection, raw: Mapping[str, Any]) -> dict[str, Any]:
    from bf_core.db.schema import wireframes

    from . import trend_struct as st

    wid = raw.get("wireframe_id")
    if isinstance(wid, bool) or not isinstance(wid, int):
        raise _bad("source.wireframe_id", "Опорный каркас не выбран.", "Выберите каркас или поверхность.")
    row = conn.execute(
        sa.select(objects.c.id).join(wireframes, wireframes.c.object_id == objects.c.id)
        .where(objects.c.id == wid, objects.c.kind == "wireframe", objects.c.state == "ready",
               wireframes.c.triangle_count > 0)
    ).first()  # fmt: skip
    if row is None:
        raise _bad(
            "source.wireframe_id",
            "Каркас не найден в проекте или пуст.",
            "Обновите список каркасов и выберите его снова.",
            404,
            "wireframe_not_found",
        )
    out: dict[str, Any] = {"type": "mesh", "wireframe_id": wid}
    r_n = st.r_n_of(raw.get("r_n"))
    if r_n > 0.0:
        out["r_n"] = r_n
    return out


def _struct_source(conn: sa.Connection, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or raw.get("type") not in ("measurements", "mesh"):
        raise _bad(
            "source", "Источник структурного тренда не задан.", "Выберите замеры (таблицу с α и Aп) или опорный каркас."
        )
    return _measurements_source(conn, raw) if raw["type"] == "measurements" else _mesh_source(conn, raw)


def _structural_fields(conn: sa.Connection, raw: Mapping[str, Any],
                       base: Mapping[str, Any]) -> tuple[dict[str, Any], str]:  # fmt: skip
    from . import trend as tr
    from . import trend_struct as st

    if raw.get("model_id") is not None or raw.get("structure") is not None:
        raise _bad(
            "model_id",
            "Структурный тренд не берётся из модели вариограммы.",
            "Выберите замеры или опорный каркас, либо создайте глобальный тренд из модели.",
        )
    source = _struct_source(conn, raw["source"] if "source" in raw else base.get("source"))
    pick = {k: raw[k] if k in raw else base.get(k) for k in ("radius", "ratios")}
    meta = {"v": 1, "kind": tr.STRUCTURAL, "source": source, "radius": st.radius_of(pick["radius"]),
            "ratios": list(tr.ratios_of(pick["ratios"])),
            "size": tr.size_of(raw.get("size"), float(base.get("size") or DEFAULT_SIZE)), "centre": None,
            "color": clean_color(raw["color"]) if "color" in raw else base.get("color") or DEFAULT_COLOR,
            "origin": {"type": "manual"}}  # fmt: skip
    return meta, STRUCT_NAME


def _shape(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except ValueError as e:
        raise ms.as_api(e) from e


# --- objects ----------------------------------------------------------------------------------------------------------


def _row(conn: sa.Connection, trend_id: int) -> Any:
    r = conn.execute(sa.select(objects).where(objects.c.id == trend_id, objects.c.kind == KIND)).first()
    if r is None:
        raise not_found()
    return r


def create(conn: sa.Connection, raw: Mapping[str, Any], author: str | None = None) -> dict[str, Any]:
    """A trend (in the caller's write transaction): `{id, name}`."""
    meta, default = _shape(_fields, conn, raw, None)
    final = _final_name(conn, raw.get("name"), default)
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(objects).values(
            kind=KIND, name=final, name_key=dialect.name_key(final), parent_id=None, sort_key=0.0, state="ready",
            version=1, meta=meta, author=author, created_at=now, updated_at=now,
        )
    )  # fmt: skip
    new = int(res.inserted_primary_key[0])  # type: ignore[index]
    _sync(conn, new, meta)
    return {"id": new, "name": final}


def _sync(conn: sa.Connection, trend_id: int, meta: Mapping[str, Any]) -> None:
    """A structural trend reads its data when it is used: the graph of dependencies (ADR 0025) gets its inputs — the
    table of the measurements, its set, the wireframe (`lineage/adapt_objects.py`)."""
    if meta.get("kind") == "structural":
        from bf_core import lineage

        lineage.sync(conn, [lineage.Node("object:" + KIND, trend_id)])


def _conflict(version: int) -> ApiError:
    return ms.conflict(version, "Тренд изменён в другом окне.", "Перечитайте тренд или сохраните свой как новый.")


def update(conn: sa.Connection, trend_id: int, raw: Mapping[str, Any]) -> dict[str, Any]:
    """The fields given replace those stored; `model_id` copies the model again («Обновить из модели»).
    `expected_version` (required) — the version the window read; another one — 409 `version_conflict` {version}."""
    row = _row(conn, trend_id)
    version = ms.clean_version(raw.get("expected_version"))
    if int(row.version) != version:
        raise _conflict(int(row.version))
    meta, _default = _shape(_fields, conn, raw, row.meta or {})
    final = str(row.name) if raw.get("name") is None else _final_name(conn, raw["name"], DEFAULT_NAME, trend_id)
    n = conn.execute(
        sa.update(objects).where(objects.c.id == trend_id, objects.c.version == version).values(
            name=final, name_key=dialect.name_key(final), meta=meta, version=version + 1,
            updated_at=dialect.utcnow(),
        )
    ).rowcount  # fmt: skip
    if n != 1:
        raise _conflict(int(_row(conn, trend_id).version))
    _sync(conn, trend_id, meta)
    return {"id": trend_id, "name": final, "version": version + 1}


def usage(conn: sa.Connection, trend_id: int) -> list[dict[str, Any]]:
    """The objects that refer to the trend: `[{id, kind, name, role}]` (every role: each one forbids deleting)."""
    _row(conn, trend_id)
    rows = conn.execute(
        sa.select(objects.c.id, objects.c.kind, objects.c.name, object_refs.c.role)
        .join(objects, objects.c.id == object_refs.c.src_id)
        .where(object_refs.c.dst_id == trend_id)
        .order_by(objects.c.name_key, objects.c.id, object_refs.c.role)
    ).all()
    return [{"id": int(r.id), "kind": r.kind, "name": r.name, "role": r.role} for r in rows]


def delete(conn: sa.Connection, trend_id: int) -> None:
    used = usage(conn, trend_id)
    if used:
        names = ", ".join(f"«{u['name']}»" for u in used[:5]) + (" и другие" if len(used) > 5 else "")
        raise ApiError(409, "in_use", f"Тренд используется: {names}.",
                       "Выберите другой тренд в этих объектах или удалите их, затем удалите тренд.",
                       {"usage": used})  # fmt: skip
    conn.execute(sa.delete(objects).where(objects.c.id == trend_id))
    from bf_core import lineage

    lineage.forget_object(conn, KIND, trend_id)


# --- reading ----------------------------------------------------------------------------------------------------------


def _models(conn: sa.Connection) -> set[int]:
    return {int(i) for i in conn.execute(sa.select(objects.c.id).where(objects.c.kind == ms.KIND)).scalars()}


def _used(conn: sa.Connection) -> set[int]:
    q = sa.select(object_refs.c.dst_id).join(objects, objects.c.id == object_refs.c.dst_id)
    return {int(i) for i in conn.execute(q.where(objects.c.kind == KIND)).scalars()}


def _struct_info(conn: sa.Connection, src: Mapping[str, Any]) -> dict[str, Any]:
    """The names the window shows for the source of a structural trend, and whether it is still there."""
    info: dict[str, Any] = {"set_name": None, "table_name": None, "wireframe_name": None, "lost": True}
    if src.get("type") == "mesh":
        r = conn.execute(sa.select(objects.c.name).where(objects.c.id == src.get("wireframe_id"),
                                                         objects.c.kind == "wireframe")).first()  # fmt: skip
        if r is not None:
            info.update(wireframe_name=r.name, lost=False)
        return info
    r = conn.execute(sa.select(objects.c.name).where(objects.c.id == src.get("set_id"),
                                                     objects.c.kind == "drillhole_set")).first()  # fmt: skip
    t = conn.execute(sa.select(dh_tables.c.name).where(dh_tables.c.id == _int(src.get("table")),
                                                       dh_tables.c.set_id == src.get("set_id"),
                                                       dh_tables.c.state == "ready")).first()  # fmt: skip
    if r is not None and t is not None:
        info.update(set_name=r.name, table_name=t.name, lost=False)
    return info


def _int(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else int(value)
    except (TypeError, ValueError):
        return None


def _form(row: Any, models: set[int], used: set[int], conn: sa.Connection | None = None) -> dict[str, Any]:
    from . import trend as tr

    meta = row.meta or {}
    if meta.get("kind") == tr.STRUCTURAL:
        src = dict(meta.get("source") or {})
        info = _struct_info(conn, src) if conn is not None else {}
        return {"id": int(row.id), "name": row.name, "kind": tr.STRUCTURAL, "source": src,
                "source_info": info, "source_lost": bool(info.get("lost", False)),
                "radius": meta.get("radius"), "ratios": list(meta.get("ratios") or [1.0, 1.0, 1.0]),
                "size": meta.get("size", DEFAULT_SIZE), "centre": None, "color": meta.get("color", DEFAULT_COLOR),
                "origin": {"type": "manual"}, "used": int(row.id) in used, "matrix": None,
                "version": int(row.version), "created_at": row.created_at,
                "updated_at": row.updated_at}  # fmt: skip
    s = tr.shape(meta.get("ang1"), meta.get("ang2", 0.0), meta.get("ang3", 0.0), meta.get("ratios"))
    origin = dict(meta.get("origin") or {"type": "manual"})
    if origin.get("type") == "model" and origin.get("model_id") not in models:
        origin["model_id"] = None  # the model is gone: the trend stays, «Обновить из модели» unavailable
    return {"id": int(row.id), "name": row.name, "kind": meta.get("kind", tr.GLOBAL), **s.as_dict(),
            "size": meta.get("size", DEFAULT_SIZE), "centre": meta.get("centre"),
            "color": meta.get("color", DEFAULT_COLOR), "origin": origin, "used": int(row.id) in used,
            "matrix": tr.matrix(s).tolist(), "version": int(row.version), "created_at": row.created_at,
            "updated_at": row.updated_at}  # fmt: skip


def list_trends(conn: sa.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(sa.select(objects).where(objects.c.kind == KIND).order_by(objects.c.name_key, objects.c.id))
    models, used = _models(conn), _used(conn)
    return [_form(r, models, used, conn) for r in rows]


def form(conn: sa.Connection, trend_id: int) -> dict[str, Any]:
    """A trend of any kind (the routes): global — with its angles and `matrix`, structural — with its `source`,
    `source_info` {set_name, table_name, wireframe_name, lost} and `radius` (`matrix` null)."""
    return _form(_row(conn, trend_id), _models(conn), _used(conn), conn)


def get(conn: sa.Connection, trend_id: int) -> dict[str, Any]:
    """The form of a global trend for the estimation of 8.8 (it reads `ang1`…, `ratios`, `size`); a structural one —
    422 `trend_structural` {param: "trend_id"} (вопрос № 99: its axes differ from point to point)."""
    out = form(conn, trend_id)
    if out["kind"] != "global":
        raise ApiError(422, "trend_structural", "Структурный тренд в оценке не применяется.",
                       "Выберите глобальный тренд или задайте углы поиска вручную.", {"param": "trend_id"})  # fmt: skip
    return out


def meta_of(conn: sa.Connection, trend_id: int) -> tuple[str, dict[str, Any]]:
    """(name, the stored `meta`) of a trend of any kind — what `model/anisotropy.py` and `trend_struct` read; 404."""
    row = _row(conn, trend_id)
    return str(row.name), dict(row.meta or {})


def matrix_of(conn: sa.Connection, trend_id: int) -> Any:
    """A = `trend.matrix` of a stored global trend (Э18, Э19 take it here); 404 `trend_not_found`, a structural
    trend — 422 `trend_structural`: it has a matrix at every point, not one (`trend_struct.local_axes`)."""
    from . import trend as tr

    meta = _row(conn, trend_id).meta or {}
    if meta.get("kind", tr.GLOBAL) == tr.STRUCTURAL:
        raise ApiError(422, "trend_structural", "У структурного тренда нет одной матрицы осей.",
                       "Для RBF оси берутся в точках участков; в оценке выберите глобальный тренд.",
                       {"param": "trend_id"})  # fmt: skip
    return tr.matrix(meta)
