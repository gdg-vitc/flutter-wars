"""Identity and team entities owned by Module B."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


class UserIdentity(SQLModel, table=True):
    __tablename__ = "user_identity"

    id: int | None = Field(default=None, primary_key=True)
    google_subject: str | None = Field(default=None, index=True, unique=True, max_length=255)
    email: str = Field(index=True, max_length=320)
    created_at: datetime = Field(default_factory=_now)


class Team(SQLModel, table=True):
    __tablename__ = "team"

    # UUID so every owner module (ledger, inventory, trading, auction) can reference it.
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True, unique=True, max_length=128)
    status: str = Field(default="active", max_length=16)
    created_at: datetime = Field(default_factory=_now)


class TeamMembership(SQLModel, table=True):
    __tablename__ = "team_membership"

    id: int | None = Field(default=None, primary_key=True)
    user_identity_id: int = Field(foreign_key="user_identity.id", unique=True, index=True)
    team_id: UUID = Field(foreign_key="team.id", index=True)
    role: str = Field(default="participant", max_length=32)
    created_at: datetime = Field(default_factory=_now)
