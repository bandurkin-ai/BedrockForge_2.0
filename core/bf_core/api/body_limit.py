"""The size and the depth of the JSON bodies of the routes of charts and statistics (`/api/v1/stats/*`,
`/api/v1/charts*`; 13.36).

Their bodies are the settings of a chart (≤ 64 КБ, `charts.kinds`), a lasso (≤ 1000 vertices) or the rows of a
selection (≤ 200 000 ids, ≈ 2,6 МБ): more than `BODY_LIMIT` (8 МБ) is 413 `request_too_large` before the body is
read further (by `Content-Length`, or by counting a body sent in parts). A body nested deeper than `MAX_DEPTH`
levels is 422 `bad_request` before FastAPI parses it: the JSON decoder, the encoder of the answer and the checks
are recursive (a body nested 10⁵ deep answered 400 in English, one 5000 deep stored a chart that could not be
sent back — 500). A `RecursionError` left all the same answers 422 too. All texts are Russian, `{code, message,
hint}`. Pure ASGI, inside `GuardMiddleware` (a request without the token never gets this far)."""

from __future__ import annotations

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import error_body

BODY_LIMIT = 8 * 1024 * 1024
MAX_DEPTH = 64
LIMITED_PREFIXES = ("/api/v1/stats/", "/api/v1/charts")

_STRING = re.compile(rb'"(?:[^"\\]|\\.)*"', re.DOTALL)
_NOT_BRACKETS = bytes(b for b in range(256) if b not in b"[]{}")


class _TooLarge(Exception):
    pass


def too_large(limit: int = BODY_LIMIT) -> JSONResponse:
    return JSONResponse(
        error_body(
            "request_too_large", f"Запрос больше {limit // (1024 * 1024)} МБ.",
            "Выделите меньше проб или упростите контур и настройки графика.",
        ),
        status_code=413,
    )  # fmt: skip


def too_deep() -> JSONResponse:
    return JSONResponse(
        error_body(
            "bad_request", f"Тело запроса вложено глубже {MAX_DEPTH} уровней.",
            f"Проверьте настройки графика: вложенных списков и объектов — не больше {MAX_DEPTH}.",
        ),
        status_code=422,
    )  # fmt: skip


def depth_over(body: bytes, limit: int = MAX_DEPTH) -> bool:
    """Is the JSON `body` nested deeper than `limit` (brackets inside strings do not count)?"""
    brackets = _STRING.sub(b"", body).translate(None, _NOT_BRACKETS)
    if len(brackets) <= limit:
        return False
    depth = 0
    for b in brackets:
        if b in b"[{":
            depth += 1
            if depth > limit:
                return True
        else:
            depth -= 1
    return False


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, limit: int = BODY_LIMIT, prefixes: tuple[str, ...] = LIMITED_PREFIXES) -> None:
        self.app, self.limit, self.prefixes = app, limit, prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith(self.prefixes):
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length", b"")
        if length.isdigit() and int(length) > self.limit:
            await too_large(self.limit)(scope, receive, send)
            return
        try:
            body = await self._read(receive)
        except _TooLarge:
            await too_large(self.limit)(scope, receive, send)
            return
        if depth_over(body):
            await too_deep()(scope, receive, send)
            return
        given = False

        async def replay() -> Message:
            nonlocal given
            if given:
                return await receive()
            given = True
            return {"type": "http.request", "body": body, "more_body": False}

        started = False

        async def tracking(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, replay, tracking)
        except RecursionError:
            if started:
                raise
            await too_deep()(scope, receive, send)

    async def _read(self, receive: Receive) -> bytes:
        parts: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # a disconnect: the app sees an empty body
                break
            part = message.get("body", b"")
            size += len(part)
            if size > self.limit:
                raise _TooLarge
            parts.append(part)
            if not message.get("more_body", False):
                break
        return b"".join(parts)
