"""Module I (transaction engine) contract, for Module K's cross-team trade feed."""

from dataclasses import dataclass
from typing import Any, ClassVar, Protocol
from uuid import UUID


@dataclass(frozen=True)
class TransactionQuery:
    team_id: UUID | None
    limit: int
    cursor: str | None


class TradingGateway(Protocol):
    OWNER: ClassVar[str] = "Module I transactions"

    def net_units(self, widget_id: str) -> int: ...

    def transaction_feed(self, query: TransactionQuery) -> dict[str, Any]:
        """Newest first: {"items": [...], "next_cursor": str | None}."""
        ...
