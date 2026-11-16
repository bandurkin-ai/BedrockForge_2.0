"""Uniform API errors: {code, message, hint} with Russian user-facing texts."""

from __future__ import annotations

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


async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        error_body(
            "internal",
            "Внутренняя ошибка ядра.",
            "Скопируйте журнал ядра и отправьте разработчику.",
        ),
        status_code=500,
    )


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
