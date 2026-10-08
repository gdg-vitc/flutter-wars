from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.integration.runtime import BackendModules

from .schemas import PurchaseRequest, SellRequest, TradeResponse


def build_router(runtime_dependency: Callable, team_dependency: Callable) -> APIRouter:
    router = APIRouter(tags=["trading"])

    @router.post("/market/purchase", response_model=TradeResponse)
    def purchase(
        request: PurchaseRequest,
        runtime: BackendModules = Depends(runtime_dependency),
        team_id: UUID = Depends(team_dependency),
    ):
        return runtime.purchase(team_id, request)

    @router.post("/market/sell", response_model=TradeResponse)
    def sell(
        request: SellRequest,
        runtime: BackendModules = Depends(runtime_dependency),
        team_id: UUID = Depends(team_dependency),
    ):
        return runtime.sell(team_id, request)

    @router.get("/transactions", response_model=list[TradeResponse])
    def history(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        runtime: BackendModules = Depends(runtime_dependency),
        team_id: UUID = Depends(team_dependency),
    ):
        return runtime.history(team_id, limit=limit, offset=offset)

    @router.get("/transactions/{trade_id}", response_model=TradeResponse)
    def detail(
        trade_id: UUID,
        runtime: BackendModules = Depends(runtime_dependency),
        team_id: UUID = Depends(team_dependency),
    ):
        return runtime.trade_detail(team_id, trade_id)

    return router
