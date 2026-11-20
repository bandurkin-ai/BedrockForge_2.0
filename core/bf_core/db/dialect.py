"""The only place with dialect-dependent code (ADR 0003 §6): portable types, SQLite PRAGMAs,
write transactions, job claim, JSON access. SQLite (desktop project / instance) and PostgreSQL 16 (server, Э25).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
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
) -> None:
    """Many rows given by columns (interval tables of millions of rows, ADR 0003 §4): `columns` — name → the
    values of every row (lists of equal length, in driver form as for `bulk_insert`), `fixed` — columns with
    one value in all the rows (an integer or NULL).

    SQLite: statements of many rows each (`VALUES (…), (…), …`) on the driver cursor, inside the transaction
    of `conn` — one statement run for some hundred rows instead of one per row, the fixed values as literals;
    a failure comes as the SQLAlchemy error the same statement would give. PostgreSQL: `bulk_insert`."""
    lists = list(columns.values())
    n = len(lists[0]) if lists else 0
    if not n:
        return
    names = [*fixed, *columns]
    if conn.dialect.name != "sqlite":
        consts = [repeat(v, n) for v in fixed.values()]
        bulk_insert(conn, table, names, list(zip(*consts, *lists, strict=True)))
        return
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
        for k in range(0, full * width, step):
            cur.execute(sql, flat[k : k + step])
        if full < n:
            sql = head + ", ".join([row] * (n - full))
            cur.execute(sql, flat[full * width :])
    except Exception as exc:
        dbapi = conn.dialect.dbapi
        if dbapi is None or not isinstance(exc, dbapi.Error):
            raise
        raise sa.exc.DBAPIError.instance(head + row, None, exc, dbapi.Error, hide_parameters=True) from exc
    finally:
        cur.close()


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
