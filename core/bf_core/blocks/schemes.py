"""The schemes of categories of block models — the reference of the project (подшаг 17.26; ТЗ 8.7.5; ADR 0023 §9,
decision 13, question № 68; the forms — `docs/progress/e17-api.md` §9).

A scheme is an ordered list of rules `{label, expr}`: the first rule a block fulfils gives it the label
(`categories.py`).
The reference lives in the table `settings` of the project (scope `blocks`, key `block_class_schemes`, one row: its
`version` grows with every write, a scheme has a `version` of its own too):

    {"v": 1, "schemes": {"jorc": {"name": "JORC", "version": 2, "rules": [{"label": "Измеренные", "expr": "…"}]}}}

Two schemes are always there, even in a project that never wrote one (version 1, not stored until the first PUT):
  * `jorc` — the example of 8.7.5: «Измеренные» — PASS = 1 AND SLOPE >= 0.8 AND NH >= 3 AND AD <= 25; «Выявленные» —
    PASS <= 2 AND SLOPE >= 0.5 AND NH >= 2; «Предполагаемые» — every block with an estimate (`1 = 1`: the estimate is
    asked for by the parameter `estimate` of the classification, not by the rule);
  * `gkz` — the template A, B, C1, C2 with no thresholds (`expr` is null; № 68: the thresholds are the owner's). A
    scheme with a rule without `expr` cannot classify: 422 `scheme_incomplete` (`categories.py`).
The user's own schemes are added by `PUT /blocks/schemes/{id}`; the two above may be rewritten, not deleted.

    schemes(conn) -> [scheme]                 # defaults first, then the others by name; each {id, name, version, rules}
    get(conn, id) -> scheme                   # 404 `scheme_not_found`
    put(conn, id, body) -> scheme             # {expected_version?, name, rules}; 409 `version_conflict {version}`
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa

from bf_core.api.errors import ApiError
from bf_core.calc import ExprError, parse
from bf_core.db.schema import settings

from .filters import schema as fs

SCOPE = "blocks"
KEY = "block_class_schemes"
MAX_RULES = 64
MAX_LABEL = 100
MAX_NAME = 100
MAX_EXPR = 4000
_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

JORC_RULES = (
    ("Измеренные", "PASS = 1 AND SLOPE >= 0.8 AND NH >= 3 AND AD <= 25"),
    ("Выявленные", "PASS <= 2 AND SLOPE >= 0.5 AND NH >= 2"),
    ("Предполагаемые", "1 = 1"),
)
GKZ_LABELS = ("A", "B", "C1", "C2")


def defaults() -> dict[str, dict[str, Any]]:
    """The two schemes of the box: JORC as in 8.7.5, ГКЗ as the template without thresholds."""
    return {
        "jorc": {
            "name": "JORC",
            "version": 1,
            "rules": [{"label": label, "expr": expr} for label, expr in JORC_RULES],
        },  # fmt: skip
        "gkz": {"name": "ГКЗ", "version": 1, "rules": [{"label": label, "expr": None} for label in GKZ_LABELS]},
    }


def bad(param: str, message: str, hint: str = "Проверьте схему категорий.") -> ApiError:
    return ApiError(422, "bad_param", message, hint, {"param": param})


def not_found(scheme_id: str) -> ApiError:
    return ApiError(404, "scheme_not_found", f"Схема категорий «{scheme_id[:60]}» не найдена.",
                    "Выберите схему из списка.", {"id": scheme_id})  # fmt: skip


def conflict(version: int) -> ApiError:
    return ApiError(409, "version_conflict", "Схему категорий изменили, пока вы её правили.",
                    "Перечитайте схемы и повторите.", {"version": version})  # fmt: skip


# --- checking ---------------------------------------------------------------------------------------------------------


def check_id(scheme_id: Any) -> str:
    if not isinstance(scheme_id, str) or not _ID.match(scheme_id):
        raise bad("id", "Код схемы — до 40 латинских букв, цифр, «_» и «-».", "Например: jorc, my-scheme.")
    return scheme_id


def check_rules(raw: Any, param: str = "rules") -> list[dict[str, Any]]:
    """The rules of a scheme checked and cleaned: labels unique (case aside), `expr` null or a text that parses (the
    fields are checked against a model by the classification, not here)."""
    if not isinstance(raw, list) or not raw:
        raise bad(param, "В схеме нет правил.", "Добавьте хотя бы одну категорию.")
    if len(raw) > MAX_RULES:
        raise bad(param, f"В схеме не больше {MAX_RULES} правил.")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, rule in enumerate(raw):
        if not isinstance(rule, Mapping):
            raise bad(f"{param}.{i}", "Правило — это подпись и условие.")
        label = rule.get("label")
        label = " ".join(label.split()) if isinstance(label, str) else ""
        if not label or len(label) > MAX_LABEL:
            raise bad(f"{param}.{i}.label", f"Подпись категории — от 1 до {MAX_LABEL} знаков.")
        if label.casefold() in seen:
            raise bad(f"{param}.{i}.label", f"Подпись «{label}» повторяется.", "Подписи категорий не повторяются.")
        seen.add(label.casefold())
        expr = rule.get("expr")
        if expr is not None and not isinstance(expr, str):
            raise bad(f"{param}.{i}.expr", "Условие — текст выражения или пусто.")
        if expr is not None and not expr.strip():
            expr = None
        if expr is not None:
            if len(expr) > MAX_EXPR:
                raise bad(f"{param}.{i}.expr", f"Условие — не больше {MAX_EXPR} знаков.")
            try:
                parse(expr)
            except ExprError as e:
                raise fs.expr_error(e, f"{param}.{i}.expr") from e
        out.append({"label": label, "expr": expr})
    return out


def check_name(raw: Any) -> str:
    name = " ".join(raw.split()) if isinstance(raw, str) else ""
    if not name or len(name) > MAX_NAME:
        raise bad("name", f"Название схемы — от 1 до {MAX_NAME} знаков.")
    return name


# --- the reference ----------------------------------------------------------------------------------------------------


def _row(conn: sa.Connection) -> tuple[dict[str, Any], int] | None:
    r = conn.execute(sa.select(settings.c.value, settings.c.version).where(settings.c.scope == SCOPE,
                                                                           settings.c.key == KEY)).first()  # fmt: skip
    if r is None:
        return None
    value = r.value if isinstance(r.value, dict) else {}
    stored = value.get("schemes")
    return (stored if isinstance(stored, dict) else {}), int(r.version)


def _all(conn: sa.Connection) -> tuple[dict[str, dict[str, Any]], int]:
    """The schemes by id (the defaults overlaid by the stored ones) and the version of the row (0 — none yet)."""
    found = _row(conn)
    merged = defaults()
    if found is not None:
        for sid, s in found[0].items():
            if isinstance(s, dict) and isinstance(s.get("rules"), list):
                merged[str(sid)] = {"name": str(s.get("name") or sid), "version": int(s.get("version") or 1),
                                    "rules": s["rules"]}  # fmt: skip
    return merged, 0 if found is None else found[1]


def schemes(conn: sa.Connection) -> list[dict[str, Any]]:
    """Every scheme `{id, name, version, rules}`: the two of the box first, then the user's by name."""
    merged, _ = _all(conn)
    fixed = [k for k in ("jorc", "gkz") if k in merged]
    rest = sorted((k for k in merged if k not in fixed), key=lambda k: (merged[k]["name"].casefold(), k))
    return [{"id": k, **merged[k]} for k in [*fixed, *rest]]


