"""The only place with dialect-dependent code (ADR 0003 §6): portable types, SQLite PRAGMAs,
write transactions, job claim, JSON access. SQLite (desktop project / instance) and PostgreSQL 16 (server, Э25).
"""

from __future__ import annotations

import threading
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from itertools import chain, repeat
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

# --- portable types ---------------------------------------------------------------------------

#: Row key: INTEGER PRIMARY KEY (rowid alias, autoincrement) on SQLite, BIGINT identity on PostgreSQL.
PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
#: A reference to a row key (same storage as PK).
REF = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
#: None is SQL NULL, not the JSON text "null" (smaller rows, and «no value» stays testable with IS NULL).
JSONType = sa.JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


class UtcDateTime(sa.types.TypeDecorator[datetime]):
    """Always UTC; set by the application (no server defaults). Naive on SQLite, TIMESTAMPTZ on PostgreSQL."""

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime: use bf_core.db.dialect.utcnow()")
        value = value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def name_key(name: str) -> str:
    """Uniqueness and search key of a name: NFC + casefold (SQLite NOCASE/LIKE do not know Cyrillic)."""
    return unicodedata.normalize("NFC", name.strip()).casefold()


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# --- engines --------------------------------------------------------------------------------


def sqlite_url(path: str, read_only: bool = False) -> str:
    from pathlib import Path

    uri = Path(path).resolve().as_uri().replace("file://", "file:", 1)
    return f"sqlite+pysqlite:///{uri}?mode=ro&uri=true" if read_only else f"sqlite+pysqlite:///{uri}?uri=true"


def create_engine(url: str, *, schema: str | None = None) -> Engine:
    """Engine with the dialect setup of ADR 0002 §5: WAL, synchronous=NORMAL, foreign keys, busy timeout.

    SQLite connections are not pooled across threads (NullPool): each request opens its own, cheap for files.
    `schema` (PostgreSQL) maps the project tables to `p_<id>` (server mode).
    """
    if url.startswith("sqlite"):
        # hide_parameters: the values of a failed statement (a job's archive password among them) never reach
        # an error text, a job log or the operation log (ТЗ 7.9).
        engine = sa.create_engine(
            url, poolclass=NullPool, connect_args={"check_same_thread": False, "timeout": 30.0}, hide_parameters=True
        )

        @sa.event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn: Any, _record: Any) -> None:
            dbapi_conn.isolation_level = None  # transactions are begun explicitly (BEGIN / BEGIN IMMEDIATE)
            cur = dbapi_conn.cursor()
            # the wait first: `journal_mode` takes a lock too, and a writer of another connection (the demo being
            # made, a job) may hold one longer than the 5 s of the driver (Windows CI: «database is locked»)
            cur.execute("PRAGMA busy_timeout=30000")
            ro = "mode=ro" in url
            if not ro:
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            # Deleted content is overwritten: the archive password a job held is not left in free pages.
            cur.execute("PRAGMA secure_delete=ON")
            cur.close()

        @sa.event.listens_for(engine, "begin")
        def _on_begin(conn: Connection) -> None:
            mode = conn.info.pop("bf_begin", "BEGIN")
            conn.exec_driver_sql(mode)

        return engine
    engine = sa.create_engine(url, pool_pre_ping=True, hide_parameters=True)
    if schema:
        engine = engine.execution_options(schema_translate_map={None: schema})
    return engine


def is_sqlite(bind: Engine | Connection) -> bool:
    return bind.dialect.name == "sqlite"


@contextmanager
def write_tx(engine: Engine) -> Iterator[Connection]:
    """A write transaction: `BEGIN IMMEDIATE` on SQLite (the writer lock is taken up front, no upgrade deadlock)."""
    with engine.connect() as conn:
        if is_sqlite(engine):
            conn.info["bf_begin"] = "BEGIN IMMEDIATE"
        with conn.begin():
            yield conn


