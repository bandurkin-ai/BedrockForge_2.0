"""Uniform API errors: {code, message, hint} with Russian user-facing texts."""

from __future__ import annotations

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
