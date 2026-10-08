"""HTTP adapter for Module C's lifecycle and read-only synchronization APIs."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlmodel import Session

from app.contracts.principal import Principal
from app.core.db import get_db
from app.modules.ide_sync.auth import get_api_key_principal, require_organizer
from app.modules.ide_sync.contracts import InventoryReader, UnconfiguredInventoryReader
from app.modules.ide_sync.schemas import IdeState, IdeWidgetState, IssuedApiKey, RevokedApiKey
from app.modules.ide_sync.service import ApiKeyPrincipal, ApiKeyService

router = APIRouter(tags=["ide-sync"])


def _inventory_reader(request: Request) -> InventoryReader:
    return getattr(request.app.state, "inventory_reader", UnconfiguredInventoryReader())


@router.post(
    "/admin/teams/{team_id}/api-keys",
    response_model=IssuedApiKey,
    status_code=status.HTTP_201_CREATED,
)
def issue_api_key(
    team_id: str,
    _: Annotated[Principal, Depends(require_organizer)],
    session: Annotated[Session, Depends(get_db)] = None,
) -> IssuedApiKey:
    record, plaintext_key = ApiKeyService(session).issue(team_id)
    return IssuedApiKey(key_id=record.key_id, api_key=plaintext_key, created_at=record.created_at)


@router.delete("/admin/teams/{team_id}/api-keys/{key_id}", response_model=RevokedApiKey)
def revoke_api_key(
    team_id: str,
    key_id: str,
    _: Annotated[Principal, Depends(require_organizer)],
    session: Annotated[Session, Depends(get_db)] = None,
) -> RevokedApiKey:
    record = ApiKeyService(session).revoke(team_id, key_id)
    assert record.revoked_at is not None
    return RevokedApiKey(key_id=record.key_id, status=record.status, revoked_at=record.revoked_at)


@router.get("/ide/state", response_model=IdeState)
def ide_state(
    request: Request,
    principal: Annotated[ApiKeyPrincipal, Depends(get_api_key_principal)],
) -> IdeState:
    widgets = _inventory_reader(request).get_team_inventory(principal.team_id)
    return IdeState(
        team_id=principal.team_id,
        widgets=[
            IdeWidgetState(widget_id=item.widget_id, quantity=item.quantity)
            for item in widgets
        ],
    )
