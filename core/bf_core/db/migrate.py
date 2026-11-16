"""Alembic, run from code (ADR 0003 §6): two chains — `project` (project.sqlite / p_<id>) and `instance`.

On open: an older database is upgraded (the caller makes a backup first), a newer one is refused
(«обновите программу»). Revision files ship as data next to this module (bf-core.spec).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection, Engine

from .dialect import foreign_keys_off, is_sqlite
from .schema import instance_md, project_md

Chain = Literal["project", "instance"]
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


class SchemaTooNew(Exception):
    """The database was written by a newer program."""


def _config(chain: Chain, conn: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS / chain))
    cfg.attributes["chain"] = chain
    if conn is not None:
        cfg.attributes["connection"] = conn
    return cfg


def head(chain: Chain) -> str:
    rev = ScriptDirectory.from_config(_config(chain)).get_current_head()
    assert rev is not None
    return rev


def known_revisions(chain: Chain) -> set[str]:
    return {s.revision for s in ScriptDirectory.from_config(_config(chain)).walk_revisions()}


def current(conn: Connection) -> str | None:
    return MigrationContext.configure(conn).get_current_revision()


def state(engine: Engine, chain: Chain) -> Literal["empty", "old", "current", "newer"]:
    with engine.connect() as conn:
        rev = current(conn)
    if rev is None:
        return "empty"
    if rev == head(chain):
        return "current"
    return "old" if rev in known_revisions(chain) else "newer"


def upgrade(engine: Engine, chain: Chain, target: str = "head") -> None:
    if state(engine, chain) == "newer":
        raise SchemaTooNew(chain)
    with engine.connect() as conn:
        command.upgrade(_config(chain, conn), target)


def downgrade(engine: Engine, chain: Chain, target: str = "base") -> None:
    with engine.connect() as conn:
        command.downgrade(_config(chain, conn), target)


def run_migrations(context: Any, chain: Chain) -> None:
    """Body of env.py: one transaction per run; SQLite batch mode with FK checks after."""
    conn: Connection = context.config.attributes["connection"]
    metadata = project_md if chain == "project" else instance_md
    context.configure(
        connection=conn,
        target_metadata=metadata,
        render_as_batch=is_sqlite(conn),
        compare_type=True,
        transaction_per_migration=False,
    )
    with foreign_keys_off(conn):
        with context.begin_transaction():
            context.run_migrations()
    conn.commit()
