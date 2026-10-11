"""Authentication result contract owned by Foundation, populated by Module B."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Principal(BaseModel):
    """Normalized identity available after authentication has succeeded.

    Module B is responsible for token verification and constructing this
    object. Foundation intentionally contains no credential parsing logic.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    # None for an organizer-only identity (Module K decides organizer access).
    team_id: UUID | None = None
    role: str = Field(default="participant", min_length=1)
    email: str | None = None
