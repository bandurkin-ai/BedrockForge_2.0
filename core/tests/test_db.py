"""Database layer on both dialects (ADR 0003): migrations cycle, schema = models, job claim, types."""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from bf_core.db import dialect, migrate, schema
from tests.dbutil import make_engine

pytestmark = pytest.mark.db
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("chain", ["project", "instance"])
def test_migrations_cycle_and_schema_equals_models(db_url: str, tmp_path: Path, chain: migrate.Chain) -> None:
    engine = make_engine(db_url, tmp_path)
    md = schema.project_md if chain == "project" else schema.instance_md
    migrate.upgrade(engine, chain)
    assert migrate.state(engine, chain) == "current"
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), md)
    assert diff == [], diff
    migrate.downgrade(engine, chain, "base")
    with engine.connect() as conn:
        left = set(sa.inspect(conn).get_table_names()) - {"alembic_version"}
    assert left == set()
    migrate.upgrade(engine, chain)
    assert migrate.state(engine, chain) == "current"


def test_newer_database_is_refused(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.text("UPDATE alembic_version SET version_num = '9999'"))
    assert migrate.state(engine, "project") == "newer"
    with pytest.raises(migrate.SchemaTooNew):
        migrate.upgrade(engine, "project")


def _job(conn: sa.Connection, queue: str, priority: int = 100, secret: str | None = None) -> int:
    now = dialect.utcnow()
    res = conn.execute(
        sa.insert(schema.jobs).values(
            queue=queue,
            kind="test",
            title="t",
            status="queued",
            priority=priority,
            params={},
            secret=secret,
            progress=0.0,
            cancel_requested=False,
            attempts=0,
            max_attempts=3,
            created_at=now,
        )
    )
    return int(res.inserted_primary_key[0])


def test_claim_job_order_queue_and_secret(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    with dialect.write_tx(engine) as conn:
        a = _job(conn, "heavy", secret="пароль")
        b = _job(conn, "heavy", priority=10)
        q = _job(conn, "quick")
    now = dialect.utcnow()
    with dialect.write_tx(engine) as conn:
        first = dialect.claim_job(conn, schema.jobs, "heavy", "w1", now)
    assert first is not None and first["id"] == b and first["status"] == "running" and first["attempts"] == 1
    with dialect.write_tx(engine) as conn:
        second = dialect.claim_job(conn, schema.jobs, "heavy", "w1", now)
    assert second is not None and second["id"] == a and second["secret"] == "пароль"
    with dialect.read_tx(engine) as conn:
        stored = conn.execute(sa.select(schema.jobs.c.secret).where(schema.jobs.c.id == a)).scalar_one()
    assert stored is None  # the password is gone from the table once a worker took it
    with dialect.write_tx(engine) as conn:
        assert dialect.claim_job(conn, schema.jobs, "heavy", "w1", now) is None
        quick = dialect.claim_job(conn, schema.jobs, "quick", "w2", now)
    assert quick is not None and quick["id"] == q


def test_utc_datetime_roundtrip_and_naive_rejected(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    moment = datetime(2026, 11, 16, 12, 0, tzinfo=UTC) + timedelta(microseconds=5)
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.insert(schema.blobs).values(sha256="a" * 64, size=1, media_type="x", created_at=moment))
    with dialect.read_tx(engine) as conn:
        got = conn.execute(sa.select(schema.blobs.c.created_at)).scalar_one()
    assert got == moment and got.tzinfo is not None
    with pytest.raises(sa.exc.StatementError), dialect.write_tx(engine) as conn:
        conn.execute(
            sa.insert(schema.blobs).values(sha256="b" * 64, size=1, media_type="x", created_at=datetime(2026, 1, 1))
        )


def test_object_refs_restrict_and_cascade(db_url: str, tmp_path: Path) -> None:
    engine = make_engine(db_url, tmp_path)
    migrate.upgrade(engine, "project")
    now = dialect.utcnow()
    vals = dict(
        kind="k",
        name="n",
        name_key="n",
        sort_key=0.0,
        state="ready",
        version=1,
        meta={},
        created_at=now,
        updated_at=now,
    )
    with dialect.write_tx(engine) as conn:
        src = conn.execute(sa.insert(schema.objects).values(**vals)).inserted_primary_key[0]
        dst = conn.execute(sa.insert(schema.objects).values(**vals)).inserted_primary_key[0]
        conn.execute(sa.insert(schema.object_refs).values(src_id=src, dst_id=dst, role="model"))
    with pytest.raises(sa.exc.DBAPIError) as err, dialect.write_tx(engine) as conn:
        conn.execute(sa.delete(schema.objects).where(schema.objects.c.id == dst))
    assert dialect.is_integrity_error(err.value)
    with dialect.write_tx(engine) as conn:
        conn.execute(sa.delete(schema.objects).where(schema.objects.c.id == src))
        assert conn.execute(sa.select(sa.func.count()).select_from(schema.object_refs)).scalar_one() == 0


def test_name_key_is_cyrillic_aware() -> None:
    assert dialect.name_key("  Участок СЕВЕРНЫЙ ") == dialect.name_key("участок северный")
    assert dialect.name_key("Ё") != dialect.name_key("Е")


@pytest.mark.parametrize("pattern", [r"sqlalchemy\.dialects", r"PRAGMA", r"json_extract", r"->>"])
def test_dialect_code_only_in_dialect_py(pattern: str) -> None:
    out = subprocess.run(
        ["git", "grep", "-n", "-E", pattern, "--", "core/bf_core"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    hits = [line for line in out.splitlines() if not line.startswith("core/bf_core/db/dialect.py:")]
    assert not hits, hits


def test_blob_owner_kinds_are_known() -> None:
    assert all(re.fullmatch(r"[a-z_]+", k) for k in schema.BLOB_OWNER_KINDS)
