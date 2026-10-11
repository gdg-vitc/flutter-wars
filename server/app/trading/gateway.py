"""Module I publishes the organizer transaction feed."""

import base64
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.contracts.trading import TransactionQuery
from app.core.errors import AppError
from app.trading.repository import TradeRepository
from app.trading.schemas import TradeResponse


def _encode_cursor(created_at: datetime, trade_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{trade_id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        created_at, trade_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(created_at), UUID(trade_id)
    except ValueError as exc:
        raise AppError("INVALID_CURSOR", "The cursor is not valid.", 422) from exc


def transaction_feed(session: Session, query: TransactionQuery) -> dict[str, Any]:
    before = _decode_cursor(query.cursor) if query.cursor else None
    trades = TradeRepository(session).feed(team_id=query.team_id, limit=query.limit, before=before)
    items = [TradeResponse.model_validate(t).model_dump(mode="json") | {"team_id": str(t.team_id)} for t in trades]
    last = trades[-1] if len(trades) == query.limit else None
    return {"items": items, "next_cursor": _encode_cursor(last.created_at, last.id) if last else None}


class TradingGatewayImpl:
    def __init__(self, session: Session) -> None:
        self.session = session

    def net_units(self, widget_id: str) -> int:
        from sqlalchemy import case, func
        from sqlmodel import select

        from app.trading.models import TradeTransaction, TradeType

        signed = case(
            (TradeTransaction.transaction_type == TradeType.BUY, TradeTransaction.quantity),
            else_=-TradeTransaction.quantity,
        )
        return max(
            0,
            int(
                self.session.exec(
                    select(func.coalesce(func.sum(signed), 0)).where(TradeTransaction.widget_id == widget_id)
                ).one()
            ),
        )

    def transaction_feed(self, query: TransactionQuery) -> dict[str, Any]:
        return transaction_feed(self.session, query)
