"""Application exceptions and FastAPI exception mapping."""

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.contracts.errors import ErrorResponse
from app.core.logging import log_exception


class AppError(Exception):
    """Controlled error used by modules to return the shared error contract."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.context = context
        super().__init__(message)


def _response(error: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=ErrorResponse(
            code=error.code, message=error.message, context=error.context
        ).model_dump(exclude_none=True),
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Install safe, uniform public error responses once during app construction."""

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return _response(exc)

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request could not be completed."
        return _response(AppError(f"HTTP_{exc.status_code}", message, exc.status_code))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        log_exception("request_validation_failed", exc)
        return _response(AppError("REQUEST_VALIDATION_FAILED", "Request validation failed.", 422))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        log_exception("unhandled_exception", exc)
        return _response(AppError("INTERNAL_SERVER_ERROR", "An unexpected error occurred.", 500))
