"""Uniform API errors: {code, message, hint} with Russian user-facing texts."""

from __future__ import annotations

import re
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Error shown to the user. `message` and `hint` are in Russian."""

    def __init__(self, status: int, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.hint = hint


def error_body(code: str, message: str, hint: str = "") -> dict[str, str]:
    return {"code": code, "message": message, "hint": hint}


async def api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(error_body(exc.code, exc.message, exc.hint), status_code=exc.status)


def internal_body(exc: BaseException) -> dict[str, str]:
    """500 for the operation log (ТЗ 7.9): `detail` — type and text, `trace` — the end of the stack."""
    import traceback

    body = error_body("internal", "Внутренняя ошибка ядра.", "Скопируйте журнал операции и отправьте разработчику.")
    body["detail"] = mask_secrets(f"{type(exc).__name__}: {exc}")[:2000]
    body["trace"] = mask_secrets("".join(traceback.format_exception(exc)[-12:]))[-6000:]
    return body


_SECRET = re.compile(r"""(?i)(["']?(?:password|secret|token|passphrase)["']?\s*[:=]\s*)(["']?)[^\s"',)}]+\2""")


def mask_secrets(text: str) -> str:
    """Passwords never reach the operation log, even inside an error text (the engines already hide SQL
    parameters: `hide_parameters=True`)."""
    return _SECRET.sub(r"\1\2***\2", text)


async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(internal_body(exc), status_code=500)


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Bad request fields: a Russian message naming the fields, never their values (a password may be one)."""
    errors: list[dict[str, Any]] = getattr(exc, "errors", lambda: [])()
    fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) or "тело" for e in errors})
    return JSONResponse(
        error_body(
            "bad_request",
            f"Неверные данные запроса: {', '.join(fields) or 'тело запроса'}.",
            "Проверьте введённые значения; если ошибка повторяется — отправьте журнал разработчику.",
        ),
        status_code=422,
    )
