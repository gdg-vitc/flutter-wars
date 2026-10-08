from collections.abc import Callable
from uuid import UUID

from sqlmodel import Session

from app.integration.contracts import Adapters
from app.integration.errors import (
    AuctionNotOpen,
    BidBelowMinimum,
    BidNotIncreasing,
    ConfigurationRequired,
    IdempotencyConflict,
    NotFound,
)
from app.integration.transactions import lock_request, require_transaction

from .models import Auction, AuctionResult, AuctionState, Bid, BidReceipt
from .repository import AuctionRepository
from .schemas import AuctionCreate, AuctionView, BidRequest, MyBid

# Explicit owner policy, invoked in the same transaction. No default allocation outcome.
NoBidHandler = Callable[[Session, Auction, Adapters], None]


class AuctionService:
    def __init__(
        self,
        *,
        repository: AuctionRepository,
        adapters: Adapters,
        no_bid_handler: NoBidHandler | None = None,
    ):
        self._repo = repository
        self._adapters = adapters
        self._no_bid_handler = no_bid_handler

    def _guard(self, auction_id: UUID, operation: str) -> Auction:
        require_transaction(self._repo.session)
        snapshot = self._repo.auction(auction_id)
        identity = (snapshot.round_id, snapshot.listing_id, snapshot.widget_id, snapshot.quantity)
        self._adapters.market.guard_auction(
            auction_id=auction_id,
            round_id=snapshot.round_id,
            listing_id=snapshot.listing_id,
            widget_id=snapshot.widget_id,
            quantity=snapshot.quantity,
            operation=operation,
        )
        auction = self._repo.auction(auction_id, lock=True)
        if identity != (auction.round_id, auction.listing_id, auction.widget_id, auction.quantity):
            raise ConfigurationRequired("Auction identity changed during lifecycle guard.")
        return auction

    def create(self, request: AuctionCreate) -> Auction:
        require_transaction(self._repo.session)
        request = AuctionCreate.model_validate(request.model_dump())
        auction = Auction.model_validate(request.model_dump())
        self._repo.add(auction)
        # DRAFT creates no ownership or credit effects. Market must allocate before open.
        return auction

    def open(self, auction_id: UUID) -> Auction:
        auction = self._guard(auction_id, "bid")
        if auction.state != AuctionState.DRAFT or self._repo.now() >= auction.closes_at:
            raise AuctionNotOpen()
        if auction.minimum_bid is None:
            raise ConfigurationRequired()
        auction.state = AuctionState.OPEN
        self._repo.add(auction)
        return auction

    def close(self, auction_id: UUID) -> Auction:
        auction = self._guard(auction_id, "settle")
        if auction.state == AuctionState.DRAFT or self._repo.now() < auction.closes_at:
            raise AuctionNotOpen()
        if auction.state == AuctionState.OPEN:
            auction.state = AuctionState.CLOSED
            self._repo.add(auction)
        return auction

    def set_minimum(self, auction_id: UUID, amount: int) -> Auction:
        require_transaction(self._repo.session)
        auction = self._repo.auction(auction_id, lock=True)
        if auction.state != AuctionState.DRAFT:
            raise AuctionNotOpen()
        if type(amount) is not int or not 0 <= amount <= 2147483647:
            raise ValueError("Invalid minimum bid.")
        auction.minimum_bid = amount
        self._repo.add(auction)
        return auction

    def bid(self, *, team_id: UUID, auction_id: UUID, request: BidRequest) -> MyBid:
        require_transaction(self._repo.session)
        request = BidRequest.model_validate(request.model_dump())
        lock_request(
            self._repo.session, scope=f"bid:{auction_id}:{team_id}:{request.idempotency_key}"
        )
        receipt = self._repo.receipt(
            auction_id=auction_id, team_id=team_id, key=request.idempotency_key
        )
        if receipt:
            if receipt.amount != request.amount:
                raise IdempotencyConflict()
            return MyBid(
                id=receipt.bid_id,
                auction_id=auction_id,
                amount=receipt.amount,
                amount_reached_at=receipt.amount_reached_at,
            )
        auction = self._guard(auction_id, "bid")
        self._adapters.ledger.lock_accounts(team_ids=[team_id])
        # Check wall clock after ALL potentially blocking guards, not transaction-start time.
        now = self._repo.now()
        if auction.state != AuctionState.OPEN or not auction.starts_at <= now < auction.closes_at:
            raise AuctionNotOpen()
        if auction.minimum_bid is None:
            raise ConfigurationRequired()
        if request.amount < auction.minimum_bid:
            raise BidBelowMinimum()
        existing = self._repo.own_bid(auction_id=auction_id, team_id=team_id)
        if existing and request.amount <= existing.amount:
            raise BidNotIncreasing()
        previous = existing.amount if existing else 0
        auction.accepted_bid_order += 1
        current = existing or Bid(
            auction_id=auction_id,
            team_id=team_id,
            amount=request.amount,
            amount_reached_at=now,
            amount_reached_order=auction.accepted_bid_order,
            created_at=now,
            updated_at=now,
        )
        self._adapters.ledger.reserve(
            team_id=team_id, reservation_id=current.id, additional_amount=request.amount - previous
        )
        current.amount = request.amount
        current.amount_reached_at = now
        current.amount_reached_order = auction.accepted_bid_order
        current.updated_at = now
        self._repo.add(auction)
        self._repo.add(current)
        self._repo.add(
            BidReceipt(
                auction_id=auction_id,
                team_id=team_id,
                idempotency_key=request.idempotency_key,
                bid_id=current.id,
                amount=request.amount,
                amount_reached_at=now,
            )
        )
        return MyBid.model_validate(current)

    def settle(self, auction_id: UUID) -> AuctionResult:
        require_transaction(self._repo.session)
        result = self._repo.result(auction_id)
        if result:
            return result
        auction = self._guard(auction_id, "settle")
        result = self._repo.result(auction_id)
        if result:
            return result
        now = self._repo.now()
        if auction.state not in (AuctionState.OPEN, AuctionState.CLOSED) or now < auction.closes_at:
            raise AuctionNotOpen()
        bids = self._repo.bids(auction_id)
        self._adapters.ledger.lock_accounts(team_ids=sorted({bid.team_id for bid in bids}, key=str))
        winner = bids[0] if bids else None
        if winner:
            self._adapters.catalog.validate_widget(widget_id=auction.widget_id, operation="award")
            for bid in bids:
                if bid.id == winner.id:
                    self._adapters.ledger.settle(
                        team_id=bid.team_id,
                        reservation_id=bid.id,
                        amount=bid.amount,
                        reference=auction.id,
                    )
                else:
                    self._adapters.ledger.release(team_id=bid.team_id, reservation_id=bid.id)
            self._adapters.market.consume_auction_allocation(auction_id=auction.id)
            self._adapters.inventory.add(
                team_id=winner.team_id,
                widget_id=auction.widget_id,
                quantity=auction.quantity,
                reference=auction.id,
            )
        else:
            if self._no_bid_handler is None:
                raise ConfigurationRequired("No-bid allocation policy is required.")
            self._no_bid_handler(self._repo.session, auction, self._adapters)
        result = AuctionResult(
            auction_id=auction.id,
            winner_team_id=winner.team_id if winner else None,
            winning_amount=winner.amount if winner else None,
            settled_at=self._repo.now(),
        )
        self._repo.add(result)
        auction.state = AuctionState.SETTLED
        self._repo.add(auction)
        return result

    def _published(self, auction_id: UUID) -> Auction:
        """Participant reads: a DRAFT auction is unpublished and answers as missing.

        Leaving DRAFT requires the market guard on an OPEN round, so a published
        auction's round is already visible to participants.
        """
        auction = self._repo.auction(auction_id)
        if auction.state == AuctionState.DRAFT:
            raise NotFound()
        return auction

    def view(self, auction_id: UUID) -> AuctionView:
        auction = self._published(auction_id)
        view = AuctionView.model_validate(auction)
        if view.state == AuctionState.OPEN and self._repo.now() >= view.closes_at:
            view.state = AuctionState.CLOSED
        return view

    def my_bid(self, *, team_id: UUID, auction_id: UUID) -> MyBid | None:
        self._published(auction_id)
        bid = self._repo.own_bid(auction_id=auction_id, team_id=team_id)
        return MyBid.model_validate(bid) if bid else None
