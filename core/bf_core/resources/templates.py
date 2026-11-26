"""The templates of the resource report — the reference of the project (подшаг 20.4; ТЗ 8.10 «Формы»; ADR 0027
decisions 17–19, question № 105).

A template is the SHOW of a report, not its input: the order of the rows (labels of the class key), the subtotals, the
units (kt, km³, kg, koz …) and the decimals (`forms.py` builds the form from the rows of a result and a template;
the change of a template does not make a report «устарел», ADR 0027 decision 17). The reference lives in the table
`settings` of the project (scope `resources`, key `report_templates`, one row whose `version` grows with every
write; a template has a `version` of its own too), as `blocks/schemes.py` keeps the schemes of categories:

    {"v": 1, "templates": {"my": {"name": "Мой", "version": 2, "labels": [...], "subtotals": [...], ...}}}

Two templates are always there, even in a project that never wrote one (version 1, not stored until the first PUT):
  * `jorc` — «Измеренные», «Выявленные», the subtotal «Изм. + Выяв.», «Предполагаемые», «Итого»; kt, g/t, koz (% — t);
  * `gkz` — A, B, C1, the subtotal «A + B + C1», C2, «Итого»; the columns «Балансовые» and «Забалансовые» (`balance`),
    kt, the metal in kg for g/t and t for % (the default until the sample of the owner, № 105).
They may be rewritten, not deleted; the user's own are made, rewritten and deleted.

The body of a template (checked and cleaned by `check_body`; unknown keys are dropped):
    name        1–100 characters
    labels      the labels of the rows in order (the groups of the key `key`), 1–64, unique (case aside)
    subtotals   [{label, members}] ≤ 16: a subtotal of ≥ 2 labels, shown after its last member in `labels`
    total       the label of the last row («Итого») or null — no total row
    key         the role of the key the labels belong to (default «class»; a result names the roles of g1…g3)
    section     the role of the outer key (e.g. «domain»): the form is cut into sections; null — one table
    columns     the metrics in order: tonnes, volume, grade, metal (default tonnes, grade, metal)
    balance     true — the columns «Балансовые» / «Забалансовые» (rows of kind balance / offbalance)
    units       {tonnes: t | kt | mt, volume: m3 | km3, metal: {g: g|kg|t|koz, t: g|kg|t|koz}}; the metal is by the
                base unit of the element (g for g/t, ppm, ppb; t for %), a text — for both
    decimals    {tonnes, volume, grade, metal}: 0…6

    templates(conn) -> [template]                 # defaults first, then the others by name; each {id, name, version, …}
    get(conn, id) -> template                     # 404 `template_not_found`
    put(conn, id, body) -> template               # {expected_version?, name, …}; 409 `version_conflict {version}`
    delete(conn, id, expected_version) -> {id, deleted}   # a template of the user; the two of the box — 422
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.blocks import schemes as block_schemes
from bf_core.db.schema import settings

SCOPE = "resources"
KEY = "report_templates"
BOX = ("jorc", "gkz")
MAX_LABELS = 64
MAX_SUBTOTALS = 16
MAX_LABEL = 100
MAX_NAME = 100
OTHER_LABEL = "Прочие"  # the row of the groups outside a template (`forms.py`)
DEFAULT_TOTAL = "Итого"
METRICS = ("tonnes", "volume", "grade", "metal")
DEFAULT_COLUMNS = ("tonnes", "grade", "metal")
# The troy ounce: 31.1034768 g (31.25 kg = 1004.7109 oz = 1.004711 koz, Т8.10-1).
TROY_OUNCE_G = 31.1034768
# Unit → (label for the user, factor from the base unit of the quantity: tonnes → t, m³ → m³, metal → g).
TONNES_UNITS: dict[str, tuple[str, float]] = {"t": ("т", 1.0), "kt": ("тыс. т", 1e-3), "mt": ("млн т", 1e-6)}
VOLUME_UNITS: dict[str, tuple[str, float]] = {"m3": ("м³", 1.0), "km3": ("тыс. м³", 1e-3)}
METAL_UNITS: dict[str, tuple[str, float]] = {
    "g": ("г", 1.0),
    "kg": ("кг", 1e-3),
    "t": ("т", 1e-6),
    "koz": ("тыс. унц", 1e-3 / TROY_OUNCE_G),
}
METAL_BASES = ("g", "t")  # the base unit of the metal of an element: g for g/t, ppm, ppb; t for %
DEFAULT_DECIMALS = {"tonnes": 0, "volume": 0, "grade": 2, "metal": 1}
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,19}$")


def _template(
    name: str, labels: list[str], subtotals: list[dict[str, Any]], *, balance: bool, metal: dict[str, str],
    metal_decimals: int,
) -> dict[str, Any]:  # fmt: skip
    return {
        "name": name,
        "version": 1,
        "labels": labels,
        "subtotals": subtotals,
        "total": DEFAULT_TOTAL,
        "key": "class",
        "section": None,
        "columns": list(DEFAULT_COLUMNS),
        "balance": balance,
        "units": {"tonnes": "kt", "volume": "km3", "metal": metal},
        "decimals": {**DEFAULT_DECIMALS, "metal": metal_decimals},
    }


def defaults() -> dict[str, dict[str, Any]]:
    """The two templates of the box. The labels are the ones of the schemes of categories (`blocks/schemes.py`)."""
    jorc = [label for label, _ in block_schemes.JORC_RULES]
    gkz = list(block_schemes.GKZ_LABELS)
    return {
        "jorc": _template(
            "JORC",
            jorc,
            [{"label": "Изм. + Выяв.", "members": jorc[:2]}],
            balance=False,
            metal={"g": "koz", "t": "t"},
            metal_decimals=1,
        ),  # fmt: skip
        "gkz": _template(
            "ГКЗ",
            gkz,
            [{"label": "A + B + C1", "members": gkz[:3]}],
            balance=True,
            metal={"g": "kg", "t": "t"},
            metal_decimals=0,
        ),  # fmt: skip
    }


def bad(param: str, message: str, hint: str = "Проверьте шаблон отчёта.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def not_found(template_id: str) -> ApiError:
    return ApiError(404, "template_not_found", f"Шаблон отчёта «{template_id[:60]}» не найден.",
                    "Выберите шаблон из списка.", {"id": template_id})  # fmt: skip


def conflict(version: int) -> ApiError:
    return ApiError(409, "version_conflict", "Шаблон отчёта изменили, пока вы его правили.",
                    "Перечитайте шаблоны и повторите.", {"version": version})  # fmt: skip


# --- checking ---------------------------------------------------------------------------------------------------------


def check_id(template_id: Any) -> str:
    if not isinstance(template_id, str) or not _ID.match(template_id):
        raise bad("id", "Код шаблона — до 40 латинских букв, цифр, «_» и «-».", "Например: jorc, my-template.")
    return template_id


def check_name(raw: Any) -> str:
    name = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not name or len(name) > MAX_NAME:
        raise bad("name", f"Название шаблона — от 1 до {MAX_NAME} знаков.")
    return name


def fold(text: str) -> str:
    """The key to compare labels: spaces collapsed, case aside."""
    return " ".join(text.split()).casefold()


def _label(raw: Any, param: str) -> str:
    text = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not text or len(text) > MAX_LABEL:
        raise bad(param, f"Подпись строки — от 1 до {MAX_LABEL} знаков.")
    return text


def _check_labels(raw: Any) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise bad("labels", "В шаблоне нет строк.", "Добавьте хотя бы одну подпись группы.")
    if len(raw) > MAX_LABELS:
        raise bad("labels", f"В шаблоне не больше {MAX_LABELS} строк.")
    out: list[str] = []
    for i, item in enumerate(raw):
        label = _label(item, f"labels.{i}")
        if fold(label) == fold(OTHER_LABEL):
            raise bad(f"labels.{i}", f"Подпись «{OTHER_LABEL}» занята строкой групп вне шаблона.",
                      "Назовите строку иначе.")  # fmt: skip
        if fold(label) in {fold(x) for x in out}:
            raise bad(f"labels.{i}", f"Подпись «{label}» повторяется.", "Подписи строк не повторяются.")
        out.append(label)
    return out


def _check_subtotals(raw: Any, labels: list[str]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise bad("subtotals", "Подытоги — список.")
    if len(raw) > MAX_SUBTOTALS:
        raise bad("subtotals", f"В шаблоне не больше {MAX_SUBTOTALS} подытогов.")
    by_fold = {fold(x): x for x in labels}
    taken = {*by_fold, fold(OTHER_LABEL)}
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise bad(f"subtotals.{i}", "Подытог — это подпись и список строк.")
        label = _label(item.get("label"), f"subtotals.{i}.label")
        if fold(label) in taken:
            raise bad(f"subtotals.{i}.label", f"Подпись «{label}» повторяется.",
                      "Подписи строк и подытогов не повторяются.")  # fmt: skip
        taken.add(fold(label))
        members_raw = item.get("members")
        if not isinstance(members_raw, list):
            raise bad(f"subtotals.{i}.members", "Укажите строки подытога списком подписей.")
        members: list[str] = []
        for j, m in enumerate(members_raw):
            found = by_fold.get(fold(m)) if isinstance(m, str) else None
            if found is None:
                raise bad(f"subtotals.{i}.members.{j}", f"Строки «{str(m)[:60]}» нет в шаблоне.",
                          "В подытог входят только строки шаблона.")  # fmt: skip
            if found not in members:
                members.append(found)
        if len(members) < 2:
            raise bad(f"subtotals.{i}.members", "Подытог объединяет не меньше двух строк.")
        out.append({"label": label, "members": members})
    return out


def _check_total(body: Mapping[str, Any], labels: list[str], subtotals: list[dict[str, Any]]) -> str | None:
    raw = body.get("total", DEFAULT_TOTAL)
    if raw is None or raw is False:
        return None
    label = _label(raw, "total")
    used = {fold(x) for x in [*labels, *(s["label"] for s in subtotals), OTHER_LABEL]}
    if fold(label) in used:
        raise bad("total", f"Подпись «{label}» повторяется.", "Подписи строк, подытогов и итога не повторяются.")
    return label


def _role(raw: Any, param: str, default: str | None) -> str | None:
    if raw is None:
        return default
    if not isinstance(raw, str) or not _ROLE.match(raw):
        raise bad(param, "Роль ключа — латинские буквы, цифры и «_», до 20 знаков.", "Например: class, domain, bench.")
    return raw


def _check_columns(raw: Any) -> list[str]:
    if raw is None:
        return list(DEFAULT_COLUMNS)
    if not isinstance(raw, list) or not raw or any(m not in METRICS for m in raw) or len(set(raw)) != len(raw):
        raise bad("columns", "Столбцы — непустой список без повторов из: tonnes, volume, grade, metal.",
                  "Например: tonnes, grade, metal.")  # fmt: skip
    return list(raw)


def _check_units(raw: Any) -> dict[str, Any]:
    base = defaults()["jorc"]["units"]
    if raw is None:
        return {"tonnes": base["tonnes"], "volume": base["volume"], "metal": dict(base["metal"])}
    if not isinstance(raw, Mapping):
        raise bad("units", "Единицы — тоннаж, объём и металл.")
    out: dict[str, Any] = {}
    for key, table, hint in (("tonnes", TONNES_UNITS, "t, kt, mt"), ("volume", VOLUME_UNITS, "m3, km3")):
        value = raw.get(key, base[key])
        if value not in table:
            raise bad(f"units.{key}", "Единица не из списка.", f"Допустимо: {hint}.")
        out[key] = value
    metal = raw.get("metal", base["metal"])
    if isinstance(metal, str):
        metal = dict.fromkeys(METAL_BASES, metal)
    if not isinstance(metal, Mapping):
        raise bad("units.metal", "Единица металла — текст или пара «основа: единица».", "Например: {g: koz, t: t}.")
    out["metal"] = {}
    for b in METAL_BASES:
        value = metal.get(b, base["metal"][b])
        if value not in METAL_UNITS:
            raise bad(f"units.metal.{b}", "Единица металла не из списка.", "Допустимо: g, kg, t, koz.")
        out["metal"][b] = value
    return out


def _check_decimals(raw: Any) -> dict[str, int]:
    if raw is None:
        return dict(DEFAULT_DECIMALS)
    if not isinstance(raw, Mapping):
        raise bad("decimals", "Знаки — число для тоннажа, объёма, содержания и металла.")
    out: dict[str, int] = {}
    for key in METRICS:
        value = raw.get(key, DEFAULT_DECIMALS[key])
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
            raise bad(f"decimals.{key}", "Знаков после запятой — целое от 0 до 6.")
        out[key] = value
    return out


def check_body(body: Any) -> dict[str, Any]:
    """The body of a template checked and cleaned: every key is there (defaults filled), unknown keys dropped. 422
    `bad_param {param}` with a Russian text and a hint."""
    if not isinstance(body, Mapping):
        raise bad("body", "Неверные данные запроса.", "Повторите действие из окна шаблонов.")
    labels = _check_labels(body.get("labels"))
    subtotals = _check_subtotals(body.get("subtotals"), labels)
    key = _role(body.get("key"), "key", "class")
    section = _role(body.get("section"), "section", None)
    if section is not None and section == key:
        raise bad("section", "Разделы и строки не могут идти по одному ключу.", "Выберите другую роль для разделов.")
    balance = body.get("balance", False)
    if not isinstance(balance, bool):
        raise bad("balance", "«Балансовые и забалансовые» — да или нет.")
    return {
        "name": check_name(body.get("name")),
        "labels": labels,
        "subtotals": subtotals,
        "total": _check_total(body, labels, subtotals),
        "key": key,
        "section": section,
        "columns": _check_columns(body.get("columns")),
        "balance": balance,
        "units": _check_units(body.get("units")),
        "decimals": _check_decimals(body.get("decimals")),
    }


# --- the reference ----------------------------------------------------------------------------------------------------


def _row(conn: sa.Connection) -> tuple[dict[str, Any], int] | None:
    r = conn.execute(sa.select(settings.c.value, settings.c.version).where(settings.c.scope == SCOPE,
                                                                           settings.c.key == KEY)).first()  # fmt: skip
    if r is None:
        return None
    value = r.value if isinstance(r.value, dict) else {}
    stored = value.get("templates")
    return (stored if isinstance(stored, dict) else {}), int(r.version)


def _all(conn: sa.Connection) -> tuple[dict[str, dict[str, Any]], int]:
    """The templates by id (the defaults overlaid by the stored ones) and the version of the row (0 — none yet). A
    stored body that does not pass the check any more is left out (a default stays)."""
    found = _row(conn)
    merged = defaults()
    if found is not None:
        for tid, t in found[0].items():
            if not isinstance(t, dict):
                continue
            try:
                body = check_body(t)
            except ApiError:
                continue
            merged[str(tid)] = {**body, "version": int(t.get("version") or 1)}
    return merged, 0 if found is None else found[1]


def templates(conn: sa.Connection) -> list[dict[str, Any]]:
    """Every template `{id, name, version, …}`: the two of the box first, then the user's by name."""
    merged, _ = _all(conn)
    rest = sorted((k for k in merged if k not in BOX), key=lambda k: (merged[k]["name"].casefold(), k))
    return [{"id": k, **merged[k]} for k in [*BOX, *rest]]


