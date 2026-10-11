from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from copy import copy
from uuid import UUID

from fastapi import Depends, Request
from sqlmodel import Session

from app.auction.repository import AuctionRepository
from app.auction.schemas import AuctionCreate, AuctionView, BidRequest, MyBid, SettlementResponse
from app.auction.service import AuctionService, NoBidHandler
from app.contracts.admin import OrganizerPrincipal
from app.contracts.marketplace import Adapters, BrokeragePolicy
from app.contracts.marketplace_errors import ConfigurationRequired
from app.core.audit import audit
from app.core.db import get_db
from app.trading.policies import DEFAULT_BROKERAGE
from app.trading.repository import TradeRepository
from app.trading.risk import ResaleRules
from app.trading.schemas import PurchaseRequest, SellRequest, TradeResponse
from app.trading.service import TradingService


class BackendModules:
    """Composition boundary supplied with Foundation's session factory.

    Fresh sessions, one explicit transaction per operation, response only AFTER commit.
    Authentication must complete before calling this runtime (separate auth session).
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        adapter_factory: Callable[[Session], Adapters],
        brokerage: BrokeragePolicy | None = DEFAULT_BROKERAGE,
        no_bid_handler: NoBidHandler | None = None,
        freeze_guard: Callable[[Session, str], None] | None = None,
        resale_rules: ResaleRules | None = None,
    ):
        self.session_factory = session_factory
        self.bound_session: Session | None = None
        self.resale_rules = resale_rules
        self.adapter_factory = adapter_factory
        self.brokerage = brokerage
        self.no_bid_handler = no_bid_handler
        # Organizer emergency freeze (Module K), checked first in each participant mutation.
        self.freeze_guard = freeze_guard

    def bind(self, session: Session) -> "BackendModules":
        runtime = copy(self)
        runtime.bound_session = session
        return runtime

    @contextmanager
    def _operation(self):
        with nullcontext(self.bound_session) if self.bound_session is not None else self.session_factory() as session:
            try:
                with nullcontext() if session.in_transaction() else session.begin():
                    yield session
                if self.bound_session is not None:
                    session.commit()
            except Exception:
                session.rollback()
                raise

    def _audit(self, session, actor, action, auction_id):
        if actor is not None:
            audit(
                session,
                actor,
                f"auction.{action}",
                target_type="auction",
                target_id=auction_id,
                reason=f"Organizer {action.replace('_', ' ')}",
            )

    def _check_open(self, session: Session, scope: str) -> None:
        if self.freeze_guard is not None:
            self.freeze_guard(session, scope)

    def _trading(self, session: Session) -> TradingService:
        adapters = self.adapter_factory(session)
        adapters.check_session(session)
        return TradingService(
            trades=TradeRepository(session),
            market=adapters.market,
            pricing=adapters.pricing,
            ledger=adapters.ledger,
            inventory=adapters.inventory,
            catalog=adapters.catalog,
            brokerage=self.brokerage,
            resale_rules=self.resale_rules,
        )

    def _auction(self, session: Session) -> AuctionService:
        adapters = self.adapter_factory(session)
        adapters.check_session(session)
        return AuctionService(
            repository=AuctionRepository(session),
            adapters=adapters,
            no_bid_handler=self.no_bid_handler,
        )

    def purchase(self, team_id: UUID, request: PurchaseRequest) -> TradeResponse:
        with self._operation() as session:
            self._check_open(session, "TRADING")
            result = TradeResponse.model_validate(self._trading(session).purchase(team_id=team_id, request=request))
        return result

    def sell(self, team_id: UUID, request: SellRequest) -> TradeResponse:
        with self._operation() as session:
            self._check_open(session, "TRADING")
            result = TradeResponse.model_validate(self._trading(session).sell(team_id=team_id, request=request))
        return result

    def history(self, team_id: UUID, *, limit: int = 50, offset: int = 0) -> list[TradeResponse]:
        with self._operation() as session:
            result = [
                TradeResponse.model_validate(t)
                for t in self._trading(session).history(team_id=team_id, limit=limit, offset=offset)
            ]
        return result

    def trade_detail(self, team_id: UUID, trade_id: UUID) -> TradeResponse:
        with self._operation() as session:
            result = TradeResponse.model_validate(self._trading(session).detail(team_id=team_id, trade_id=trade_id))
        return result

    def bid(self, team_id: UUID, auction_id: UUID, request: BidRequest) -> MyBid:
        with self._operation() as session:
            self._check_open(session, "BIDDING")
            result = self._auction(session).bid(team_id=team_id, auction_id=auction_id, request=request)
        return result

    def settle(self, auction_id: UUID, actor: OrganizerPrincipal | None = None) -> SettlementResponse:
        with self._operation() as session:
            result = SettlementResponse.model_validate(self._auction(session).settle(auction_id))
            self._audit(session, actor, "settle", auction_id)
        return result

    def auction_view(self, team_id: UUID, auction_id: UUID) -> AuctionView:
        with self._operation() as session:
            result = self._auction(session).view(auction_id)
        return result

    def auctions(self, *, admin: bool, limit: int, offset: int) -> list[AuctionView]:
        with self._operation() as session:
            return self._auction(session).list_auctions(admin=admin, limit=limit, offset=offset)

    def resale_quote(self, team_id: UUID, request: SellRequest) -> dict:
        with self._operation() as session:
            self._check_open(session, "TRADING")
            return self._trading(session).resale_quote(team_id=team_id, request=request)

    def my_bid(self, team_id: UUID, auction_id: UUID) -> MyBid | None:
        with self._operation() as session:
            result = self._auction(session).my_bid(team_id=team_id, auction_id=auction_id)
        return result

    def create_auction(self, request: AuctionCreate, actor: OrganizerPrincipal | None = None) -> AuctionView:
        with self._operation() as session:
            result = AuctionView.model_validate(self._auction(session).create(request))
            self._audit(session, actor, "create", result.id)
        return result

    def auction_transition(
        self, auction_id: UUID, action: str, *, amount: int | None = None, actor: OrganizerPrincipal | None = None
    ) -> AuctionView:
        with self._operation() as session:
            service = self._auction(session)
            if action == "open":
                auction = service.open(auction_id)
            elif action == "close":
                auction = service.close(auction_id)
            elif action == "cancel":
                auction = service.cancel(auction_id)
            elif action == "minimum" and amount is not None:
                auction = service.set_minimum(auction_id, amount)
            else:
                raise ValueError("Unsupported auction action.")
            result = AuctionView.model_validate(auction)
            self._audit(session, actor, action, auction_id)
        return result


def get_runtime(request: Request, session: Session = Depends(get_db)) -> BackendModules:
    runtime = getattr(request.app.state, "backend_runtime", None)
    if runtime is None:
        raise ConfigurationRequired()
    return runtime.bind(session)
