"""Module F HTTP routes (participant side). Organizer routes are in admin_router.py."""

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.auth import Principal, require_participant
from app.core.db import get_db
from app.modules.inventory import service
from app.modules.inventory.schemas import InventoryItemOut, InventoryOut

router = APIRouter(tags=["inventory"])


@router.get("/inventory", response_model=InventoryOut)
def read_inventory(p: Principal = Depends(require_participant), s: Session = Depends(get_db)) -> InventoryOut:
    assert p.team_id is not None
    items = service.get_team_inventory(s, p.team_id)
    return InventoryOut(team_id=p.team_id, items=[InventoryItemOut(**asdict(i)) for i in items])