#: SQLite, the connection that writes the portions of a big table (`write_portions`): it keeps so much of
#: pages (a portion whole), and after every so many portions it waits until the WAL is folded whole, so that
#: the next portion starts the WAL anew — the WAL stays within those portions (some 150 MB).
BULK_CACHE_KB = 65536
BULK_WAL_PORTIONS = 16


class _WalFolder:
    """A thread that folds the WAL of a SQLite file into it (PASSIVE checkpoints on a connection of its own)
    when asked: the writer goes on with the next portion while the WAL and the file are synced — a quarter of
    the time of a table of millions of rows. PASSIVE takes what no reader and no writer needs and never makes
    them wait. A failure (the file is busy, the engine is gone) ends the folding: the data are in the WAL, and
    the connections fold it themselves, as ever."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._cond = threading.Condition()
        self._asked = 0  # commits asked to be folded
        self._done = 0  # … and folded (or given up)
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="bf-wal-fold", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            with _driver(self._engine) as raw:
                while True:
                    with self._cond:
                        self._cond.wait_for(lambda: self._stop or self._asked > self._done)
                        asked, stop = self._asked, self._stop
                    if asked > self._done:
                        raw.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchall()
                    with self._cond:
                        self._done = asked
                        self._cond.notify_all()
                    if stop:
                        return
        except Exception:  # noqa: BLE001, S110 - best effort (see the class docstring)
            pass
        finally:
            with self._cond:
                self._stop = True
                self._done = float("inf")  # type: ignore[assignment]
                self._cond.notify_all()

    def fold(self, wait: bool = False) -> None:
        """What is committed by now is to be folded; `wait` — until it is (a minute at most)."""
        with self._cond:
            self._asked += 1
            asked = self._asked
            self._cond.notify_all()
            if wait:
                self._cond.wait_for(lambda: self._done >= asked, timeout=60.0)

    def close(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        self._thread.join()


@contextmanager
def write_portions(engine: Engine) -> Iterator[Callable[[], AbstractContextManager[Connection]]]:
    """Many short write transactions in a row (the portions of a table built aside, ADR 0003 §4):

        with write_portions(engine) as tx:
            for portion in portions:
                with tx() as conn:
                    bulk_insert_columns(conn, …)

    Each `tx()` is a transaction of its own, as `write_tx`: readers and writers get in between, a failure or a
    cancel leaves the portions committed so far; the foreign keys are checked as on any connection.

    SQLite: the portions share one connection (every `write_tx` opens and closes its own, and closing the last
    connection of a file folds the whole WAL into it) that keeps BULK_CACHE_KB of pages and does not fold the
    WAL at its commits: a thread does it meanwhile (`_WalFolder`), asked after every commit; after every
    BULK_WAL_PORTIONS portions the writer waits for it, and the WAL starts anew. The thread ends with the block,
    after a last folding; the settings die with the connection."""
    with engine.connect() as conn:
        folder = None
        if is_sqlite(engine):
            raw = conn.connection.driver_connection
            assert raw is not None
            raw.execute("PRAGMA wal_autocheckpoint=0")
            raw.execute(f"PRAGMA cache_size=-{int(BULK_CACHE_KB)}")
            folder = _WalFolder(engine)
        done = 0

        @contextmanager
        def tx() -> Iterator[Connection]:
            nonlocal done
            if folder is not None:
                conn.info["bf_begin"] = "BEGIN IMMEDIATE"
            with conn.begin():
                yield conn
            done += 1
            if folder is not None:
                folder.fold(wait=done % BULK_WAL_PORTIONS == 0)

        try:
            yield tx
        finally:
            if folder is not None:
                folder.fold()
                folder.close()


@contextmanager
def read_tx(engine: Engine) -> Iterator[Connection]:
    with engine.connect() as conn, conn.begin():
        yield conn


@contextmanager
def _driver(engine: Engine) -> Iterator[Any]:
    """The sqlite3 connection itself, in autocommit (`isolation_level = None`): for statements that may not
    run inside a transaction (VACUUM, wal_checkpoint) — SQLAlchemy would begin one implicitly."""
    with engine.connect() as conn:
        raw = conn.connection.driver_connection
        assert raw is not None
        yield raw


def quick_check(engine: Engine) -> str:
    """`PRAGMA quick_check` on SQLite ("ok" when sound); PostgreSQL has no equivalent — "ok"."""
    if not is_sqlite(engine):
        return "ok"
    try:
        with _driver(engine) as raw:
            rows = raw.execute("PRAGMA quick_check").fetchall()
    except sa.exc.DBAPIError as exc:
        return str(exc.orig)
    except Exception as exc:  # noqa: BLE001 - sqlite3.DatabaseError on a damaged header
        return str(exc)
    return "; ".join(str(r[0]) for r in rows[:5])


def checkpoint(engine: Engine) -> None:
    """Fold the WAL into the database file (on close and before a backup)."""
    if is_sqlite(engine):
        with _driver(engine) as raw:
            raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def vacuum_into(engine: Engine, target: str) -> None:
    """A consistent copy of a live SQLite database (backups, `.bfggis` export)."""
    with _driver(engine) as raw:
        raw.execute("VACUUM INTO ?", (target,))


@contextmanager
def foreign_keys_off(conn: Connection) -> Iterator[None]:
    """Alembic batch mode on SQLite recreates tables: FK checks are off during it and verified after.

    The PRAGMA works only outside a transaction, so it goes to the driver connection directly
    (autocommit: `isolation_level = None`), before the migration transaction begins.
    """
    if conn.dialect.name != "sqlite":
        yield
        return
    raw = conn.connection.driver_connection
    assert raw is not None
    raw.execute("PRAGMA foreign_keys=OFF")
    try:
        yield
        bad = raw.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"foreign key check failed after migration: {bad[:5]}")
    finally:
        raw.execute("PRAGMA foreign_keys=ON")


# --- jobs -------------------------------------------------------------------------------------


def claim_job(conn: Connection, jobs: sa.Table, queue: str, worker: str, now: datetime) -> dict[str, Any] | None:
    """Atomically take the next queued job of `queue` (ADR 0002 §4). The caller holds a write transaction
    (`write_tx`: BEGIN IMMEDIATE on SQLite — one writer, so select + update cannot interleave; PostgreSQL
    locks the row with FOR UPDATE SKIP LOCKED). The job secret (archive password) is read and cleared here.
    Returns the job row as it is after the claim, with the secret under "secret".
    """
    pick = (
        sa.select(jobs.c.id, jobs.c.secret)
        .where(jobs.c.status == "queued", jobs.c.queue == queue)
        .order_by(jobs.c.priority, jobs.c.id)
        .limit(1)
    )
    if conn.dialect.name == "postgresql":
        pick = pick.with_for_update(skip_locked=True)
    found = conn.execute(pick).first()
    if found is None:
        return None
    conn.execute(
        sa.update(jobs)
        .where(jobs.c.id == found.id)
        .values(
            status="running",
            attempts=jobs.c.attempts + 1,
            started_at=now,
            heartbeat_at=now,
            worker=worker,
            cancel_requested=False,
            secret=None,
        )
    )
    row = conn.execute(sa.select(jobs).where(jobs.c.id == found.id)).one()
    out = dict(row._mapping)
    out["secret"] = found.secret
    return out


def json_text(column: sa.ColumnElement[Any], key: str) -> sa.ColumnElement[str]:
    """`column ->> key` on both dialects (filters on JSON fields are rare: such fields become columns)."""
    return column[key].as_string()


def is_integrity_error(exc: BaseException) -> bool:
    """Constraint violation on both drivers: pg8000 reports SQLSTATE 23xxx as ProgrammingError."""
    if isinstance(exc, sa.exc.IntegrityError):
        return True
    orig = getattr(exc, "orig", None)
    args = getattr(orig, "args", ())
    code = args[0].get("C") if args and isinstance(args[0], dict) else None
    return isinstance(code, str) and code.startswith("23")


def bulk_insert(conn: Connection, table: sa.Table, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    """Many rows through the driver's executemany, past SQLAlchemy's per-row parameter processing (millions of
    interval rows, ADR 0003 §4). Values must already be in driver form: float/int/str/bytes/None; JSON as text.
    PostgreSQL bulk loads through COPY come with the server (Э25)."""
    if not rows:
        return
    placeholder = "?" if conn.dialect.name == "sqlite" else "%s"
    sql = f"INSERT INTO {table.name} ({', '.join(columns)}) VALUES ({', '.join([placeholder] * len(columns))})"
    conn.exec_driver_sql(sql, list(map(tuple, rows)))


#: Bound values of one SQLite statement of many rows: under the limit of every build (999 before 3.32), and
#: where the statement is fastest (some hundred rows; longer ones gain nothing).
SQLITE_ROW_VARS = 900


def bulk_insert_columns(
    conn: Connection, table: sa.Table, fixed: Mapping[str, int | None], columns: Mapping[str, Sequence[Any]]
) -> tuple[int, int] | None:
    """Many rows given by columns (interval tables of millions of rows, ADR 0003 §4): `columns` — name → the
    values of every row (lists of equal length, in driver form as for `bulk_insert`), `fixed` — columns with
    one value in all the rows (an integer or NULL).

    SQLite: statements of many rows each (`VALUES (…), (…), …`) on the driver cursor, inside the transaction
    of `conn` — one statement run for some hundred rows instead of one per row, the fixed values as literals;
    a failure comes as the SQLAlchemy error the same statement would give. PostgreSQL: `bulk_insert`.

    Returns (first, last) row key when the rows got consecutive keys in the order given — SQLite, when `conn`
    holds the write lock (`write_tx`, `write_portions`) and the key is the rowid; None — the keys are not
    known (PostgreSQL: a sequence shared with other writers; no rows)."""
    lists = list(columns.values())
    n = len(lists[0]) if lists else 0
    if not n:
        return None
    names = [*fixed, *columns]
    if conn.dialect.name != "sqlite":
        consts = [repeat(v, n) for v in fixed.values()]
        bulk_insert(conn, table, names, list(zip(*consts, *lists, strict=True)))
        return None
    literals = ["NULL" if v is None else str(int(v)) for v in fixed.values()]
    row = f"({', '.join([*literals, *['?'] * len(lists)])})"
    head = f"INSERT INTO {table.name} ({', '.join(names)}) VALUES "
    width = len(lists)
    per = max(1, SQLITE_ROW_VARS // width)
    flat = list(chain.from_iterable(zip(*lists, strict=True)))
    full = n - n % per
    sql = head + ", ".join([row] * per)
    step = per * width
    cur = conn.connection.driver_connection.cursor()  # type: ignore[union-attr]
    try:
        first = int(cur.execute(f"SELECT coalesce(max(rowid), 0) + 1 FROM {table.name}").fetchone()[0])
        for k in range(0, full * width, step):
            cur.execute(sql, flat[k : k + step])
        if full < n:
            sql = head + ", ".join([row] * (n - full))
            cur.execute(sql, flat[full * width :])
        last = cur.lastrowid
    except Exception as exc:
        dbapi = conn.dialect.dbapi
        if dbapi is None or not isinstance(exc, dbapi.Error):
            raise
        raise sa.exc.DBAPIError.instance(head + row, None, exc, dbapi.Error, hide_parameters=True) from exc
    finally:
        cur.close()
    return (first, int(last)) if last is not None and int(last) - first + 1 == n else None


def bulk_update(
    conn: Connection, table: sa.Table, columns: Sequence[str], key: str, rows: Sequence[Sequence[Any]]
) -> None:
    """Many rows `UPDATE table SET columns… WHERE key = …` through the driver's executemany; each row is the
    new values followed by the key. Values in driver form, as for `bulk_insert`."""
    if not rows:
        return
    placeholder = "?" if conn.dialect.name == "sqlite" else "%s"
    sets = ", ".join(f"{c} = {placeholder}" for c in columns)
    conn.exec_driver_sql(f"UPDATE {table.name} SET {sets} WHERE {key} = {placeholder}", list(map(tuple, rows)))


#: `set_slots`: rows of one statement (its blobs hold 8 bytes a row and a column), runs of consecutive keys and
#: labels of a text column it takes (a CASE of that many branches a row) — past them the caller writes the rows.
SLOT_ROWS, SLOT_RUNS, SLOT_LABELS = 1_000_000, 64, 32
_NAN = b"\x00\x00\x00\x00\x00\x00\xf8\x7f"  # NaN of `dh_rows.num`: float64, little-endian


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
    whens += [f"WHEN substr({full}, {8 * k - 7}, 8) != {nan} THEN substr({full}, 1, {8 * k})" for k in range(at - 1, 0, -1)]
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
    rows written, or None when nothing was done and the caller writes them row by row: not SQLite, more than
    SLOT_RUNS runs or SLOT_LABELS labels. PostgreSQL gets its own statement with the server (Э25)."""
    import numpy as np

    if conn.dialect.name != "sqlite" or len(runs) > SLOT_RUNS or any(len(t[2]) > SLOT_LABELS for t in texts):
        return None
    if any(not key.replace("_", "").isalnum() for key, _c, _l in texts):
        raise ValueError([key for key, _c, _l in texts])
    numbers = sorted(numbers, key=lambda item: item[0])
    at = "id - :first"
    spots = [(slot, f"substr(:n{k}, ({at}) * 8 + 1, 8)") for k, (slot, _v) in enumerate(numbers)]
    cases = [f"CASE substr(:t{k}, {at} + 1, 1) "
             + " ".join(f"WHEN x'{code:02x}' THEN :t{k}_{code}" for code in range(len(labels))) + " END"
             for k, (_key, _codes, labels) in enumerate(texts)]  # fmt: skip
    where = f"{scope[0]} = :scope"  # (in the runs — «+»: the rows go by their keys, not through an index of it)
    bound: dict[str, Any] = {"scope": scope[1], "pad": _NAN * (max((s for s, _v in numbers), default=0) + 1)}
    bound.update({f"t{k}_{code}": str(x) for k, (_key, _c, labels) in enumerate(texts) for code, x in enumerate(labels)})

    def statement(vectors: Sequence[tuple[int, str]], values: Sequence[str], rows: str) -> str:
        sets = [f"num = {_vector_sql('num', vectors)}"] if vectors else []
        if values:
            pairs = ", ".join(f"'{key}', {value}" for (key, _c, _l), value in zip(texts, values, strict=True))
            patch = f"nullif(json_patch(coalesce(txt, '{{}}'), json_object({pairs})), '{{}}')"
            if len(values) == 1:  # a key the row has not: no JSON is parsed — left as it is, or added at the end
                (key, _c, _l), (value,) = texts[0], values
                absent, pair = f"instr(txt, '\"{key}\":') = 0", f"'\"{key}\": ' || json_quote({value}) || '}}'"
                patch = (f"CASE WHEN {value} IS NULL AND (txt IS NULL OR {absent}) THEN txt "
                         f"WHEN {value} IS NULL THEN {patch} WHEN txt IS NULL THEN '{{' || {pair} "
                         f"WHEN {absent} AND length(txt) > 2 AND substr(txt, -1) = '}}' "
                         f"THEN substr(txt, 1, length(txt) - 1) || ', ' || {pair} ELSE {patch} END")  # fmt: skip
            sets.append(f"txt = {patch}")
        return f"UPDATE {table.name} SET {', '.join(sets)} WHERE {where} AND {rows}"

    done = 0
    cur = conn.connection.driver_connection.cursor()  # type: ignore[union-attr]
    try:
        sql = statement(spots, cases, "id BETWEEN :first AND :last").replace(f" WHERE {where}", f" WHERE +{where}")
        start = 0
        for first, count in runs:
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
        sql = statement([(slot, f"x'{_NAN.hex()}'") for slot, _sql in spots], ["NULL"] * len(texts), outside)
        done += cur.execute(sql, bound).rowcount
    except Exception as exc:
        dbapi = conn.dialect.dbapi
        if dbapi is None or not isinstance(exc, dbapi.Error):
            raise
        raise sa.exc.DBAPIError.instance("UPDATE (set_slots)", None, exc, dbapi.Error, hide_parameters=True) from exc
    finally:
        cur.close()
    return done


