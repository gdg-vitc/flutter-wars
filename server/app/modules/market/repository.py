from uuid import UUID

from sqlmodel import Session, col, select

from app.modules.catalog.models import Widget
from app.modules.market.models import LIVE_STATUSES, Market, MarketListing, MarketRound, RoundStatus


def active_market(session: Session) -> Market | None:
    return session.exec(select(Market).where(col(Market.is_active).is_(True))).one_or_none()


def rounds(session: Session, market_id: UUID) -> list[MarketRound]:
    return list(
        session.exec(
            select(MarketRound)
            .where(MarketRound.market_id == market_id)
            .order_by(col(MarketRound.sequence)),
        )
    )


def live_round(session: Session, market_id: UUID) -> MarketRound | None:
    return session.exec(
        select(MarketRound).where(
            MarketRound.market_id == market_id, col(MarketRound.status).in_(LIVE_STATUSES)
        ),
    ).one_or_none()


def latest_started_round(session: Session, market_id: UUID) -> MarketRound | None:
    return session.exec(
        select(MarketRound)
        .where(MarketRound.market_id == market_id, MarketRound.status != RoundStatus.DRAFT)
        .order_by(col(MarketRound.sequence).desc())
        .limit(1),
    ).one_or_none()


def listings(session: Session, round_id: UUID) -> list[MarketListing]:
    return list(
        session.exec(
            select(MarketListing)
            .where(MarketListing.round_id == round_id)
            .order_by(col(MarketListing.created_at), col(MarketListing.id)),
        )
    )


def listing_ids(session: Session, round_id: UUID) -> list[UUID]:
    return list(
        session.exec(
            select(MarketListing.id)
            .where(MarketListing.round_id == round_id)
            .order_by(col(MarketListing.created_at), col(MarketListing.id)),
        )
    )


def listing_for_widget(session: Session, round_id: UUID, widget_id: UUID) -> MarketListing | None:
    return session.exec(
        select(MarketListing).where(
            MarketListing.round_id == round_id, MarketListing.widget_id == widget_id
        ),
    ).one_or_none()


def archived_widget_ids(session: Session, round_id: UUID) -> list[UUID]:
    return list(
        session.exec(
            select(Widget.id)
            .join(MarketListing, col(MarketListing.widget_id) == Widget.id)
            .where(MarketListing.round_id == round_id, col(Widget.archived).is_(True))
            .order_by(col(Widget.name), col(Widget.id)),
        )
    )


def widget_names(session: Session, widget_ids: list[UUID]) -> dict[UUID, str]:
    rows = session.exec(select(Widget.id, Widget.name).where(col(Widget.id).in_(widget_ids)))
    return {widget_id: name for widget_id, name in rows}
