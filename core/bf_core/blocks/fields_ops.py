"""The properties of the fields of a block model: the list with «устарел» and «Где используется», the edit of the unit,
the description and the role, the deletion, «Перестроить» of a field (подшаг 17.29; ТЗ 8.7.2, 8.7.5; ADR 0023 §2, §7,
§9, решение 4; the forms — `docs/progress/e17-api.md` §4, §7). The routes are `routes_fields.py`.

    rows = describe(conn, store, model)                       # GET /blocks/{id}/fields
    version, items = edit_fields(conn, id, version, edits)    # PATCH …/fields/{name}: unit, description, role
    version = remove_field(conn, store, id, version, name)    # DELETE …/fields/{name}
    kind, params, title = rebuild_request(conn, store, model, field, version)   # POST …/fields/{name}/rebuild
    stale, reason = stale_of(conn, store, model, field)       # «устарел» of a field

A field (`GET`): {name, kind, dtype, unit, description, role, labels_count, stats, file, source {op, params, algo,
at} | null, stale, stale_reason, rebuildable, deletable, required, usage}. «Устарел» by the maker (`source.op`):
  calc — `calc_job.is_stale` (the files the expression and the restrictor read, the filter, `algo`);
  density, classify — `jobs_fields.is_stale` (the same over the rule, the table, the wireframes of the categories);
  fill, code, regularize — the inputs of the model (`fill.inputs.stale_reason`: a wireframe or the source model
    changed or gone); a coding without splitting records only the file of the cells, so it is never «устарел»;
  import and a field with no source — never.
`rebuildable` — a field of calc, density or classify with its params: «Перестроить» of the field; a field of a fill, a
coding with splitting or a regularization is rebuilt with the model (`POST /blocks/{id}/rebuild`).

«Где используется» (`usage`): [{kind: "field" | "view", id, name, role}] — the other fields of the model whose rule
reads the field (`field`, role `source`), the project view and the saved views of the model that colour by it, set a
quick restrictor on it or name a filter of the library that reads it (`view`, roles `colour`, `restrictor`,
`filter`). Reports and the dialogs keep nothing about a field: they are not listed. A field with a role `frac` is
required (`required`): it sets the volume of the rows — 422 `field_required` until the role is taken off.

The edit. `unit` (≤ 40 symbols) and `description` (≤ 500) — any text or null; `role`: a number — grade, density,
frac or null; a category — code, class or null (422 `role_invalid {param, field}` for another); frac — one field of
a model (422 `frac_exists`) with the values in [0; 1] (the stats), density — the values above 0. The name cannot be
changed: 422 `field_rename_unsupported` (the name is the key of the files of the model, of the filters, the views and
the expressions that read it: make the field again by the calculator and delete the old one). Written with the version
+ 1; the files do not change (the fields stay «актуальны»).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import calc_job
from bf_core.blocks import fields_store as fst
from bf_core.blocks import jobs_fields as jf
from bf_core.blocks import schema as bs
from bf_core.blocks.fill import commit, inputs
from bf_core.blocks.filters import schema as fs
from bf_core.blocks.model import Model
from bf_core.db.schema import objects, settings
from bf_core.storage.blobs import BlobStore

MAKERS = ("calc", "density", "classify")
MODEL_MAKERS = ("fill", "code", "regularize")
# an ApiError of these codes while the inputs of a rule are resolved: an input is gone (409 `source_lost`)
LOST = ("expr_syntax", "expr_field", "expr_type", "filter_not_found", "filter_field_missing", "density_field_missing",
        "classify_field_missing", "block_field_not_found", "wireframe_not_found", "bad_param", "not_found")  # fmt: skip


def bad(param: str, message: str, hint: str, code: str = "bad_param") -> ApiError:
    return ApiError(422, code, message, hint, {"param": param})


def field_not_found(name: Any, param: str | None = None) -> ApiError:
    extra = {} if param is None else {"param": param}
    return ApiError(404, "block_field_not_found", f"Поле «{name}» не найдено.", "Обновите свойства модели.", extra)


# --- what a field reads -----------------------------------------------------------------------------------------------


def _calc_reads(conn: sa.Connection, store: BlobStore, model: Model, field: Mapping[str, Any]) -> list[str]:
    params = field["source"]["params"]
    prog, _ = calc_job.compile_for(model, str(params["expr"]), params.get("dtype"))
    tree = calc_job.rules_tree(conn, params.get("filter_id"), params.get("restrict"))
    names = list(prog.fields_used) + ([] if tree is None else fs.fields_of(tree))
    return [n for n in names if n.casefold() != str(field["name"]).casefold()]


def reads_of(conn: sa.Connection, store: BlobStore, model: Model, field: Mapping[str, Any]) -> list[str] | None:
    """The names of the other fields of the model the rule of `field` reads; None — no rule or it cannot be read now
    (an input is gone)."""
    src = field.get("source")
    if not isinstance(src, Mapping) or src.get("op") not in MAKERS or not src.get("params"):
        return None
    try:
        if src["op"] == "calc":
            return _calc_reads(conn, store, model, field)
        return jf.reads_of(conn, store, model, field).names
    except (ApiError, KeyError, TypeError, ValueError):
        return None


def stale_of(conn: sa.Connection, store: BlobStore, model: Model, field: Mapping[str, Any]) -> tuple[bool, str | None]:
    """(stale, reason) of a field by its maker (the module text)."""
    src = field.get("source")
    op = src.get("op") if isinstance(src, Mapping) else None
    if op == "calc":
        if not calc_job.is_stale(conn, store, model, field):
            return False, None
        try:
            _calc_reads(conn, store, model, field)
        except (ApiError, KeyError, TypeError):
            return True, "Нет входов: поле или фильтр, из которых считалось, удалены или изменены."
        return True, "Изменились поля или фильтр, из которых считалось, или алгоритм."
    if op in ("density", "classify"):
        if not jf.is_stale(conn, store, model, field):
            return False, None
        try:
            jf.reads_of(conn, store, model, field)
        except (ApiError, KeyError, TypeError, ValueError):
            return True, "Нет входов: поле, фильтр или каркас, из которых считалось, удалены."
        return True, "Изменились поля, фильтр или каркасы, из которых считалось, или алгоритм."
    if op in MODEL_MAKERS:
        why = model_stale(conn, model)
        return why is not None, why
    return False, None


def model_stale(conn: sa.Connection, model: Model) -> str | None:
    """`stale_reason` of the model: its inputs (the wireframes of a fill, the source of a regularization) changed."""
    return inputs.stale_reason(conn, model.meta.get("inputs"), inputs.names_in(model.meta))


# --- «Где используется» -----------------------------------------------------------------------------------------------


def _style_uses(conn: sa.Connection, style: Any, name: str) -> list[str]:
    """The roles in which a style of a block model (e17-api §14) holds the field."""
    from .filters import store as filters

    if not isinstance(style, Mapping):
        return []
    key = name.casefold()
    out: list[str] = []
    colour = style.get("colour")
    if isinstance(colour, Mapping) and str(colour.get("field") or "").casefold() == key:
        out.append("colour")
    quick = style.get("quick")
    items = quick.get("items") if isinstance(quick, Mapping) else None
    if isinstance(items, list) and any(isinstance(i, Mapping) and str(i.get("field") or "").casefold() == key
                                       for i in items):  # fmt: skip
        out.append("restrictor")
    fid = style.get("filter_id")
    if isinstance(fid, int) and not isinstance(fid, bool):
        try:
            if key in {n.casefold() for n in fs.fields_of(filters.rules_of(conn, fid))}:
                out.append("filter")
        except ApiError:
            pass
    return out


def view_uses(conn: sa.Connection, model: Model, name: str) -> list[dict[str, Any]]:
    """The project view and the saved views that name the field in the style of the model."""
    from bf_core.viewstate.keys import SCOPE
    from bf_core.viewstate.views import KIND as VIEW

    from .filters import store as filters

    out: list[dict[str, Any]] = []
    current = conn.execute(
        sa.select(settings.c.value).where(settings.c.scope == SCOPE, settings.c.key == filters.VIEW_KEY)
    ).scalar()
    for role in _style_uses(conn, filters._styles_of(current).get(str(model.id)), name):  # noqa: SLF001
        out.append({"kind": "view", "id": model.id, "name": f"Вид проекта · {model.name}", "role": role})
    saved = conn.execute(sa.select(objects.c.id, objects.c.name, objects.c.meta).where(objects.c.kind == VIEW)
                         .order_by(objects.c.name_key, objects.c.id)).all()  # fmt: skip
    for r in saved:
        styles = filters._styles_of(((r.meta or {}).get("keys") or {}).get(filters.VIEW_KEY))  # noqa: SLF001
        for role in _style_uses(conn, styles.get(str(model.id)), name):
            out.append({"kind": "view", "id": int(r.id), "name": str(r.name), "role": role})
    return out


def dependents(conn: sa.Connection, store: BlobStore, model: Model) -> dict[str, list[str]]:
    """{field (casefold): [names of the fields of the model whose rule reads it]}."""
    out: dict[str, list[str]] = {}
    for f in model.fields:
        for n in reads_of(conn, store, model, f) or []:
            out.setdefault(n.casefold(), []).append(str(f["name"]))
    return out


def usage_of(
    conn: sa.Connection, store: BlobStore, model: Model, name: str, deps: Mapping[str, Sequence[str]] | None = None
) -> list[dict[str, Any]]:
    """«Где используется» of a field (the module text); `deps` — `dependents(...)` when asked for many fields."""
    deps = dependents(conn, store, model) if deps is None else deps
    out: list[dict[str, Any]] = [{"kind": "field", "id": None, "name": n, "role": "source"}
                                 for n in deps.get(name.casefold(), [])]  # fmt: skip
    return out + view_uses(conn, model, name)


def is_required(field: Mapping[str, Any]) -> bool:
    return field.get("role") == "frac"


# --- the list ---------------------------------------------------------------------------------------------------------


def _source_view(src: Any) -> dict[str, Any] | None:
    if not isinstance(src, Mapping):
        return None
    return {k: src.get(k) for k in ("op", "params", "algo", "at") if k in src}


def describe(conn: sa.Connection, store: BlobStore, model: Model) -> list[dict[str, Any]]:
    """The fields of the model for the window «Свойства»: the module text."""
    deps = dependents(conn, store, model)
    rows: list[dict[str, Any]] = []
    for f in model.fields:
        stale, why = stale_of(conn, store, model, f)
        src = f.get("source")
        op = src.get("op") if isinstance(src, Mapping) else None
        use = usage_of(conn, store, model, str(f["name"]), deps)
        rows.append({
            "name": f["name"], "kind": f["kind"], "dtype": f["dtype"], "unit": f.get("unit"),
            "description": f.get("description"), "role": f.get("role"), "labels_count": len(f.get("labels") or []),
            "stats": f.get("stats"), "file": f["file"], "source": _source_view(src), "stale": stale,
            "stale_reason": why, "rebuildable": op in MAKERS and bool((src or {}).get("params")),
            "required": is_required(f), "deletable": not is_required(f) and not use, "usage": use,
        })  # fmt: skip
    return rows


# --- the edit ---------------------------------------------------------------------------------------------------------


def _clean(value: Any, key: str, limit: int, index: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise bad(f"fields.{index}.{key}", "Ожидается текст.", "Введите значение текстом или оставьте пустым.")
    text = " ".join(value.split())
    if len(text) > limit:
        raise bad(f"fields.{index}.{key}", f"Не больше {limit} знаков.", "Сократите текст.")
    return text or None


def _check_role(field: Mapping[str, Any], role: Any, index: int) -> None:
    roles = bs.CATEGORY_ROLES if field["kind"] == bs.CATEGORY else bs.NUMBER_ROLES
    param = f"fields.{index}.role"
    if role is not None and role not in roles:
        raise ApiError(
            422,
            "role_invalid",
            f"Роль «{role}» не подходит полю «{field['name']}».",
            "Допустимо: " + ", ".join(roles) + " или без роли.",
            {"param": param, "field": field["name"]},
        )
    stats = field.get("stats") or {}
    low, high = stats.get("min"), stats.get("max")
    if role == "frac" and low is not None and high is not None and (low < 0.0 or high > 1.0 + 1e-9):
        raise ApiError(422, "role_invalid", f"Значения поля «{field['name']}» не доли: они выходят за 0…1.",
                       "Роль «Доля объёма» — только для поля со значениями от 0 до 1.",
                       {"param": param, "field": field["name"]})  # fmt: skip
    if role == "density" and low is not None and low <= 0.0:
        raise ApiError(
            422,
            "role_invalid",
            f"В поле «{field['name']}» есть значения не больше нуля.",
            "Плотность — число больше нуля.",
            {"param": param, "field": field["name"]},
        )


def _rename_refused(field: Mapping[str, Any], index: int) -> ApiError:
    return ApiError(
        422, "field_rename_unsupported", f"Поле «{field['name']}» переименовать нельзя.",
        "Имя поля — ключ файлов модели, фильтров, видов и выражений. Создайте поле калькулятором под новым именем "
        "и удалите старое.", {"param": f"fields.{index}.name", "field": field["name"]},
    )  # fmt: skip


def edit_fields(
    conn: sa.Connection, model_id: int, expected_version: int, edits: Sequence[Mapping[str, Any]]
) -> tuple[int, list[dict[str, Any]]]:
    """Inside the write transaction: the unit, the description and the role of fields (the module text). Every edit is
    {name, unit?, description?, role?} — only the keys present are changed. → (the new version, the fields changed).
    404 `block_field_not_found {param: "fields.N.name"}`, 409 `version_conflict {version}`, 422 `bad_param`,
    `role_invalid`, `frac_exists`, `field_rename_unsupported`."""
    if not edits:
        raise bad("fields", "Нечего менять.", "Задайте единицу, описание или роль.")
    changed: list[dict[str, Any]] = []

    def edit(meta: dict[str, Any]) -> None:
        by = {str(f["name"]).casefold(): f for f in meta["fields"]}
        touched: set[str] = set()
        for n, e in enumerate(edits):
            name = e.get("name")
            f = by.get(str(name).casefold()) if isinstance(name, str) else None
            if f is None:
                raise field_not_found(name, f"fields.{n}.name")
            if "new_name" in e and e["new_name"] != f["name"]:
                raise _rename_refused(f, n)
            if "unit" in e:
                f["unit"] = _clean(e["unit"], "unit", bs.MAX_UNIT, n)
            if "description" in e:
                f["description"] = _clean(e["description"], "description", bs.MAX_DESCRIPTION, n)
            if "role" in e:
                _check_role(f, e["role"], n)
                f["role"] = e["role"]
            touched.add(str(f["name"]))
        fracs = [str(f["name"]) for f in meta["fields"] if f.get("role") == "frac"]
        if len(fracs) > 1:
            raise ApiError(
                422,
                "frac_exists",
                f"В модели уже есть поле доли объёма «{fracs[0]}».",
                "Снимите роль «Доля объёма» с одного из полей.",
                {"param": "role", "field": fracs[0]},
            )
        changed.extend(dict(f) for f in meta["fields"] if str(f["name"]) in touched)

    meta, old = fst._modify(conn, model_id, expected_version, edit)  # noqa: SLF001  (the one path that locks the meta)
    version = fst._save(conn, model_id, meta, old + 1)  # noqa: SLF001
    return version, changed


# --- the deletion -----------------------------------------------------------------------------------------------------


def remove_field(conn: sa.Connection, store: BlobStore, model_id: int, expected_version: int, name: str) -> int:
    """Inside the write transaction: the field leaves the model (`delete_fields`) unless it is required or used:
    422 `field_required`, 422 `field_in_use {usage}`; 404 `block_field_not_found`; 409 `version_conflict`."""
    model = Model.open(conn, store, model_id)
    if model.version != expected_version:
        raise fst.version_conflict(model.version)
    field = model.field(name)
    if is_required(field):
        raise ApiError(
            422, "field_required", f"Поле «{field['name']}» задаёт объём блоков, удалить его нельзя.",
            "Снимите роль «Доля объёма» в свойствах поля, затем удалите его.", {"field": field["name"]},
        )  # fmt: skip
    use = usage_of(conn, store, model, str(field["name"]))
    if use:
        names = ", ".join(f"«{u['name']}»" for u in use[:5]) + (" и другие" if len(use) > 5 else "")
        raise ApiError(422, "field_in_use", f"Поле «{field['name']}» используется: {names}.",
                       "Уберите поле из этих мест или удалите зависимые поля, затем удалите поле.",
                       {"field": field["name"], "usage": use})  # fmt: skip
    return fst.delete_fields(conn, model_id, expected_version, [str(field["name"])])


# --- «Перестроить» of a field -----------------------------------------------------------------------------------------


def not_rebuildable() -> ApiError:
    return ApiError(
        422, "not_rebuildable", "Это поле нельзя перестроить.",
        "Перестраиваются поля калькулятора, плотности и категорий; поля заполнения и регуляризации — вместе с моделью.",
    )  # fmt: skip


def rebuild_request(
    conn: sa.Connection, store: BlobStore, model: Model, field: Mapping[str, Any], expected_version: int
) -> tuple[str, dict[str, Any], str]:
    """(job kind, its params, title) that make the field again from `source.params`, replacing it (the unit and the
    description it has are kept). 422 `not_rebuildable`; 409 `source_lost` — an input is gone (a field, a filter, a
    wireframe of a category), `version_conflict {version}`."""
    src = field.get("source")
    op = src.get("op") if isinstance(src, Mapping) else None
    if op not in MAKERS or not isinstance(src, Mapping) or not src.get("params"):
        raise not_rebuildable()
    if model.version != expected_version:
        raise fst.version_conflict(model.version)
    params = dict(src["params"])
    try:
        if op == "calc":
            _calc_reads(conn, store, model, field)
            tree = calc_job.rules_tree(conn, params.get("filter_id"), params.get("restrict"))
            jf.check_restrictor(model, tree)
        else:
            jf.check_inputs_alive(conn, store, model, field)
    except ApiError as e:
        if e.code in LOST:
            raise commit.source_lost(f"{e.message.rstrip('.')}") from None
        raise
    base = {"model_id": model.id, "expected_version": expected_version, "replace": True, "rows": model.rows}
    if op == "calc":
        body = {**base, "expr": params["expr"], "field": field["name"], "dtype": params.get("dtype"),
                "unit": field.get("unit"), "description": field.get("description"),
                "filter_id": params.get("filter_id"), "restrict": params.get("restrict"),
                "outside": params.get("outside", "keep")}  # fmt: skip
        return "blocks.calc", body, f"Перестроить: {field['name']} · {model.name}"
    main = next((f for f in model.fields if str(f["name"]).casefold() == str(params.get("out")).casefold()), field)
    body = {**base, **params, "unit": main.get("unit"), "description": main.get("description")}
    kind = "blocks.density" if op == "density" else "blocks.classify"
    return kind, body, f"Перестроить: {main['name']} · {model.name}"
