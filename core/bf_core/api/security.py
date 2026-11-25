"""Request guard: launch token (X-BF-Token), Host and Origin checks (ТЗ 5.6, S1-S2)."""

from __future__ import annotations

import hmac
import logging
from collections.abc import Awaitable, Callable, Iterable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from .errors import error_body, internal_body

log = logging.getLogger("bf_core.api")

TOKEN_HEADER = "X-BF-Token"
APP_ORIGIN = "app://bedrockforge"
# Paths reachable without a token: CORS preflight is handled separately.
PUBLIC_PATHS: frozenset[str] = frozenset()


class GuardMiddleware:
    """Pure ASGI middleware so that it also covers streaming (SSE) responses."""

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        allowed_origins: Iterable[str] = (APP_ORIGIN,),
        port: int | None = None,
    ) -> None:
        self.app = app
        self.token = token.encode()
        self.allowed_origins = frozenset(allowed_origins)
        # ADR 0002: Host must be exactly 127.0.0.1:<port> (DNS rebinding); port None = tests only.
        self.port = port

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        response = self._check(request)
        if response is not None:
            await response(scope, receive, send)
            return
        started = False
        cors_send = self._with_cors(send, request)

        async def tracking_send(message):  # type: ignore[no-untyped-def]
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await cors_send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:  # noqa: BLE001 - answer in Russian with CORS instead of a bare 500
            log.exception("unhandled error in %s %s", request.method, request.url.path)
            if started:
                return
            body = internal_body(exc)
            await JSONResponse(body, status_code=500)(scope, receive, cors_send)

    def _cors_headers(self, request: Request) -> list[tuple[bytes, bytes]]:
        origin = request.headers.get("origin")
        if origin and origin in self.allowed_origins:
            return [
                (b"access-control-allow-origin", origin.encode()),
                (b"vary", b"Origin"),
                (b"access-control-expose-headers", b"ETag, Content-Range, Content-Length, X-BF-Support"),
            ]
        return []

    def _with_cors(self, send: Callable[..., Awaitable[None]], request: Request):  # type: ignore[no-untyped-def]
        extra = self._cors_headers(request)
        if not extra:
            return send

        async def wrapped(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        return wrapped

    def _check(self, request: Request) -> Response | None:
        host = request.headers.get("host") or ""
        name, _, port = host.rpartition(":")
        ok_host = host == f"127.0.0.1:{self.port}" if self.port else (name == "127.0.0.1" and port.isdigit())
        if not ok_host:
            return JSONResponse(
                error_body("bad_host", "Запрос с недопустимым адресом.", "Ядро принимает запросы только с 127.0.0.1."),
                status_code=403,
            )
        origin = request.headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            return JSONResponse(
                error_body("bad_origin", "Запрос со сторонней страницы отклонён.", ""),
                status_code=403,
            )
        if request.method == "OPTIONS" and origin is not None:
            # CORS preflight carries no custom headers; it reveals nothing.
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE",
                    "Access-Control-Allow-Headers": f"{TOKEN_HEADER}, Content-Type, Range, If-Match, If-None-Match",
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )
        if request.url.path in PUBLIC_PATHS:
            return None
        supplied = (request.headers.get(TOKEN_HEADER) or "").encode()
        if not supplied or not hmac.compare_digest(supplied, self.token):
            return JSONResponse(
                error_body("unauthorized", "Нет доступа к ядру.", "Запрос должен идти из окна BedrockForge."),
                status_code=401,
                headers=dict((k.decode(), v.decode()) for k, v in self._cors_headers(request)),
            )
        return None
