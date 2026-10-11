"""HTTP adapter for Module C's lifecycle and read-only synchronization APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.contracts.admin import OrganizerPrincipal
from app.contracts.identity import IdentityGateway
from app.contracts.inventory import InventoryGateway
from app.core.audit import audit
from app.core.db import get_db
from app.core.errors import AppError
from app.core.services import gateway
from app.core.transactions import lock_request
from app.modules.ide_sync.auth import get_api_key_principal, require_organizer
from app.modules.ide_sync.schemas import IdeState, IdeWidgetState, IssuedApiKey, RevokedApiKey
from app.modules.ide_sync.service import ApiKeyPrincipal, ApiKeyService

router = APIRouter(tags=["ide-sync"])


@router.post(
    "/admin/teams/{team_id}/api-keys",
    response_model=IssuedApiKey,
    status_code=status.HTTP_201_CREATED,
)
def issue_api_key(
    team_id: UUID,
    organizer: Annotated[OrganizerPrincipal, Depends(require_organizer)],
    session: Annotated[Session, Depends(get_db)] = None,
) -> IssuedApiKey:
    if gateway(IdentityGateway, session).get_team(team_id) is None:
        raise AppError("TEAM_NOT_FOUND", "Team not found.", 404)
    lock_request(session, scope=f"ide-key:{team_id}")
    record, plaintext_key = ApiKeyService(session).issue(str(team_id))
    audit(
        session,
        organizer,
        "ide.api_key_issue",
        target_type="team",
        target_id=team_id,
        reason="Organizer rotated IDE credential",
        details={"key_id": record.key_id},
    )
    return IssuedApiKey(key_id=record.key_id, api_key=plaintext_key, created_at=record.created_at)


@router.delete("/admin/teams/{team_id}/api-keys/{key_id}", response_model=RevokedApiKey)
def revoke_api_key(
    team_id: UUID,
    key_id: str,
    organizer: Annotated[OrganizerPrincipal, Depends(require_organizer)],
    session: Annotated[Session, Depends(get_db)] = None,
) -> RevokedApiKey:
    session.connection()
    lock_request(session, scope=f"ide-key:{team_id}")
    record = ApiKeyService(session).revoke(str(team_id), key_id)
    audit(
        session,
        organizer,
        "ide.api_key_revoke",
        target_type="team",
        target_id=team_id,
        reason="Organizer revoked IDE credential",
        details={"key_id": record.key_id},
    )
    assert record.revoked_at is not None
    return RevokedApiKey(key_id=record.key_id, status=record.status, revoked_at=record.revoked_at)


@router.get("/ide/state", response_model=IdeState)
def ide_state(
    principal: Annotated[ApiKeyPrincipal, Depends(get_api_key_principal)],
    session: Annotated[Session, Depends(get_db)] = None,
) -> IdeState:
    widgets = gateway(InventoryGateway, session).get_team_inventory(UUID(principal.team_id))
    return IdeState(
        team_id=principal.team_id,
        widgets=[IdeWidgetState(widget_id=item.widget_id, quantity=item.quantity) for item in widgets],
    )
