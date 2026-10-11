"""FastAPI dependencies for API-key and organizer authorization boundaries."""

from typing import Annotated

from fastapi import Depends, Header
from sqlmodel import Session

from app.contracts.admin import Permission
from app.core.auth import require_permission
from app.core.db import get_db
from app.modules.ide_sync.service import ApiKeyPrincipal, ApiKeyService


def get_api_key_principal(
    api_key: Annotated[str | None, Header(alias="X-Team-API-Key")] = None,
    session: Annotated[Session, Depends(get_db)] = None,
) -> ApiKeyPrincipal:
    """Resolve team identity exclusively from the IDE credential."""
    return ApiKeyService(session).authenticate(api_key)


# Module K decides who is an organizer and what they may do.
require_organizer = require_permission(Permission.API_KEYS_MANAGE)
