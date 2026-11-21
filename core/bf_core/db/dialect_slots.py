"""Columns packed into the rows of a table written by statements of the database (`dialect.set_slots`; ADR 0003
§4, §6). A part of `db/dialect.py` kept in a file of its own for its size: everything here is used through
`bf_core.db.dialect` (`dialect.set_slots`, `dialect.SLOT_ROWS`, `SLOT_RUNS`, `SLOT_LABELS`), never imported
directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Connection

#: `set_slots`: rows of a statement (8 bytes of a blob a row and a column), the runs of keys and the labels of a
#: text column it takes (a CASE of that many branches a row)
SLOT_ROWS, SLOT_RUNS, SLOT_LABELS = 1_000_000, 64, 32
_NAN = b"\x00\x00\x00\x00\x00\x00\xf8\x7f"  # NaN of `dh_rows.num`: float64, little-endian
_FLAT = "txt GLOB '{?*\"}'"  # {…"}: an object that ends with a string — without its last character it ends with «"»


def _vector_sql(column: str, slots: Sequence[tuple[int, str]]) -> str:
    """SQL of the float64 vector `column` with `slots` [(slot, SQL of its 8 bytes)] (ascending) set; `:pad` — NaN
    for every slot up to the last one. No empty slots are left at the end (NULL — no value at all)."""
    nan = f"x'{_NAN.hex()}'"
    parts, at = [], 0
    for slot, value in slots:
        if slot > at:  # the slots between: of the row, or NaN where it is shorter
            cut = f", {8 * at + 1}, {8 * (slot - at)})"
            parts.append(f"CASE WHEN length({column}) >= {8 * slot} THEN substr({column}{cut} "
                         f"ELSE substr(CAST(coalesce({column}, x'') || :pad AS BLOB){cut} END")  # fmt: skip
        parts.append(value)
        at = slot + 1
    full = f"CAST({' || '.join(parts)} || coalesce(substr({column}, {8 * at + 1}), x'') AS BLOB)"
    none = " AND ".join(f"{value} = {nan}" for _slot, value in slots)
    whens = [f"WHEN coalesce(length({column}), 0) <= {8 * slots[0][0]} AND {none} THEN {column}",  # nothing to set
             f"WHEN length({column}) > {8 * at} OR {slots[-1][1]} != {nan} THEN {full}"]  # fmt: skip
    for k in range(at - 1, 0, -1):  # the last value is empty: cut after the last slot that is not
        whens.append(f"WHEN substr({full}, {8 * k - 7}, 8) != {nan} THEN substr({full}, 1, {8 * k})")
    return f"CASE {' '.join(whens)} ELSE NULL END"


def set_slots(
    conn: Connection, table: sa.Table, scope: tuple[str, int], runs: Sequence[tuple[int, int]],
    numbers: Sequence[tuple[int, Any]], texts: Sequence[tuple[str, Any, Sequence[str]]],
) -> int | None:  # fmt: skip
    """Columns packed into the rows of `table` (ADR 0003 §4: `num` — float64 vectors by slot, `txt` — JSON
    {slot: text}) written without a round trip of the rows: a statement per run of consecutive keys computes
    the new vector and JSON of every row from the old ones and the values bound as blobs.

    `scope` — (column, value) the rows of the table have; `runs` — [(first key, rows)] ascending, their values
    one after another in `numbers` [(slot, float64 array, NaN — empty)] and `texts` [(slot, uint8 codes into
    the labels, 255 — empty, labels)]. The rows of the scope outside the runs get the slots empty. Returns the
    rows written, or None — nothing is done, the caller writes them row by row: not SQLite (PostgreSQL gets its
    statement with the server, Э25), more than SLOT_RUNS runs or SLOT_LABELS labels."""
    import numpy as np

    if conn.dialect.name != "sqlite" or len(runs) > SLOT_RUNS or any(len(t[2]) > SLOT_LABELS for t in texts):
        return None
    if any(not key.replace("_", "").isalnum() for key, _c, _l in texts):
        raise ValueError([key for key, _c, _l in texts])
    numbers = sorted(numbers, key=lambda item: item[0])
    at = "id - :first"  # `cases` — (SQL «the text is empty», SQL of the text) of every text column
    spots = [(slot, f"substr(:n{k}, ({at}) * 8 + 1, 8)") for k, (slot, _v) in enumerate(numbers)]
    cases = [(f"substr(:t{k}, {at} + 1, 1) = x'ff'", f"CASE substr(:t{k}, {at} + 1, 1) "
              + " ".join(f"WHEN x'{code:02x}' THEN :t{k}_{code}" for code in range(len(labels))) + " END"
              if labels else "NULL")  # a column empty in every row: a CASE without a WHEN is not SQL
             for k, (_key, _codes, labels) in enumerate(texts)]  # fmt: skip
    where = f"{scope[0]} = :scope"  # (in the runs — «+»: the rows go by their keys, not through an index of it)
    bound: dict[str, Any] = {"scope": scope[1], "pad": _NAN * (max((s for s, _v in numbers), default=0) + 1)}
    bound.update({f"t{k}_{code}": str(x) for k, t in enumerate(texts) for code, x in enumerate(t[2])})

    def statement(vectors: Sequence[tuple[int, str]], values: Sequence[tuple[str, str]], rows: str,
                  lean: bool = False) -> str:  # fmt: skip
        """`lean` (the rows of the runs) — one number, one text column: the usual row first, with as few
        function calls as it takes (a call is 0.1 µs, the rows are millions); the same values and bytes."""
        sets = [f"num = {_vector_sql('num', vectors)}"] if vectors else []
        if lean and len(vectors) == 1:  # the value read once; a vector that ends before the slot — as it is, or + it
            (slot, value), nan = vectors[0], f"x'{_NAN.hex()}'"
            sets = [f"num = CASE {value} WHEN {nan} THEN CASE WHEN coalesce(length(num), 0) <= {8 * slot} THEN num "
                    f"ELSE {sets[0][6:]} END ELSE CASE WHEN length(num) = {8 * slot} THEN "
                    f"CAST(num || {value} AS BLOB) ELSE {sets[0][6:]} END END"]  # fmt: skip
        if values and lean and len(values) == 1:  # the code read once; `:t0_<code>a`, `…b` — the text as JSON
            key, absent = texts[0][0], f"instr(txt, '\"{texts[0][0]}\":') = 0"
            patch = f"nullif(json_patch(coalesce(txt, '{{}}'), json_object('{key}', %s)), '{{}}')"
            whens = [f"WHEN x'ff' THEN CASE WHEN txt IS NULL OR {absent} THEN txt ELSE {patch % 'NULL'} END"]
            whens += [f"WHEN x'{code:02x}' THEN CASE WHEN txt IS NULL THEN :t0_{code}a "
                      f"WHEN {absent} AND {_FLAT} THEN rtrim(txt, '}}') || :t0_{code}b "
                      f"WHEN {absent} AND txt LIKE '{{_%}}' THEN substr(txt, 1, length(txt) - 1) || :t0_{code}b "
                      f"ELSE {patch % f':t0_{code}'} END" for code in range(len(texts[0][2]))]  # fmt: skip
            sets.append(f"txt = CASE substr(:t0, {at} + 1, 1) {' '.join(whens)} END")
        elif values:
            pairs = ", ".join(f"'{key}', {value}" for (key, _c, _l), (_no, value) in zip(texts, values, strict=True))
            patch = f"nullif(json_patch(coalesce(txt, '{{}}'), json_object({pairs})), '{{}}')"
            if len(values) == 1:  # a key the row has not: no JSON is parsed — left as it is, or added at the end
                key, (empty, value) = texts[0][0], values[0]
                absent, pair = f"instr(txt, '\"{key}\":') = 0", f"'\"{key}\": ' || json_quote({value}) || '}}'"
                patch = (f"CASE WHEN {empty} THEN CASE WHEN txt IS NULL OR {absent} THEN txt ELSE {patch} END "
                         f"WHEN txt IS NULL THEN '{{' || {pair} WHEN {absent} AND txt LIKE '{{_%}}' "
                         f"THEN substr(txt, 1, length(txt) - 1) || ', ' || {pair} ELSE {patch} END")  # fmt: skip
            sets.append(f"txt = {patch}")
        return f"UPDATE {table.name} SET {', '.join(sets)} WHERE {where} AND {rows}"

    done = 0
    cur = conn.connection.driver_connection.cursor()  # type: ignore[union-attr]
    try:
        sql = statement(spots, cases, "id BETWEEN :first AND :last", True)
        sql = sql.replace(f" WHERE {where}", f" WHERE +{where}")
        if len(texts) == 1:  # the pairs of the lean statement, quoted by the database: {"1": "ore"} and , "1": "ore"}
            for code, label in enumerate(texts[0][2]):
                pair = f'"{texts[0][0]}": ' + str(cur.execute("SELECT json_quote(?)", (str(label),)).fetchone()[0])
                bound.update({f"t0_{code}a": "{" + pair + "}", f"t0_{code}b": ", " + pair + "}"})
        start = 0
        for first, count in runs if numbers or texts else ():
            for a in range(0, count, SLOT_ROWS):
                b = min(count, a + SLOT_ROWS)
                more: dict[str, Any] = {"first": first + a, "last": first + b - 1}
                for k, (_slot, values) in enumerate(numbers):
                    part = np.array(values[start + a : start + b], dtype="<f8")
                    part[np.isnan(part)] = np.nan  # one NaN: the statement compares bytes
                    more[f"n{k}"] = part.tobytes()
                for k, (_key, codes, _labels) in enumerate(texts):
                    more[f"t{k}"] = np.ascontiguousarray(codes[start + a : start + b], dtype=np.uint8).tobytes()
                done += cur.execute(sql, {**bound, **more}).rowcount
            start += count
        outside = " AND ".join(f"NOT (id BETWEEN {int(f)} AND {int(f) + int(c) - 1})" for f, c in runs) or "1"
        sql = statement([(slot, f"x'{_NAN.hex()}'") for slot, _sql in spots], [("1", "NULL")] * len(texts), outside)
        done += cur.execute(sql, bound).rowcount if numbers or texts else 0
    except Exception as exc:
        dbapi = conn.dialect.dbapi
        if dbapi is None or not isinstance(exc, dbapi.Error):
            raise
        raise sa.exc.DBAPIError.instance("UPDATE (set_slots)", None, exc, dbapi.Error, hide_parameters=True) from exc
    finally:
        cur.close()
    return done
