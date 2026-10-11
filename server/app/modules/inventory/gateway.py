"""Module F's gateway (app/contracts/inventory.py). Thin: the rules live in service.py.

Trading and auction write inventory events with reference marketplace/<trade_id or auction_id>.
"""

from uuid import UUID

from sqlmodel import Session

from app.contracts.inventory import InventoryItem
from app.contracts.marketplace_errors import InsufficientInventory, InvalidListing, port_errors
from app.modules.inventory import repository as repo
from app.modules.inventory import service as inventory

ACTOR = "system:marketplace"
_PORT_ERRORS = {
    "INSUFFICIENT_QUANTITY": InsufficientInventory,
    "WIDGET_NOT_FOUND": InvalidListing,
    "WIDGET_ARCHIVED": InvalidListing,
}


class InventoryGatewayImpl:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_team_inventory(self, team_id: UUID, *, include_zero: bool = False) -> list[InventoryItem]:
        return inventory.get_team_inventory(self.session, team_id, include_zero=include_zero)

    def owned_quantity(self, team_id: UUID, widget_id: str) -> int:
        return repo.get_quantity(self.session, team_id, widget_id, lock=True)

    def unit_counts(self, team_ids: list[UUID]) -> dict[UUID, int]:
        return inventory.unit_counts(self.session, team_ids)

    def add(self, *, team_id: UUID, widget_id: str, quantity: int, reference: UUID) -> None:
        with port_errors(_PORT_ERRORS):
            inventory.increment(
                self.session,
                team_id,
                widget_id,
                quantity,
                ref_type="marketplace",
                ref_id=str(reference),
                reason="Marketplace award",
                actor=ACTOR,
            )

    def remove(self, *, team_id: UUID, widget_id: str, quantity: int, reference: UUID) -> None:
        with port_errors(_PORT_ERRORS):
            inventory.decrement(
                self.session,
                team_id,
                widget_id,
                quantity,
                ref_type="marketplace",
                ref_id=str(reference),
                reason="Market resale",
                actor=ACTOR,
            )
