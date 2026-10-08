"""Request and response contracts for Module C."""

from datetime import datetime

from pydantic import BaseModel, Field


class IssuedApiKey(BaseModel):
    key_id: str
    api_key: str = Field(description="Shown once; it cannot be retrieved again.")
    created_at: datetime


class RevokedApiKey(BaseModel):
    key_id: str
    status: str
    revoked_at: datetime


class IdeWidgetState(BaseModel):
    widget_id: str
    quantity: int = Field(ge=0)


class IdeState(BaseModel):
    team_id: str
    widgets: list[IdeWidgetState]
