"""Public, version-stable error payloads shared by all modules."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Safe error response returned by the API.

    ``context`` is deliberately optional: future modules can add client-safe
    details without changing the required ``code``/``message`` contract.
    """

    code: str = Field(description="Stable, machine-readable error code")
    message: str = Field(description="Human-readable, safe error message")
    context: dict[str, Any] | None = Field(default=None, description="Optional safe context")
