from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.integration.runtime import BackendModules

from .schemas import AuctionCreate, AuctionView, BidRequest, MyBid, SettlementResponse


class MinimumBidRequest(BaseModel):
    amount: int = Field(strict=True, ge=0, le=2147483647)


def build_router(
    runtime_dependency: Callable, team_dependency: Callable, organizer_dependency: Callable
) -> APIRouter:
    router = APIRouter(tags=["auction"])

    @router.get("/auctions/{auction_id}", response_model=AuctionView)
    def view(
        auction_id: UUID,
        runtime: BackendModules = Depends(runtime_dependency),
        team_id: UUID = Depends(team_dependency),
    ):
        return runtime.auction_view(team_id, auction_id)

    @router.get("/auctions/{auction_id}/my-bid", response_model=MyBid | None)
    def my_bid(
        auction_id: UUID,
        runtime: BackendModules = Depends(runtime_dependency),
        team_id: UUID = Depends(team_dependency),
    ):
        return runtime.my_bid(team_id, auction_id)

    @router.post("/auctions/{auction_id}/bids", response_model=MyBid)
    def bid(
        auction_id: UUID,
        request: BidRequest,
        runtime: BackendModules = Depends(runtime_dependency),
        team_id: UUID = Depends(team_dependency),
    ):
        return runtime.bid(team_id, auction_id, request)

    @router.post(
        "/admin/auctions", response_model=AuctionView, dependencies=[Depends(organizer_dependency)]
    )
    def create(request: AuctionCreate, runtime: BackendModules = Depends(runtime_dependency)):
        return runtime.create_auction(request)

    @router.post(
        "/admin/auctions/{auction_id}/open",
        response_model=AuctionView,
        dependencies=[Depends(organizer_dependency)],
    )
    def open_auction(auction_id: UUID, runtime: BackendModules = Depends(runtime_dependency)):
        return runtime.auction_transition(auction_id, "open")

    @router.post(
        "/admin/auctions/{auction_id}/close",
        response_model=AuctionView,
        dependencies=[Depends(organizer_dependency)],
    )
    def close_auction(auction_id: UUID, runtime: BackendModules = Depends(runtime_dependency)):
        return runtime.auction_transition(auction_id, "close")

    @router.patch(
        "/admin/auctions/{auction_id}/minimum-bid",
        response_model=AuctionView,
        dependencies=[Depends(organizer_dependency)],
    )
    def minimum(
        auction_id: UUID,
        request: MinimumBidRequest,
        runtime: BackendModules = Depends(runtime_dependency),
    ):
        return runtime.auction_transition(auction_id, "minimum", amount=request.amount)

    @router.post(
        "/admin/auctions/{auction_id}/settle",
        response_model=SettlementResponse,
        dependencies=[Depends(organizer_dependency)],
    )
    def settle(auction_id: UUID, runtime: BackendModules = Depends(runtime_dependency)):
        return runtime.settle(auction_id)

    return router
