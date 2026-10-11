"""Module B (authentication and team identity) contract.

Callers: Module A's token check (app/core/principal.py), Module K's team management,
Module C's API-key routes, and Modules E/F's team-existence checks. Module B owns the
`team`, `team_membership` and `user_identity` tables; nobody else reads them.
"""

from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import UUID

from app.contracts.principal import Principal


@dataclass(frozen=True)
class TeamSummary:
    id: UUID
    name: str
    status: str  # "ACTIVE" | "DISABLED"


class IdentityGateway(Protocol):
    OWNER: ClassVar[str] = "Module B team directory"

    def principal_for_token(self, token: str, settings: object) -> Principal:
        """Verify an access token and resolve the caller's current identity and team."""
        ...

    def authorized_stream_tokens(self, tokens: list[str], settings: object) -> set[str]:
        """Revalidate JWTs and current team access in a batched identity query."""
        ...

    def team_exists(self, team_id: UUID) -> bool: ...
    def list_teams(self) -> list[TeamSummary]: ...
    def get_team(self, team_id: UUID) -> TeamSummary | None: ...
    def find_by_name(self, name: str) -> TeamSummary | None: ...

    def create_team(self, name: str, member_emails: list[str]) -> TeamSummary:
        """Create the team and pre-register its members by email."""
        ...

    def set_status(self, team_id: UUID, status: str) -> TeamSummary: ...
