from collections.abc import Callable
from uuid import UUID

from sqlmodel import Session

from app.auction.repository import AuctionRepository
from app.auction.schemas import AuctionCreate, AuctionView, BidRequest, MyBid, SettlementResponse
from app.auction.service import AuctionService, NoBidHandler
from app.trading.policies import DEFAULT_BROKERAGE
from app.trading.repository import TradeRepository
from app.trading.schemas import PurchaseRequest, SellRequest, TradeResponse
from app.trading.service import TradingService

from .contracts import Adapters, BrokeragePolicy


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
    ):
        self.session_factory = session_factory
        self.adapter_factory = adapter_factory
        self.brokerage = brokerage
        self.no_bid_handler = no_bid_handler

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
        with self.session_factory() as session, session.begin():
            result = TradeResponse.model_validate(
                self._trading(session).purchase(team_id=team_id, request=request)
            )
        return result

    def sell(self, team_id: UUID, request: SellRequest) -> TradeResponse:
        with self.session_factory() as session, session.begin():
            result = TradeResponse.model_validate(
                self._trading(session).sell(team_id=team_id, request=request)
            )
        return result

    def history(self, team_id: UUID, *, limit: int = 50, offset: int = 0) -> list[TradeResponse]:
        with self.session_factory() as session, session.begin():
            result = [
                TradeResponse.model_validate(t)
                for t in self._trading(session).history(team_id=team_id, limit=limit, offset=offset)
            ]
        return result

    def trade_detail(self, team_id: UUID, trade_id: UUID) -> TradeResponse:
        with self.session_factory() as session, session.begin():
            result = TradeResponse.model_validate(
                self._trading(session).detail(team_id=team_id, trade_id=trade_id)
            )
        return result

    def bid(self, team_id: UUID, auction_id: UUID, request: BidRequest) -> MyBid:
        with self.session_factory() as session, session.begin():
            result = self._auction(session).bid(
                team_id=team_id, auction_id=auction_id, request=request
            )
        return result

    def settle(self, auction_id: UUID) -> SettlementResponse:
        with self.session_factory() as session, session.begin():
            result = SettlementResponse.model_validate(self._auction(session).settle(auction_id))
        return result

    def auction_view(self, team_id: UUID, auction_id: UUID) -> AuctionView:
        with self.session_factory() as session, session.begin():
            result = self._auction(session).view(auction_id)
        return result

    def my_bid(self, team_id: UUID, auction_id: UUID) -> MyBid | None:
        with self.session_factory() as session, session.begin():
            result = self._auction(session).my_bid(team_id=team_id, auction_id=auction_id)
        return result

    def create_auction(self, request: AuctionCreate) -> AuctionView:
        with self.session_factory() as session, session.begin():
            result = AuctionView.model_validate(self._auction(session).create(request))
        return result

    def auction_transition(
        self, auction_id: UUID, action: str, *, amount: int | None = None
    ) -> AuctionView:
        with self.session_factory() as session, session.begin():
            service = self._auction(session)
            if action == "open":
                auction = service.open(auction_id)
            elif action == "close":
                auction = service.close(auction_id)
            elif action == "minimum" and amount is not None:
                auction = service.set_minimum(auction_id, amount)
            else:
                raise ValueError("Unsupported auction action.")
            result = AuctionView.model_validate(auction)
        return result
