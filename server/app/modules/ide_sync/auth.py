"""FastAPI dependencies for API-key and organizer authorization boundaries."""

from typing import Annotated

from fastapi import Depends, Header
from sqlmodel import Session

from app.contracts.principal import Principal
from app.core.db import get_db
from app.core.errors import AppError
from app.core.principal import get_principal
from app.modules.ide_sync.service import ApiKeyPrincipal, ApiKeyService


def get_api_key_principal(
    api_key: Annotated[str | None, Header(alias="X-Team-API-Key")] = None,
    session: Annotated[Session, Depends(get_db)] = None,
) -> ApiKeyPrincipal:
    """Resolve team identity exclusively from the IDE credential."""
    return ApiKeyService(session).authenticate(api_key)


async def require_organizer(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    """Module B supplies the principal; C only enforces its organizer role."""
    if principal.role not in {"organizer", "admin"}:
        raise AppError("FORBIDDEN", "Organizer access is required.", 403)
    return principal
