from datetime import datetime
from uuid import UUID

from sqlalchemy import func
from sqlalchemy import select as sa_select
from sqlmodel import Session, select

from app.integration.errors import NotFound

from .models import Auction, AuctionResult, Bid, BidReceipt


class AuctionRepository:
    def __init__(self, session: Session):
        self.session = session

    def auction(self, auction_id: UUID, *, lock: bool = False) -> Auction:
        statement = select(Auction).where(Auction.id == auction_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        item = self.session.exec(statement).one_or_none()
        if item is None:
            raise NotFound()
        return item

    def own_bid(self, *, auction_id: UUID, team_id: UUID) -> Bid | None:
        return self.session.exec(
            select(Bid).where(Bid.auction_id == auction_id, Bid.team_id == team_id)
        ).one_or_none()

    def receipt(self, *, auction_id: UUID, team_id: UUID, key: UUID) -> BidReceipt | None:
        return self.session.exec(
            select(BidReceipt).where(
                BidReceipt.auction_id == auction_id,
                BidReceipt.team_id == team_id,
                BidReceipt.idempotency_key == key,
            )
        ).one_or_none()

    def bids(self, auction_id: UUID) -> list[Bid]:
        return list(
            self.session.exec(
                select(Bid)
                .where(Bid.auction_id == auction_id)
                .order_by(Bid.amount.desc(), Bid.amount_reached_order.asc())
            ).all()
        )

    def result(self, auction_id: UUID) -> AuctionResult | None:
        return self.session.get(AuctionResult, auction_id)

    def now(self) -> datetime:
        return self.session.execute(sa_select(func.clock_timestamp())).scalar_one()

    def add(self, record: Auction | Bid | BidReceipt | AuctionResult) -> None:
        self.session.add(record)
        self.session.flush()
