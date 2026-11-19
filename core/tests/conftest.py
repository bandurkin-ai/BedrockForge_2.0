from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# the quick worker of a test core does not warm the kernels of the simplified copies of wireframes: compiling
# for seconds there would hold its jobs (cancels, ticks) the tests time; `test_jobs` warms them directly
os.environ.setdefault("BF_NO_WARMUP", "1")

from bf_core.api.app import CoreState, create_app

TOKEN = "test-token-0123456789"


@pytest.fixture()
def state() -> CoreState:
    return CoreState(token=TOKEN)


@pytest.fixture()
def client(state: CoreState) -> TestClient:
    return TestClient(create_app(state), base_url="http://127.0.0.1:5555")


def _db_urls() -> list[object]:
    pg = os.environ.get("BF_TEST_PG")
    return [
        "sqlite",
        pytest.param(pg or "pg", id="pg", marks=pytest.mark.skipif(not pg, reason="BF_TEST_PG is not set")),
    ]


@pytest.fixture(params=_db_urls())
def db_url(request: pytest.FixtureRequest) -> str:
    """Database dialect of a test: SQLite always, PostgreSQL 16 when BF_TEST_PG is set (CI core-pg)."""
    return str(request.param)


@pytest.fixture()
def pclient(tmp_path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """A core with its own profile and projects folder (closed at the end: locks and engines released)."""
    monkeypatch.setenv("BF_LOCAL_DIR", str(tmp_path / "local"))
    st = CoreState(token=TOKEN, projects_dir=str(tmp_path / "Проекты"), data_dir=str(tmp_path / "roaming"))
    with TestClient(create_app(st), base_url="http://127.0.0.1:5555", headers={"X-BF-Token": TOKEN}) as c:
        c.core = st  # type: ignore[attr-defined]
        yield c


@pytest.fixture(autouse=True, scope="module")
def _drop_caches_after_module():  # type: ignore[no-untyped-def]
    """The caches of the API process (exact geometry 768 MB, contours, layouts, trajectories) live as long as the
    process; in one pytest process of the whole suite they piled up from module to module — on the Windows runner
    the memory ran out at the end of `-m windows` (PR #24). Each module starts with them empty."""
    yield
    import gc
    import sys

    for name, fn in (
        ("bf_core.wireframes.exact", "forget"),
        ("bf_core.viz.meshes", "forget"),
        ("bf_core.sections.contours", "clear_caches"),
    ):
        mod = sys.modules.get(name)
        if mod is not None:
            getattr(mod, fn)()
    for name, attrs in (
        ("bf_core.drillholes.trajectory", ("_cache",)),
        ("bf_core.viz.lines", ("_cache",)),
        ("bf_core.viz.holes", ("_cache",)),
        ("bf_core.wireframes.lod_job", ("_asked",)),
    ):
        mod = sys.modules.get(name)
        for attr in attrs if mod is not None else ():
            cache = getattr(mod, attr, None)
            if hasattr(cache, "clear"):
                cache.clear()
    gc.collect()
