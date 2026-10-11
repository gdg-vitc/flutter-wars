"""Application exceptions and FastAPI exception mapping."""

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
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
        *,
        details: dict[str, Any] | None = None,
        **fields: Any,
    ) -> None:
        # Modules pass safe context as a dict (`context=`/`details=`) or as keyword fields.
        merged = {**(context or {}), **(details or {}), **fields}
        self.code = code
        self.message = message
        self.status_code = status_code
        self.context = merged or None
        super().__init__(message)

    @property
    def details(self) -> dict[str, Any]:
        return self.context or {}


class NotFound(AppError):
    def __init__(self, code: str, message: str, **fields: Any) -> None:
        super().__init__(code, message, 404, **fields)


class Conflict(AppError):
    def __init__(self, code: str, message: str, **fields: Any) -> None:
        super().__init__(code, message, 409, **fields)


def _response(error: AppError) -> JSONResponse:
    body = ErrorResponse(code=error.code, message=error.message, context=error.context)
    return JSONResponse(
        status_code=error.status_code,
        content=jsonable_encoder({"error": body.model_dump(exclude_none=True)}),
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
        # Field locations and messages only: never echo the submitted input back.
        errors = [
            {"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", ""))[:200], "type": e.get("type")}
            for e in exc.errors()[:20]
        ]
        return _response(
            AppError("REQUEST_VALIDATION_FAILED", "Request validation failed.", 422, {"errors": errors})
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        log_exception("unhandled_exception", exc)
        return _response(AppError("INTERNAL_SERVER_ERROR", "An unexpected error occurred.", 500))


# Name used by modules written before Module A landed.
register_error_handlers = install_exception_handlers
