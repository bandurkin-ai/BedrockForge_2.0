"""Shared fixtures. DB tests run against a real PostgreSQL (TEST_DATABASE_URL)
and are skipped when it is unreachable. The schema is created by Alembic, so
every run also checks the migrations."""

import dataclasses
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app
from app.routers import imports

API_ROOT = Path(__file__).resolve().parent.parent
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://geo:geo@localhost:5432/geo_test"
)


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
    yield TestClient(app)
    app.dependency_overrides.clear()
