"""HTTP routes for Module B authentication and identity."""

import base64
import hashlib
import inspect
import re
import secrets
from datetime import timedelta
from secrets import token_urlsafe
from typing import Annotated, Any, Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from app.contracts.principal import Principal
from app.core.db import get_db
from app.core.errors import AppError
from app.core.principal import get_principal
from app.modules.authentication import sessions
from app.modules.authentication.google import verify_google_token
from app.modules.authentication.oauth import authorization_url, exchange_code
from app.modules.authentication.schemas import (
    CodeExchangeRequest,
    GoogleLoginRequest,
    MeResponse,
    RefreshRequest,
    TokenResponse,
)
from app.modules.authentication.service import AuthenticationService

router = APIRouter(prefix="/auth", tags=["authentication"])
_OAUTH_STATE_COOKIE = "google_oauth_state"


async def _resolved(value: Any) -> Any:
    """Tests may install synchronous hooks; the real Google calls are async."""
    return await value if inspect.isawaitable(value) else value


def _google_verifier(request: Request) -> Callable[[str], Any]:
    configured = getattr(request.app.state, "google_token_verifier", None)
    if configured is not None:
        return configured
    return lambda credential: verify_google_token(credential, request.app.state.settings.google_oauth_client_id)


def _issue_token(request: Request, session: Session, google_identity: dict[str, str]) -> TokenResponse:
    service = AuthenticationService(session, request.app.state.settings)
    principal = service.principal_for_google_identity(google_identity["google_subject"], google_identity["email"])
    if principal is None:
        raise AppError("ACCOUNT_NOT_ALLOWED", "Google account is not registered for this workshop.", 403)
    return sessions.issue(session, request.app.state.settings, principal)


@router.get("/google/login", include_in_schema=True)
def google_login_redirect(
    request: Request,
    session: Annotated[Session, Depends(get_db)],
    code_challenge: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_-]{43}$")] = None,
    code_challenge_method: Annotated[str | None, Query(pattern="^S256$")] = None,
) -> RedirectResponse:
    sessions.rate_limit(session, request)
    state = ("pkce." if code_challenge is not None else "") + token_urlsafe(32)
    if code_challenge is not None:
        if code_challenge_method != "S256":
            raise AppError("PKCE_REQUIRED", "Use the S256 code challenge method.", 400)
        session.add(
            sessions.OAuthAttempt(
                state_hash=sessions.digest(state),
                challenge=code_challenge,
                expires_at=sessions.now() + timedelta(minutes=10),
            )
        )
    elif code_challenge_method is not None:
        raise AppError("PKCE_REQUIRED", "A code challenge is required.", 400)
    response = RedirectResponse(authorization_url(request.app.state.settings, state), status_code=302)
    response.set_cookie(
        _OAUTH_STATE_COOKIE,
        state,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=600,
    )
    return response


