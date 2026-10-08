"""Module K ports: how the admin layer reaches modules owned by OTHER teams.

Module K never touches another module's tables. Owners register an implementation at
startup; until they do, the related admin endpoint answers 503 DEPENDENCY_NOT_AVAILABLE.

  Module B (Team 6) -> set_team_directory(...)         teams: list / create / enable / disable
  Module G (Team 2) -> set_market_status_provider(...)  read-only market/round summary
  Module I (Team 2) -> set_transaction_feed(...)        cross-team trade history
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from sqlmodel import Session


@dataclass(frozen=True)
class TeamSummary:
    id: UUID
    name: str
    status: str  # "ACTIVE" | "DISABLED"


class TeamDirectory(Protocol):
    def list_teams(self, s: Session) -> list[TeamSummary]: ...
    def get_team(self, s: Session, team_id: UUID) -> TeamSummary | None: ...
    def find_by_name(self, s: Session, name: str) -> TeamSummary | None: ...
    def create_team(self, s: Session, name: str, member_emails: list[str]) -> TeamSummary: ...
    def set_status(self, s: Session, team_id: UUID, status: str) -> TeamSummary: ...


@dataclass(frozen=True)
class TransactionQuery:
    team_id: UUID | None
    limit: int
    cursor: str | None


MarketStatusProvider = Callable[[Session], dict[str, Any]]
TransactionFeed = Callable[[Session, TransactionQuery], dict[str, Any]]  # {"items": [...], "next_cursor": ...}

_team_directory: TeamDirectory | None = None
_market_status: MarketStatusProvider | None = None
_transaction_feed: TransactionFeed | None = None


def set_team_directory(impl: TeamDirectory | None) -> None:
    global _team_directory
    _team_directory = impl


def set_market_status_provider(fn: MarketStatusProvider | None) -> None:
    global _market_status
    _market_status = fn


def set_transaction_feed(fn: TransactionFeed | None) -> None:
    global _transaction_feed
    _transaction_feed = fn


def team_directory() -> TeamDirectory | None:
    return _team_directory


def market_status_provider() -> MarketStatusProvider | None:
    return _market_status


def transaction_feed() -> TransactionFeed | None:
    return _transaction_feed
