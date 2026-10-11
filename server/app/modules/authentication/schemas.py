"""Public HTTP models for Module B."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str | None = None
    expires_in: int | None = None


class MeResponse(BaseModel):
    user_id: str
    email: str | None
    team_id: str | None
    role: str
    auth_type: str = "JWT"


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: SecretStr = Field(min_length=43, max_length=128)


class CodeExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: SecretStr = Field(min_length=43, max_length=128)
    code_verifier: SecretStr = Field(min_length=43, max_length=128)
