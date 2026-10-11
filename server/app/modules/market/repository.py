from uuid import UUID

from sqlmodel import Session, case, col, select

from app.contracts.catalog import CatalogGateway, WidgetInfo
from app.core.services import gateway
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


def visible_round(session: Session, market_id: UUID | None = None) -> MarketRound | None:
    """Prefer the live round, otherwise the latest started round, in one query."""
    statement = select(MarketRound).where(MarketRound.status != RoundStatus.DRAFT)
    if market_id is None:
        statement = statement.join(Market).where(col(Market.is_active).is_(True))
    else:
        statement = statement.where(MarketRound.market_id == market_id)
    return session.exec(
        statement.order_by(
            case((col(MarketRound.status).in_(LIVE_STATUSES), 0), else_=1),
            col(MarketRound.sequence).desc(),
        ).limit(1)
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


def listing_for_widget(session: Session, round_id: UUID, widget_id: str) -> MarketListing | None:
    return session.exec(
        select(MarketListing).where(
            MarketListing.round_id == round_id, MarketListing.widget_id == widget_id
        ),
    ).one_or_none()


def archived_widget_ids(session: Session, round_id: UUID) -> list[str]:
    """Listed widgets that Module D has archived, by display name."""
    widget_ids = list(
        session.exec(select(MarketListing.widget_id).where(MarketListing.round_id == round_id))
    )
    widgets = gateway(CatalogGateway, session).get_widgets(widget_ids)
    archived = [w for w in widgets.values() if w.archived]
    return [w.id for w in sorted(archived, key=lambda w: (w.display_name, w.id))]


def widget_details(session: Session, widget_ids: list[str]) -> dict[str, WidgetInfo]:
    return gateway(CatalogGateway, session).get_widgets(widget_ids)
