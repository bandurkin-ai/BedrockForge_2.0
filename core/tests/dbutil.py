"""Engines for the database tests: a fresh SQLite file, or a fresh PostgreSQL schema (BF_TEST_PG)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlalchemy as sa

from bf_core.db import dialect

PG_SCHEMA = "bf_test"


def make_engine(url: str, tmp_path: Path) -> sa.Engine:
    if url == "sqlite":
        return dialect.create_engine(dialect.sqlite_url(str(tmp_path / "project.sqlite")))
    admin = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f"DROP SCHEMA IF EXISTS {PG_SCHEMA} CASCADE"))
        conn.execute(sa.text(f"CREATE SCHEMA {PG_SCHEMA}"))
    admin.dispose()
    engine = dialect.create_engine(url)

    @sa.event.listens_for(engine, "connect")
    def _search_path(dbapi_conn: Any, _rec: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute(f"SET search_path TO {PG_SCHEMA}")
        cur.close()
        dbapi_conn.commit()

    return engine
