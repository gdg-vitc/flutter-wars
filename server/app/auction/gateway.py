from uuid import UUID

from sqlmodel import Session, select

from app.auction.models import Auction, AuctionState
from app.core.errors import Conflict


class AuctionGatewayImpl:
    def __init__(self, session: Session):
        self.session = session

    def ensure_lot_releasable(self, auction_id: UUID) -> None:
        auction = self.session.exec(select(Auction).where(Auction.id == auction_id).with_for_update()).one_or_none()
        if auction is not None and auction.state != AuctionState.DRAFT:
            raise Conflict("AUCTION_LOT_IN_USE", "Cancel the auction to release its lot and refund held credits.")

    def widget_is_exclusive(self, widget_id: str) -> bool:
        return self.session.exec(select(Auction.id).where(Auction.widget_id == widget_id).limit(1)).first() is not None