@router.get("/google/callback", response_class=RedirectResponse, status_code=302)
async def google_login_callback(
    request: Request,
    code: Annotated[str, Query(min_length=1)],
    state: Annotated[str, Query(min_length=1)],
    session: Annotated[Session, Depends(get_db)],
) -> RedirectResponse:
    sessions.rate_limit(session, request)
    expected_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    if not expected_state or expected_state != state:
        raise AppError("OAUTH_STATE_INVALID", "OAuth sign-in could not be validated.", 400)
    configured_exchange = getattr(request.app.state, "google_code_exchange", None)
    id_token = await _resolved(
        configured_exchange(code)
        if configured_exchange is not None
        else exchange_code(code, request.app.state.settings)
    )
    google_identity = await _resolved(_google_verifier(request)(id_token))
    if google_identity is None:
        raise AppError("INVALID_GOOGLE_CREDENTIAL", "Google credential is invalid.", 401)
    attempt = session.exec(
        select(sessions.OAuthAttempt)
        .where(sessions.OAuthAttempt.state_hash == sessions.digest(state))
        .with_for_update()
    ).first()
    if state.startswith("pkce.") and attempt is None:
        raise AppError("OAUTH_STATE_INVALID", "OAuth sign-in could not be validated.", 400)
    if attempt is not None:
        if attempt.consumed_at is not None or attempt.expires_at <= sessions.now():
            raise AppError("OAUTH_STATE_INVALID", "OAuth sign-in could not be validated.", 400)
        principal = AuthenticationService(session, request.app.state.settings).principal_for_google_identity(
            google_identity["google_subject"], google_identity["email"]
        )
        if principal is None:
            raise AppError("ACCOUNT_NOT_ALLOWED", "Google account is not registered for this workshop.", 403)
        raw = token_urlsafe(32)
        attempt.consumed_at = sessions.now()
        session.add(attempt)
        session.add(
            sessions.LoginCode(
                token_hash=sessions.digest(raw),
                user_id=int(principal.user_id),
                challenge=attempt.challenge,
                expires_at=sessions.now() + timedelta(seconds=60),
            )
        )
        parameters = {"code": raw}
    else:
        token = _issue_token(request, session, google_identity)
        parameters = {"access_token": token.access_token, "refresh_token": token.refresh_token}
    response = RedirectResponse(
        "flutterwars://auth-callback?" + urlencode(parameters),
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    response.delete_cookie(_OAUTH_STATE_COOKIE)
    return response


@router.post("/google", response_model=TokenResponse)
async def google_login(
    payload: GoogleLoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    sessions.rate_limit(session, request)
    google_identity = await _resolved(_google_verifier(request)(payload.credential))
    if google_identity is None:
        raise AppError("INVALID_GOOGLE_CREDENTIAL", "Google credential is invalid.", 401)
    response.headers["Cache-Control"] = "no-store"
    return _issue_token(request, session, google_identity)


@router.get("/me", response_model=MeResponse)
def get_me(principal: Annotated[Principal, Depends(get_principal)]) -> MeResponse:
    return MeResponse(
        user_id=principal.user_id,
        email=principal.email,
        team_id=str(principal.team_id) if principal.team_id else None,
        role=principal.role,
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    payload: RefreshRequest, request: Request, response: Response, session: Annotated[Session, Depends(get_db)]
) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    sessions.rate_limit(session, request)
    return sessions.rotate(session, request.app.state.settings, payload.refresh_token.get_secret_value())


@router.post("/token", response_model=TokenResponse)
def exchange_login_code(
    payload: CodeExchangeRequest, request: Request, response: Response, session: Annotated[Session, Depends(get_db)]
) -> TokenResponse:
    sessions.rate_limit(session, request)
    raw, verifier = payload.code.get_secret_value(), payload.code_verifier.get_secret_value()
    row = session.exec(
        select(sessions.LoginCode).where(sessions.LoginCode.token_hash == sessions.digest(raw)).with_for_update()
    ).first()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if (
        row is None
        or row.consumed_at is not None
        or row.expires_at <= sessions.now()
        or re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier) is None
        or not secrets.compare_digest(row.challenge, challenge)
    ):
        raise AppError("INVALID_LOGIN_CODE", "Login code is invalid or expired.", 401)
    principal = sessions.current_principal(session, request.app.state.settings, row.user_id)
    row.consumed_at = sessions.now()
    session.add(row)
    response.headers["Cache-Control"] = "no-store"
    return sessions.issue(session, request.app.state.settings, principal)


@router.post("/logout", status_code=204)
def logout(payload: RefreshRequest, session: Annotated[Session, Depends(get_db)]) -> Response:
    credential = session.get(sessions.RefreshCredential, sessions.digest(payload.refresh_token.get_secret_value()))
    if credential is not None:
        row = session.exec(
            select(sessions.AuthSession).where(sessions.AuthSession.id == credential.session_id).with_for_update()
        ).one()
        row.revoked_at = sessions.now()
        session.add(row)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/logout-all", status_code=204)
def logout_all(
    principal: Annotated[Principal, Depends(get_principal)], session: Annotated[Session, Depends(get_db)]
) -> Response:
    rows = session.exec(
        select(sessions.AuthSession)
        .where(sessions.AuthSession.user_id == int(principal.user_id))
        .order_by(sessions.AuthSession.id)
        .with_for_update()
    ).all()
    for row in rows:
        row.revoked_at = sessions.now()
        session.add(row)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
