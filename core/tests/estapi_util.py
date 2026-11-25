"""The fixtures and helpers of the tests of the routes of the estimation (подшаг 18.13): the routers included here
until the lines of the orchestrator (`geostat/router.py`), a project with the synthetic world, the answers read."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from bf_core.projects.service import projects_of
from tests.estapi_world import build, row_body
from tests.test_import_job import API, wait_job

GS = f"{API}/geostat"


@pytest.fixture()
def reg():  # type: ignore[no-untyped-def]
    """The routers of 18.13 in `geostat.router` while the orchestrator's lines are not in (before the app is made)."""
    from bf_core.geostat import router as gr
    from bf_core.geostat.estimate import routes, routes_check

    saved = list(gr.router.routes)
    if not any(getattr(r, "path", "").endswith("/estimations") for r in saved):
        gr.router.include_router(routes.router)
        gr.router.include_router(routes_check.router)
    yield
    gr.router.routes[:] = saved


@pytest.fixture()
def ea(reg, pclient):  # type: ignore[no-untyped-def]  # noqa: ARG001
    """A core with an open project and the world; `.w` — the world, `.project`, `.events` — what was published."""
    assert pclient.post(f"{API}/projects", json={"name": "Оценка"}).status_code == 200
    pclient.project = projects_of(pclient.core).require_current()
    pclient.w = build(pclient.project)
    pclient.events = []
    publish = pclient.core.events.publish

    def spy(name: str, data: Any = None, *a: Any, **kw: Any) -> Any:
        pclient.events.append((name, data))
        return publish(name, data, *a, **kw)

    pclient.core.events.publish = spy
    return pclient


def refused(r: Any, status: int, code: str, param: str | None = None) -> dict[str, Any]:
    body = r.json()
    assert (r.status_code, body.get("code")) == (status, code), (r.status_code, body)
    assert body["message"] and body["hint"] and any("а" <= ch.lower() <= "я" for ch in body["message"]), body
    if param is not None:
        assert body.get("param") == param, body
    return dict(body)


def ok(r: Any) -> Any:
    assert r.status_code == 200, r.text
    return r.json()


def finish(c: Any, started: dict[str, Any], timeout: float = 240) -> dict[str, Any]:
    assert sorted(started) == ["id", "source"], started
    return wait_job(c, started["source"], started["id"], timeout=timeout)  # type: ignore[no-any-return]


def make_row(c: Any, label: str = "окисл", **over: Any) -> dict[str, Any]:
    return ok(c.post(f"{GS}/estimations", json=row_body(c.w, label, **over)))  # type: ignore[no-any-return]


def read_only(c: Any) -> Callable[[], None]:
    c.project.read_only = True
    return lambda: setattr(c.project, "read_only", False)