def json_field_sql(conn: Connection, column: str, key: str) -> str:
    """SQL text of `column ->> key` for a raw select (`iter_driver_rows`); `key` must be a plain token."""
    if not key.replace("_", "").isalnum():
        raise ValueError(key)
    if conn.dialect.name == "sqlite":
        return f"json_extract({column}, '$.\"{key}\"')"
    return f"({column} ->> '{key}')"


def iter_driver_rows(conn: Connection, sql: str, params: Sequence[Any], size: int = 200_000) -> Iterator[list[Any]]:
    """Rows of a raw select in portions, as driver tuples — no per-row SQLAlchemy work (snapshots of 5 million
    rows). `sql` uses `?` placeholders; they become `%s` on PostgreSQL."""
    if conn.dialect.name != "sqlite":
        sql = sql.replace("?", "%s")
    cur = conn.connection.driver_connection.cursor()  # type: ignore[union-attr]
    try:
        cur.execute(sql, tuple(params))
        while True:
            part = cur.fetchmany(size)
            if not part:
                return
            yield part
    finally:
        cur.close()


# --- SQLite files of other programs (a TRIDB of Micromine: formats/tridb.py, tridb_write.py) --------------


def open_sqlite_file(path: Any) -> Any:
    """A database file that is not ours, to be read (a `sqlite3` connection in autocommit mode): read-only and
    immutable — SQLite neither locks the file nor writes a journal next to it — with what SQLite advises for a
    file that is not trusted: its schema runs no functions of ours, the cells of a page are checked, no mmap."""
    import sqlite3
    from pathlib import Path
    from urllib.parse import quote

    p = str(Path(path).resolve()).replace("\\", "/")
    p = p if p.startswith("/") else "/" + p  # C:/dir → /C:/dir
    uri = "file:" + ("//" if p.startswith("//") else "") + quote(p, safe="/:") + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        for pragma in ("query_only = ON", "trusted_schema = OFF", "cell_size_check = ON", "mmap_size = 0"):
            conn.execute(f"PRAGMA {pragma}")
    except BaseException:
        conn.close()
        raise
    return conn


def new_sqlite_file(path: Any, *, encoding: str, page_size: int = 4096) -> Any:
    """An empty database file for the writer of a format (a `sqlite3` connection in autocommit mode) with the
    text encoding and the page size of the format. The journal is kept in memory and nothing is synced: the
    writer fills a temporary file and renames it, so a file half written is never the result."""
    import sqlite3

    conn = sqlite3.connect(path, isolation_level=None)
    try:
        for pragma in (f"page_size = {int(page_size)}", f"encoding = '{encoding}'", "journal_mode = MEMORY"):
            conn.execute(f"PRAGMA {pragma}")
        conn.execute("PRAGMA synchronous = OFF")
    except BaseException:
        conn.close()
        raise
    return conn
