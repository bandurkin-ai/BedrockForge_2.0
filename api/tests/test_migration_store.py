"""Migration 0005 moves geometry and images of existing projects from bytea into the file store and back."""

import os

import numpy as np
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.storage import blobs
from tests.conftest import API_ROOT, TEST_DB_URL


@pytest.fixture
def scratch_db():
    url = make_url(os.environ.get("TEST_DATABASE_URL", TEST_DB_URL))
    name = f"{url.database}_migration"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            c.execute(text(f'CREATE DATABASE "{name}"'))
    except (OperationalError, ProgrammingError):
        pytest.skip("cannot create a scratch database")
    scratch = url.set(database=name).render_as_string(hide_password=False)
    yield scratch
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _config(url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["url"] = url
    return config


def test_existing_geometry_moves_to_the_store_and_back(scratch_db):
    config = _config(scratch_db)
    command.upgrade(config, "0004")
    engine = create_engine(scratch_db)
    v = np.array([[512000.125, 4501000.5, 300.25], [512010.0, 4501000.5, 300.0], [512000.0, 4501010.75, 301.0]])
    t = np.array([[0, 1, 2]], dtype="<i4")
    offsets = np.array([0, 3], dtype="<i8")
    with engine.begin() as c:
        c.execute(text("INSERT INTO project (id, name) VALUES (1, 'old')"))
        c.execute(text("INSERT INTO dataset (id, project_id, kind, name, meta) VALUES "
                       "(1, 1, 'wireframes', 'w', '{}'), (2, 1, 'lines', 'l', '{}'), (3, 1, 'rasters', 'r', '{}')"))
        c.execute(text("INSERT INTO wireframe (dataset_id, name, color, point_count, triangle_count, extent, stats, vertices, triangles) "
                       "VALUES (1, 'w', '#ffffff', 3, 1, '{}', '{}', :v, :t)"),
                  {"v": v.astype("<f8").tobytes(), "t": t.tobytes()})
        c.execute(text("INSERT INTO line_layer (dataset_id, name, color, line_count, point_count, extent, stats, vertices, offsets, closed) "
                       "VALUES (2, 'l', '#ffffff', 1, 3, '{}', '{}', :v, :o, :c)"),
                  {"v": v.astype("<f8").tobytes(), "o": offsets.tobytes(), "c": b"\x00"})
        c.execute(text("INSERT INTO raster (dataset_id, name, width, height, georef, extent, opacity, image, mime) "
                       "VALUES (3, 'r', 1, 1, '{}', '{}', 1, :i, 'image/png')"), {"i": b"\x89PNG fake"})

    command.upgrade(config, "head")
    with engine.connect() as c:
        w = c.execute(text("SELECT blobs, origin, extent FROM wireframe")).mappings().one()
        line = c.execute(text("SELECT blobs FROM line_layer")).scalar()
        image = c.execute(text("SELECT blobs->>'image' FROM raster")).scalar()
        columns = {r[0] for r in c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'wireframe'"))}
    assert "vertices" not in columns
    assert blobs.read_array(w["blobs"]["vertices"], "<f8", 3).tobytes() == v.astype("<f8").tobytes()
    assert np.array_equal(blobs.read_array(w["blobs"]["triangles"], "<i4", 3), t)
    assert w["extent"]["min"] == v.min(axis=0).tolist() and w["origin"] == [512005.0, 4501006.0, 300.0]
    assert np.array_equal(blobs.read_array(line["render_offsets"], "<u4"), [0, 3])
    assert blobs.read_bytes(image) == b"\x89PNG fake"

    command.downgrade(config, "0004")
    with engine.connect() as c:
        assert c.execute(text("SELECT vertices FROM wireframe")).scalar() == v.astype("<f8").tobytes()
        assert c.execute(text("SELECT image FROM raster")).scalar() == b"\x89PNG fake"
    engine.dispose()
