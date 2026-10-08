"""Public HTTP models for Module B."""

from pydantic import BaseModel, Field


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    user_id: str
    email: str | None
    team_id: str
    role: str
    auth_type: str = "JWT"