def get(conn: sa.Connection, template_id: str) -> dict[str, Any]:
    merged, _ = _all(conn)
    if template_id not in merged:
        raise not_found(template_id)
    return {"id": template_id, **merged[template_id]}


def _write(conn: sa.Connection, merged: dict[str, dict[str, Any]], row_version: int, current: int) -> None:
    """Store the reference (the untouched box is not kept); a version of the row that moved — 409."""
    box = defaults()
    stored = {"v": 1, "templates": {k: v for k, v in merged.items() if box.get(k) != v}}
    if row_version == 0:
        try:
            with conn.begin_nested():  # two first writes at once: the second one is a conflict, not a 500
                conn.execute(sa.insert(settings).values(scope=SCOPE, key=KEY, value=stored, version=1))
        except sa.exc.IntegrityError as e:
            raise conflict(current) from e
        return
    n = conn.execute(
        sa.update(settings)
        .where(settings.c.scope == SCOPE, settings.c.key == KEY, settings.c.version == row_version)
        .values(value=stored, version=row_version + 1)
    ).rowcount
    if n != 1:
        raise conflict(current)


def _expected(raw: Any) -> int | None:
    if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int) or raw < 1):
        raise bad("expected_version", "Версия шаблона — целое число от 1.")
    return raw  # type: ignore[no-any-return]


