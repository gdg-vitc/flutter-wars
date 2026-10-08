from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Machine-readable business error (spec section 2.2).

    The final error shape is a Module A decision; this one carries a stable
    code, a human-readable message and safe context fields.
    """

    def __init__(self, code: str, message: str, *, status_code: int = 400, **context: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.context = context


class NotFound(AppError):
    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(code, message, status_code=404, **context)


class Conflict(AppError):
    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(code, message, status_code=409, **context)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        body = {"error": {"code": exc.code, "message": exc.message, "context": exc.context}}
        return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(body))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Never echo exception text: it can carry SQL, parameters or secrets.
        body = {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "The operation could not be completed.",
                "context": {},
            }
        }
        return JSONResponse(status_code=500, content=body)
