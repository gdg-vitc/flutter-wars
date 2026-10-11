"""Module E HTTP routes (participant side). Thin: auth + validation + one service call.

Organizer routes for wallets are in admin_router.py, with Module K's permission check
and audit log."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.auth import Principal, require_participant
from app.core.db import get_db
from app.modules.ledger import service
from app.modules.ledger.schemas import LedgerEntryOut, LedgerPage, WalletOut

router = APIRouter(tags=["wallet"])


# ---------------- participant (team comes from the token, never from the URL/body)


@router.get("/wallet", response_model=WalletOut)
def read_wallet(p: Principal = Depends(require_participant), s: Session = Depends(get_db)) -> WalletOut:
    assert p.team_id is not None
    return WalletOut(**asdict(service.get_wallet(s, p.team_id)))


@router.get("/wallet/ledger", response_model=LedgerPage)
def read_ledger(
    cursor: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    p: Principal = Depends(require_participant),
    s: Session = Depends(get_db),
) -> LedgerPage:
    assert p.team_id is not None
    rows, next_cursor = service.list_ledger(s, p.team_id, cursor=cursor, limit=limit)
    return LedgerPage(items=[LedgerEntryOut.model_validate(r) for r in rows], next_cursor=next_cursor)
