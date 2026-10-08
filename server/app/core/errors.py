"""TEMP until Module A (Foundation) is merged.

Shared error contract (doc section 2.2): stable code + human message + safe details.
Never put stack traces, SQL or secrets in `details`.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Same envelope as AppError. Never echo the submitted input back (it could contain secrets).
    errors = [
        {"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", ""))[:200], "type": e.get("type")}
        for e in exc.errors()[:20]
    ]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": "Request is invalid", "details": {"errors": errors}}},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
