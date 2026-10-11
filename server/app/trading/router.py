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

    @router.get("/market/trading-rules")
    def trading_rules(runtime: BackendModules = Depends(runtime_dependency), team_id: UUID = Depends(team_dependency)):
        rules = runtime.resale_rules
        return {
            "price_min_factor": "0.98",
            "price_max_factor": "1.02",
            "max_interval_change_bps": 100,
            "resale_fee_bps": rules.fee_bps,
            "max_resale_profit_bps": rules.profit_bps,
            "event_profit_budget_bps": rules.event_profit_bps,
            "max_loss_bps": rules.max_loss_bps,
            "event_loss_budget_bps": rules.max_loss_bps,
            "explanation": "Everyone sees the same market price. Only new demand from other teams can unlock resale profit. Fees and limits are shown in your resale preview. Profit allowances do not renew between rounds. Sales beyond a loss limit are refused without taking your widgets.",
        }

    @router.post("/market/resale-quote")
    def resale_quote(
        request: SellRequest,
        runtime: BackendModules = Depends(runtime_dependency),
        team_id: UUID = Depends(team_dependency),
    ):
        return runtime.resale_quote(team_id, request)

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
