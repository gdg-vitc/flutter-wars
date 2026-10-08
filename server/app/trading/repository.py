from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from .models import TradeTransaction, TradeType


class TradeRepository:
    def __init__(self, session: Session):
        self.session = session

    def add(self, trade: TradeTransaction) -> None:
        self.session.add(trade)
        self.session.flush()

    def get_by_idempotency_key(
        self, *, team_id: UUID, transaction_type: TradeType, idempotency_key: UUID
    ) -> TradeTransaction | None:
        return self.session.exec(
            select(TradeTransaction).where(
                TradeTransaction.team_id == team_id,
                TradeTransaction.transaction_type == transaction_type,
                TradeTransaction.idempotency_key == idempotency_key,
            )
        ).one_or_none()

    def sold_quantity(self, *, team_id: UUID, listing_id: UUID) -> int:
        """Units this team has resold into this listing (receipts are immutable)."""
        return self.session.exec(
            select(func.coalesce(func.sum(TradeTransaction.quantity), 0)).where(
                TradeTransaction.team_id == team_id,
                TradeTransaction.listing_id == listing_id,
                TradeTransaction.transaction_type == TradeType.SELL,
            )
        ).one()

    def history(self, *, team_id: UUID, limit: int, offset: int) -> list[TradeTransaction]:
        return list(
            self.session.exec(
                select(TradeTransaction)
                .where(
                    TradeTransaction.team_id == team_id,
                )
                .order_by(TradeTransaction.created_at.desc(), TradeTransaction.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )

    def get_owned(self, *, team_id: UUID, trade_id: UUID) -> TradeTransaction | None:
        return self.session.exec(
            select(TradeTransaction).where(
                TradeTransaction.id == trade_id,
                TradeTransaction.team_id == team_id,
            )
        ).one_or_none()