def put(conn: sa.Connection, template_id: str, body: Any) -> dict[str, Any]:
    """Write a template: `{expected_version?, name, labels, …}`. A template that exists (a default is one, at version
    1) is written with its current `expected_version` (409 `version_conflict {version}` otherwise); a new one — without
    it. The version of the template grows by one."""
    if not isinstance(body, Mapping):
        raise bad("body", "Неверные данные запроса.", "Повторите действие из окна шаблонов.")
    tid = check_id(template_id)
    clean = check_body(body)
    expected = _expected(body.get("expected_version"))
    merged, row_version = _all(conn)
    current = int(merged[tid]["version"]) if tid in merged else 0
    if expected != (current or None):
        raise conflict(current)
    merged[tid] = {**clean, "version": current + 1}
    _write(conn, merged, row_version, current)
    return {"id": tid, **merged[tid]}


def delete(conn: sa.Connection, template_id: str, expected_version: Any = None) -> dict[str, Any]:
    """Delete a template of the user with its current `expected_version` (409 `version_conflict`). 404
    `template_not_found`; the two of the box are rewritten, not deleted (422)."""
    tid = check_id(template_id)
    if tid in BOX:
        raise bad("id", "Встроенный шаблон нельзя удалить.", "Измените его или создайте свой шаблон.")
    expected = _expected(expected_version)
    merged, row_version = _all(conn)
    if tid not in merged:
        raise not_found(tid)
    current = int(merged[tid]["version"])
    if expected != current:
        raise conflict(current)
    del merged[tid]
    _write(conn, merged, row_version, current)
    return {"id": tid, "deleted": True}
