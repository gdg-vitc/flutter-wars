"""Module F: organizer routes for a team's widget inventory.

Permission check from Module K (app.core.auth.require_permission); every change
writes its audit row through Module K's gateway in the same transaction.
"""

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.contracts.admin import AuditAction, OrganizerPrincipal, Permission
from app.core.audit import audit
from app.core.auth import require_permission
from app.core.db import get_db
from app.modules.inventory import service as inventory
from app.modules.inventory.schemas import (
    InventoryAdjustIn,
    InventoryEventOut,
    InventoryItemOut,
    InventoryMismatchOut,
    InventoryOut,
    InventoryVerifyOut,
)

router = APIRouter(prefix="/admin/teams/{team_id}", tags=["admin: inventory"])

_view = require_permission(Permission.VIEW)
_widgets = require_permission(Permission.INVENTORY_ADJUST)


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
