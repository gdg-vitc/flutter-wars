"""Module E: organizer routes for a team's wallet and credits.

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
from app.modules.ledger import service as ledger
from app.modules.ledger.schemas import AdminCreditIn, InitialGrantIn, LedgerEntryAdminOut, WalletOut, WalletVerifyOut

router = APIRouter(prefix="/admin/teams/{team_id}", tags=["admin: wallet"])

_view = require_permission(Permission.VIEW)
_credits = require_permission(Permission.CREDITS_ADJUST)


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
