from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, tuple_
from sqlmodel import Session, select

from .models import ResaleAccount, ResalePosition, TradeTransaction, TradeType


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

    def feed(self, *, team_id: UUID | None, limit: int, before: tuple[datetime, UUID] | None) -> list[TradeTransaction]:
        """All teams' trades (or one team's), newest first, keyset-paginated."""
        statement = select(TradeTransaction)
        if team_id is not None:
            statement = statement.where(TradeTransaction.team_id == team_id)
        if before is not None:
            created_at, trade_id = before
            statement = statement.where(
                tuple_(TradeTransaction.created_at, TradeTransaction.id) < tuple_(created_at, trade_id)
            )
        return list(
            self.session.exec(
                statement.order_by(TradeTransaction.created_at.desc(), TradeTransaction.id.desc()).limit(limit)
            ).all()
        )

    def get_owned(self, *, team_id: UUID, trade_id: UUID) -> TradeTransaction | None:
        return self.session.exec(
            select(TradeTransaction).where(
                TradeTransaction.id == trade_id,
                TradeTransaction.team_id == team_id,
            )
        ).one_or_none()

    def external_units(self, team_id: UUID, widget_id: str) -> int:
        signed = case(
            (TradeTransaction.transaction_type == TradeType.BUY, TradeTransaction.quantity),
            else_=-TradeTransaction.quantity,
        )
        return max(
            0,
            int(
                self.session.exec(
                    select(func.coalesce(func.sum(signed), 0)).where(
                        TradeTransaction.team_id != team_id, TradeTransaction.widget_id == widget_id
                    )
                ).one()
            ),
        )

    def resale_state(self, team_id: UUID, widget_id: str) -> tuple[ResaleAccount, ResalePosition]:
        # The caller holds one team-wide transaction lock BEFORE any market lock.
        account = self.session.get(ResaleAccount, team_id)
        if account is None:
            account = ResaleAccount(team_id=team_id)
            self.session.add(account)
        position = self.session.get(ResalePosition, (team_id, widget_id))
        if position is None:
            position = ResalePosition(team_id=team_id, widget_id=widget_id)
            self.session.add(position)
        self.session.flush()
        return account, position
