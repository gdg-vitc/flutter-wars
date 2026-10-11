"""Module F (team widget inventory) contract, for Modules C, I, J and K."""

from dataclasses import dataclass
from typing import ClassVar, Protocol
from uuid import UUID


@dataclass(frozen=True)
class InventoryItem:
    widget_id: str
    appdev_key: str
    display_name: str
    quantity: int
    archived: bool
    description: str | None = None


class InventoryGateway(Protocol):
    OWNER: ClassVar[str] = "Module F inventory"

    # --- reads (Modules C, K)
    def get_team_inventory(self, team_id: UUID, *, include_zero: bool = False) -> list[InventoryItem]:
        """Archived widgets the team still owns are included, flagged archived=True."""
        ...

    def unit_counts(self, team_ids: list[UUID]) -> dict[UUID, int]:
        """Total units owned per team in one query; teams with nothing show 0."""
        ...

    def owned_quantity(self, team_id: UUID, widget_id: str) -> int: ...

    # --- trading and auctions (Modules I, J); errors are the marketplace errors
    def add(self, *, team_id: UUID, widget_id: str, quantity: int, reference: UUID) -> None: ...
    def remove(self, *, team_id: UUID, widget_id: str, quantity: int, reference: UUID) -> None: ...
