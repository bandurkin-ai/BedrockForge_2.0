"""`GET /api/v1/intercepts/codes?set_id=&table=&column=` — the codes of a domain field for «Заполнить кодами поля»
(ADR 0021 §11; the form — docs/progress/e15-api.md).

→ {codes, total, truncated}: the distinct non-empty values of the field over the rows of the table, written as the
orientation writes a domain code (`orient._field_codes`): a text without spaces around, a number by `.15g` (−0.0 is
0); sorted (numbers by value, texts as strings), at most `CODES_MAX`; `total` — every distinct code, `truncated` —
`total > CODES_MAX`. Read only. 404 `set_not_found`, `table_not_found` (`table` — the id of a ready table of the set,
an ASCII number), `column_not_found` (`column` — the key of a field or its name).
"""

from __future__ import annotations

from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from bf_core.api.errors import ApiError
from bf_core.db import dialect
from bf_core.db.schema import dh_tables, objects
from bf_core.projects.service import projects
from bf_core.projects.store import ProjectService

router = APIRouter()
Svc = Annotated[ProjectService, Depends(projects)]
#: The most codes one answer gives (the table of domains holds up to 1 000 rows).
CODES_MAX = 1000


def _check(conn: sa.Connection, set_id: int, table: str) -> None:
    got = conn.execute(sa.select(objects.c.id).where(objects.c.id == set_id, objects.c.kind == "drillhole_set"))
    if got.first() is None:
        raise ApiError(404, "set_not_found", "Набор скважин не найден.", "Обновите дерево проекта и выберите набор.")
    as_id = table.isascii() and table.isdigit() and len(table) <= 18  # «²», «١», 10**30 — not an id
    q = sa.select(dh_tables.c.id).where(dh_tables.c.set_id == set_id, dh_tables.c.state == "ready")
    if not as_id or conn.execute(q.where(dh_tables.c.id == int(table))).first() is None:
        raise ApiError(404, "table_not_found", "Таблица не найдена.", "Обновите дерево проекта и выберите таблицу.")


def distinct_codes(values: Any, kind: str) -> list[str]:
    """The distinct non-empty codes of `values` (a column of a snapshot), sorted, as the orientation writes them."""
    import numpy as np

    if kind == "number":
        x = np.asarray(values, dtype=np.float64)
        u = np.unique(x[~np.isnan(x)] + 0.0)  # −0.0 is 0
        return [format(float(v), ".15g") for v in u.tolist()]
    texts = {str(v).strip() for v in values if v is not None}
    texts.discard("")
    return sorted(texts)


@router.get("/codes")
def codes(
    svc: Svc,
    set_id: int,
    table: Annotated[str, Query(max_length=40)],
    column: Annotated[str, Query(max_length=300)],
) -> dict[str, Any]:
    from bf_core.drillholes.router import snapshot_of

    project = svc.require_current()
    with dialect.read_tx(project.engine) as conn:
        _check(conn, set_id, table.strip())
    snap = snapshot_of(project, set_id, table.strip())
    want = column.strip()
    col = next((c for c in snap.columns if c.key == want), None)
    if col is None:
        key = dialect.name_key(want)
        col = next((c for c in snap.columns if dialect.name_key(c.name) == key), None) if want else None
    if col is None or (col.key not in snap.data and col.key != "hole"):
        raise ApiError(404, "column_not_found", "Поле не найдено в таблице.", "Обновите список полей и выберите поле.")
    found = distinct_codes(snap.values(col.key)[snap.alive], "number" if col.kind == "number" else "text")
    return {"codes": found[:CODES_MAX], "total": len(found), "truncated": len(found) > CODES_MAX}
