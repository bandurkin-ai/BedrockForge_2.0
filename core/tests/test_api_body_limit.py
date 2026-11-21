"""The bodies of the routes of charts and statistics (13.36, `api/body_limit.py`): more than 8 МБ — 413, nested
deeper than 64 levels — 422 before parsing, both `{code, message, hint}` in Russian; others pass as they were."""

from __future__ import annotations

from typing import Any

import pytest

from bf_core.api import body_limit as bl
from tests.test_import_job import API


@pytest.fixture()
def demo(pclient):  # type: ignore[no-untyped-def]
    assert pclient.post(f"{API}/projects/demo").status_code == 200
    return pclient, pclient.get(f"{API}/drillholes/sets").json()["sets"][0]["id"]


def post(c: Any, url: str, body: bytes, **headers: str) -> Any:
    return c.post(url, content=body, headers={"content-type": "application/json", **headers})


def test_a_body_over_8_mb_is_413(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    pad = b'{"v": 1, "set_id": %d, "pad": "' % set_id + b"x" * bl.BODY_LIMIT + b'"}'
    for url in (f"{API}/stats/scatter", f"{API}/charts", f"{API}/stats/selection"):
        r = post(c, url, pad)
        assert r.status_code == 413, (url, r.text)
        got = r.json()
        assert got["code"] == "request_too_large" and "8 МБ" in got["message"] and got["hint"]

    def parts() -> Any:  # no Content-Length: counted as it comes
        yield b'{"v": 1, "pad": "'
        for _ in range(9):
            yield b"x" * (1024 * 1024)
        yield b'"}'

    r = c.post(f"{API}/stats/scatter", content=parts(), headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "request_too_large"
    # 200 000 ids of rows fit
    rows = b"[" + b",".join(str(10**11 + i).encode() for i in range(200_000)) + b"]"
    r = post(c, f"{API}/stats/selection", b'{"set_id": %d, "table": null, "rows": %s}' % (set_id, rows))
    assert r.status_code == 200, r.text


def test_deep_nesting_is_422_not_500(demo) -> None:  # type: ignore[no-untyped-def]
    c, set_id = demo
    for depth in (65, 5000, 100_000):
        inner = b"[" * depth + b"]" * depth
        for url, body in (
            (f"{API}/stats/scatter", b'{"v": 1, "set_id": %d, "pad": %s}' % (set_id, inner)),
            (f"{API}/charts", b'{"set_id": %d, "kind": "histogram", "name": "H%d", "settings": {"v": 1, "pad": %s}}'
             % (set_id, depth, inner)),
        ):  # fmt: skip
            r = post(c, url, body)
            assert r.status_code == 422, (depth, url, r.text[:200])
            got = r.json()
            assert got["code"] == "bad_request" and "64" in got["message"] and got["hint"]
    assert c.get(f"{API}/charts?set_id={set_id}&kind=histogram").json()["charts"] == []  # nothing stored
    # 64 levels and brackets inside strings pass
    ok = b'{"v": 1, "set_id": %d, "pad": %s, "s": "%s"}' % (set_id, b"[" * 62 + b"]" * 62, b"[{" * 1000)
    assert post(c, f"{API}/stats/scatter", ok).status_code == 200


def test_recursion_left_is_422(demo, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from bf_core.stats import routes_charts

    c, set_id = demo

    def deep(*_args: Any) -> Any:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(routes_charts, "checked", deep)
    r = post(c, f"{API}/stats/scatter", b'{"v": 1, "set_id": %d}' % set_id)
    assert r.status_code == 422 and r.json()["code"] == "bad_request"


def test_depth_and_other_paths() -> None:
    assert not bl.depth_over(b'{"a": [1, [2, {"b": "]]]]"}]]}', 4)
    assert bl.depth_over(b'{"a": [1, [2, {"b": 3}]]}', 3)
    assert not bl.depth_over(b'"[[[[\\"[[["', 1)  # an escaped quote inside a string
    assert not bl.depth_over(b"[]" * 1000, 1) and bl.depth_over(b"[[]]", 1)


def test_other_routes_are_not_limited(pclient) -> None:  # type: ignore[no-untyped-def]
    big = b'{"name": "' + b"x" * (bl.BODY_LIMIT + 10) + b'"}'
    r = post(pclient, f"{API}/projects", big)
    assert r.status_code != 413
    assert post(pclient, f"{API}/stats/scatter", b"{}", **{"X-BF-Token": "x"}).status_code == 401  # the guard first
