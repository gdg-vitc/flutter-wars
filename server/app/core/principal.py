"""Shared JWT authentication dependency; Module B verifies the token through its gateway."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.contracts.identity import IdentityGateway
from app.contracts.principal import Principal
from app.core.db import get_db
from app.core.errors import AppError
from app.core.services import gateway

_bearer_scheme = HTTPBearer(auto_error=False)


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: Annotated[Session, Depends(get_db)],
) -> Principal:
    """Verify a participant JWT and resolve current identity/team membership."""
    if credentials is None:
        raise AppError("AUTHENTICATION_REQUIRED", "Authentication is required.", 401)
    return gateway(IdentityGateway, session).principal_for_token(
        credentials.credentials, request.app.state.settings
    )
