"""Shared fixtures. DB tests run against a real PostgreSQL (TEST_DATABASE_URL)
and are skipped when it is unreachable. The schema is created by Alembic, so
every run also checks the migrations."""

import dataclasses
import os
from collections.abc import Iterator
from pathlib import Path

# Before the app is imported: no job worker threads inside the API under tests.
os.environ.setdefault("JOBS_IN_API", "0")

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app
from app.routers import imports, jobs
from app.storage import blobs

API_ROOT = Path(__file__).resolve().parent.parent
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://geo:geo@localhost:5432/geo_test"
)


@pytest.fixture(scope="session", autouse=True)
def blob_store(tmp_path_factory) -> Path:
    """The object store of the whole run (migrations write into it too)."""
    root = tmp_path_factory.mktemp("store")
    blobs.set_root(root)
    yield root
    blobs.set_root(None)


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(TEST_DB_URL)
    try:
        engine.connect().close()
    except OperationalError:
        pytest.skip("PostgreSQL is not reachable (set TEST_DATABASE_URL)")

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["url"] = TEST_DB_URL
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_client(db_engine, tmp_path: Path, monkeypatch) -> Iterator[TestClient]:
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE project RESTART IDENTITY CASCADE"))
    monkeypatch.setattr(
        imports, "settings", dataclasses.replace(imports.settings, upload_dir=tmp_path / "uploads")
    )

    def _session() -> Iterator[Session]:
        with Session(db_engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = _session
    monkeypatch.setattr(jobs, "session_factory", lambda: Session(db_engine, expire_on_commit=False))
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def run_jobs(db_engine):
    """Runs queued jobs right away, as a worker process would."""
    from app.jobs.worker import run_pending

    return lambda: run_pending(lambda: Session(db_engine, expire_on_commit=False))


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    """API client with uploads in a temporary folder (no database needed)."""
    monkeypatch.setattr(
        imports, "settings", dataclasses.replace(imports.settings, upload_dir=tmp_path / "uploads")
    )
    return TestClient(app)