def get(conn: sa.Connection, scheme_id: str) -> dict[str, Any]:
    merged, _ = _all(conn)
    if scheme_id not in merged:
        raise not_found(scheme_id)
    return {"id": scheme_id, **merged[scheme_id]}


def put(conn: sa.Connection, scheme_id: str, body: Any) -> dict[str, Any]:
    """Write a scheme: `{expected_version?, name, rules}`. A scheme that exists (a default is one, at version 1) is
    written with its current `expected_version` (409 `version_conflict {version}` otherwise); a new one — without it.
    The version of the scheme grows by one."""
    if not isinstance(body, Mapping):
        raise bad("body", "Неверные данные запроса.", "Повторите действие из окна схем.")
    sid = check_id(scheme_id)
    name = check_name(body.get("name"))
    rules = check_rules(body.get("rules"))
    expected = body.get("expected_version")
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected < 1):
        raise bad("expected_version", "Версия схемы — целое число от 1.")
    merged, row_version = _all(conn)
    current = int(merged[sid]["version"]) if sid in merged else 0
    if expected != (current or None):
        raise conflict(current)
    merged[sid] = {"name": name, "version": current + 1, "rules": rules}
    box = defaults()
    stored = {"v": 1, "schemes": {k: v for k, v in merged.items() if box.get(k) != v}}  # the untouched box is not kept
    if row_version == 0:
        try:
            with conn.begin_nested():  # two first writes at once: the second one is a conflict, not a 500
                conn.execute(sa.insert(settings).values(scope=SCOPE, key=KEY, value=stored, version=1))
        except sa.exc.IntegrityError as e:
            raise conflict(current) from e
    else:
        n = conn.execute(
            sa.update(settings)
            .where(settings.c.scope == SCOPE, settings.c.key == KEY, settings.c.version == row_version)
            .values(value=stored, version=row_version + 1)
        ).rowcount
        if n != 1:
            raise conflict(current)
    return {"id": sid, **merged[sid]}
