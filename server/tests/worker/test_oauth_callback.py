"""OAuth browser-to-app handoff without a live Google or database connection."""

import asyncio
import importlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import install_exception_handlers
from app.modules.authentication.jwt import create_access_token, decode_access_token

auth = importlib.import_module("app.modules.authentication.router")


@pytest.mark.parametrize("case", ["success", "invalid_state", "invalid_google", "unregistered"])
def test_oauth_callback_handoff(case, monkeypatch):
    monkeypatch.setattr(auth.sessions, "rate_limit", lambda *args: None)
    app = FastAPI()
    app.state.settings = get_settings()
    app.state.google_code_exchange = lambda code: "verified-id-token"
    app.state.google_token_verifier = lambda token: (
        None if case == "invalid_google" else {"google_subject": "subject", "email": "user@example.test"}
    )
    principal = SimpleNamespace(user_id="user-1", email="user@example.test", team_id=None, role="OWNER")
    monkeypatch.setattr(
        auth,
        "AuthenticationService",
        lambda *args: SimpleNamespace(
            principal_for_google_identity=lambda *args: None if case == "unregistered" else principal
        ),
    )

    class FakeSession:
        def exec(self, statement):
            return SimpleNamespace(first=lambda: None)

    async def no_database():
        return FakeSession()

    monkeypatch.setattr(
        auth.sessions,
        "issue",
        lambda session, settings, p: auth.TokenResponse(
            access_token=create_access_token(
                user_id=p.user_id, email=p.email, team_id=None, role=p.role, settings=settings
            ),
            refresh_token="r" * 43,
            expires_in=settings.auth_access_token_minutes * 60,
        ),
    )

    app.dependency_overrides[get_db] = no_database
    install_exception_handlers(app)
    app.include_router(auth.router)

    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://backend.test") as client:
            client.cookies.set("google_oauth_state", "expected")
            state = "wrong" if case == "invalid_state" else "expected"
            response = await client.get("/auth/google/callback", params={"code": "test-code", "state": state})
            if case != "success":
                assert response.status_code == {"invalid_state": 400, "invalid_google": 401, "unregistered": 403}[case]
                assert "location" not in response.headers
                return
            assert response.status_code == 302
            target = urlsplit(response.headers["location"])
            assert (target.scheme, target.netloc, target.path, target.fragment) == (
                "flutterwars",
                "auth-callback",
                "",
                "",
            )
            query = parse_qs(target.query)
            assert set(query) == {"access_token", "refresh_token"}
            assert query["refresh_token"] == ["r" * 43]
            assert decode_access_token(query["access_token"][0], app.state.settings)["sub"] == "user-1"
            assert response.headers["cache-control"] == "no-store"
            assert "Max-Age=0" in response.headers["set-cookie"]
            # The direct Google-token endpoint continues to serve JSON to API clients.
            direct = await client.post("/auth/google", json={"credential": "verified-id-token"})
            assert direct.status_code == 200
            assert decode_access_token(direct.json()["access_token"], app.state.settings)["sub"] == "user-1"

    asyncio.run(check())
