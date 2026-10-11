"""Module J lifecycle checks consumed by Market and Catalog through a gateway."""

from typing import Protocol
from uuid import UUID


class AuctionGateway(Protocol):
    def ensure_lot_releasable(self, auction_id: UUID) -> None: ...
    def widget_is_exclusive(self, widget_id: str) -> bool: ...
