"""Module K: organizer routes for a team's credits (Module E) and widgets (Module F).

K owns every /admin route, so the permission check and the audit row live in one place.
These routes only call E's and F's public contract (app.modules.ledger / app.modules.inventory)
and reuse E's and F's request/response shapes.
"""

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.db import get_db
from app.modules import inventory, ledger
from app.modules.admin.authz import OrganizerPrincipal, require_permission
from app.modules.admin.permissions import AuditAction, Permission
from app.modules.admin.service import audit
from app.modules.inventory.schemas import (
    InventoryAdjustIn,
    InventoryEventOut,
    InventoryItemOut,
    InventoryMismatchOut,
    InventoryOut,
    InventoryVerifyOut,
)
from app.modules.ledger.schemas import AdminCreditIn, InitialGrantIn, LedgerEntryAdminOut, WalletOut, WalletVerifyOut

router = APIRouter(prefix="/admin/teams/{team_id}", tags=["admin: wallet & inventory"])

_view = require_permission(Permission.VIEW)
_credits = require_permission(Permission.CREDITS_ADJUST)
_widgets = require_permission(Permission.INVENTORY_ADJUST)


# ---------------------------------------------------------------- wallet (Module E)


@router.get("/wallet", response_model=WalletOut)
def read_wallet(team_id: UUID, _: OrganizerPrincipal = Depends(_view), s: Session = Depends(get_db)) -> WalletOut:
    return WalletOut(**asdict(ledger.get_wallet(s, team_id)))


@router.post("/credits", response_model=LedgerEntryAdminOut, status_code=201)
def adjust_credits(
    team_id: UUID,
    body: AdminCreditIn,
    org: OrganizerPrincipal = Depends(_credits),
    s: Session = Depends(get_db),
) -> LedgerEntryAdminOut:
    entry = ledger.admin_adjust(
        s, team_id, body.amount, reason=body.reason, actor=org.actor, idempotency_key=body.idempotency_key
    )
    audit(
        s,
        org,
        AuditAction.CREDITS_ADJUST,
        target_type="team",
        target_id=team_id,
        reason=body.reason,
        details={
            "amount": body.amount,
            "balance_after": entry.balance_after,
            "ledger_entry_id": entry.id,
            "idempotency_key": body.idempotency_key,
        },
    )
    out = LedgerEntryAdminOut.model_validate(entry)
    s.commit()  # ledger row + audit row: saved together or not at all
    return out


@router.post("/credits/initial-grant", response_model=LedgerEntryAdminOut, status_code=201)
def initial_grant(
    team_id: UUID,
    body: InitialGrantIn,
    org: OrganizerPrincipal = Depends(_credits),
    s: Session = Depends(get_db),
) -> LedgerEntryAdminOut:
    entry = ledger.grant_initial(s, team_id, body.amount, actor=org.actor)
    audit(
        s,
        org,
        AuditAction.CREDITS_GRANT_INITIAL,
        target_type="team",
        target_id=team_id,
        reason="Initial credit grant",
        details={"amount": body.amount, "ledger_entry_id": entry.id},
    )
    out = LedgerEntryAdminOut.model_validate(entry)
    s.commit()
    return out


@router.get("/wallet/ledger", response_model=list[LedgerEntryAdminOut])
def read_ledger(
    team_id: UUID,
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    _: OrganizerPrincipal = Depends(_view),
    s: Session = Depends(get_db),
) -> list[LedgerEntryAdminOut]:
    rows, _next = ledger.list_ledger(s, team_id, cursor=cursor, limit=limit)
    return [LedgerEntryAdminOut.model_validate(r) for r in rows]


@router.get("/wallet/verify", response_model=WalletVerifyOut)
def verify_wallet(
    team_id: UUID, _: OrganizerPrincipal = Depends(_view), s: Session = Depends(get_db)
) -> WalletVerifyOut:
    return WalletVerifyOut(**asdict(ledger.verify_wallet(s, team_id)))


# ---------------------------------------------------------------- inventory (Module F)


@router.get("/inventory", response_model=InventoryOut)
def read_inventory(
    team_id: UUID,
    include_zero: bool = Query(default=False),
    _: OrganizerPrincipal = Depends(_view),
    s: Session = Depends(get_db),
) -> InventoryOut:
    items = inventory.get_team_inventory(s, team_id, include_zero=include_zero)
    return InventoryOut(team_id=team_id, items=[InventoryItemOut(**asdict(i)) for i in items])


@router.post("/inventory/adjust", response_model=InventoryEventOut, status_code=201)
def adjust_inventory(
    team_id: UUID,
    body: InventoryAdjustIn,
    org: OrganizerPrincipal = Depends(_widgets),
    s: Session = Depends(get_db),
) -> InventoryEventOut:
    ev = inventory.admin_adjust(
        s,
        team_id,
        body.widget_id,
        body.delta,
        reason=body.reason,
        actor=org.actor,
        idempotency_key=body.idempotency_key,
    )
    audit(
        s,
        org,
        AuditAction.INVENTORY_ADJUST,
        target_type="team",
        target_id=team_id,
        reason=body.reason,
        details={
            "widget_id": body.widget_id,
            "delta": body.delta,
            "quantity_after": ev.quantity_after,
            "inventory_event_id": ev.id,
            "idempotency_key": body.idempotency_key,
        },
    )
    out = InventoryEventOut.model_validate(ev)
    s.commit()  # inventory event + audit row: saved together or not at all
    return out


@router.get("/inventory/verify", response_model=InventoryVerifyOut)
def verify_inventory(
    team_id: UUID, _: OrganizerPrincipal = Depends(_view), s: Session = Depends(get_db)
) -> InventoryVerifyOut:
    mismatches = inventory.verify_inventory(s, team_id)
    return InventoryVerifyOut(
        team_id=team_id, ok=not mismatches, mismatches=[InventoryMismatchOut(**asdict(m)) for m in mismatches]
    )
