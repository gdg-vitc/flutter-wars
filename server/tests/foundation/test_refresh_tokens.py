import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.db import get_engine
from app.core.errors import AppError
from app.modules.authentication import sessions
from app.modules.authentication.jwt import decode_access_token
from app.modules.authentication.model import Team
from tests.foundation.test_authentication import _seed_team_a


@pytest.fixture(autouse=True)
def fresh_rate_buckets():
    with Session(get_engine()) as db:
        db.execute(delete(sessions.AuthRateBucket))
        db.commit()


def login(app, client):
    user, team, subject, email = _seed_team_a()
    app.state.google_token_verifier = lambda value: {"google_subject": subject, "email": email}
    response = client.post("/auth/google", json={"credential": "valid"})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json(), user, team


def bearer(pair):
    return {"Authorization": f"Bearer {pair['access_token']}"}


def test_rotation_replay_revokes_entire_session(app, client):
    original, user, _ = login(app, client)
    assert original["expires_in"] == 900
    claims = decode_access_token(original["access_token"], app.state.settings)
    with Session(get_engine()) as db:
        row = db.get(sessions.RefreshCredential, sessions.digest(original["refresh_token"]))
        assert row is not None and row.token_hash != original["refresh_token"]
        fixed_expiry = db.get(sessions.AuthSession, UUID(claims["sid"])).expires_at
    rotated = client.post("/auth/refresh", json={"refresh_token": original["refresh_token"]})
    assert rotated.status_code == 200
    replacement = rotated.json()
    assert replacement["refresh_token"] != original["refresh_token"]
    assert client.get("/auth/me", headers=bearer(replacement)).status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": original["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": replacement["refresh_token"]}).status_code == 401
    assert client.get("/auth/me", headers=bearer(replacement)).status_code == 401
    with Session(get_engine()) as db:
        row = db.get(sessions.AuthSession, UUID(claims["sid"]))
        assert row.user_id == user and row.revoked_at is not None and row.expires_at == fixed_expiry


@pytest.mark.parametrize("field", ["expires_at", "idle_expires_at"])
def test_session_expiry(app, client, field):
    pair, _, _ = login(app, client)
    sid = UUID(decode_access_token(pair["access_token"], app.state.settings)["sid"])
    with Session(get_engine()) as db:
        row = db.get(sessions.AuthSession, sid)
        setattr(row, field, sessions.now() - timedelta(seconds=1))
        db.add(row)
        db.commit()
    assert client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401
    assert client.get("/auth/me", headers=bearer(pair)).status_code == 401


def test_logout_and_logout_all(app, client):
    first, _, _ = login(app, client)
    second = client.post("/auth/google", json={"credential": "valid"}).json()
    assert client.post("/auth/logout", json={"refresh_token": first["refresh_token"]}).status_code == 204
    assert client.get("/auth/me", headers=bearer(first)).status_code == 401
    assert client.get("/auth/me", headers=bearer(second)).status_code == 200
    assert client.post("/auth/logout-all", headers=bearer(second)).status_code == 204
    assert client.get("/auth/me", headers=bearer(second)).status_code == 401
    from app.modules.authentication.gateway import IdentityGatewayImpl

    with Session(get_engine()) as db:
        assert IdentityGatewayImpl(db).authorized_stream_tokens([second["access_token"]], app.state.settings) == set()
    assert client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]}).status_code == 401


def test_removed_permissions_prevent_refresh(app, client):
    pair, _, team_id = login(app, client)
    with Session(get_engine()) as db:
        team = db.get(Team, team_id)
        team.status = "disabled"
        db.add(team)
        db.commit()
    assert client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]}).status_code == 401


def test_concurrent_refresh_has_one_winner_then_revokes(app, client):
    pair, _, _ = login(app, client)

    def refresh():
        with Session(get_engine()) as db:
            try:
                result = sessions.rotate(db, app.state.settings, pair["refresh_token"])
                db.commit()
                return result
            except AppError:
                db.rollback()
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: refresh(), range(2)))
    assert sum(result is not None for result in results) == 1
    winner = next(result for result in results if result is not None)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {winner.access_token}"}).status_code == 401


def test_pkce_handoff_requires_verifier_and_code_is_single_use(app, client):
    _, _, subject, email = _seed_team_a()
    app.state.google_code_exchange = lambda code: "id-token"
    app.state.google_token_verifier = lambda token: {"google_subject": subject, "email": email}
    app.state.settings = app.state.settings.model_copy(
        update={"google_oauth_client_id": "test-client", "google_oauth_client_secret": SecretStr("test-secret")}
    )
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    response = client.get(
        "/auth/google/login",
        params={"code_challenge": challenge, "code_challenge_method": "S256"},
        follow_redirects=False,
    )
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    response = client.get(
        "/auth/google/callback", params={"code": "google-code", "state": state}, follow_redirects=False
    )
    assert response.status_code == 302, response.text
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert set(query) == {"code"}
    code = query["code"][0]
    assert client.post("/auth/token", json={"code": code, "code_verifier": "w" * 43}).status_code == 401
    exchange = client.post("/auth/token", json={"code": code, "code_verifier": verifier})
    assert exchange.status_code == 200
    assert exchange.json()["refresh_token"]
    assert client.post("/auth/token", json={"code": code, "code_verifier": verifier}).status_code == 401
    assert client.get("/auth/me", headers=bearer(exchange.json())).status_code == 200


def test_expired_login_code_and_rate_limit(app, client):
    pair, user, _ = login(app, client)
    with Session(get_engine()) as db:
        db.add(
            sessions.LoginCode(
                token_hash=sessions.digest("x" * 43),
                user_id=user,
                challenge="y" * 43,
                expires_at=sessions.now() - timedelta(seconds=1),
            )
        )
        bucket = db.exec(select(sessions.AuthRateBucket)).one()
        bucket.attempts = 60
        db.add(bucket)
        db.commit()
    response = client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert response.status_code == 429
    with Session(get_engine()) as db:
        db.execute(delete(sessions.AuthRateBucket))
        db.commit()
    assert client.post("/auth/token", json={"code": "x" * 43, "code_verifier": "v" * 43}).status_code == 401


def test_missing_pkce_attempt_cannot_downgrade_to_legacy(app, client):
    _, _, subject, email = _seed_team_a()
    app.state.google_code_exchange = lambda code: "id-token"
    app.state.google_token_verifier = lambda token: {"google_subject": subject, "email": email}
    client.cookies.set("google_oauth_state", "pkce.missing-attempt")
    response = client.get(
        "/auth/google/callback", params={"code": "google-code", "state": "pkce.missing-attempt"}, follow_redirects=False
    )
    assert response.status_code == 400
    assert "location" not in response.headers


def test_cleanup_retains_live_replay_evidence(app, client):
    pair, _, _ = login(app, client)
    client.post("/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    from scripts.cleanup_auth import main

    main()
    with Session(get_engine()) as db:
        credential = db.get(sessions.RefreshCredential, sessions.digest(pair["refresh_token"]))
        assert credential is not None and credential.consumed_at is not None
        row = db.get(sessions.AuthSession, credential.session_id)
        row.expires_at = sessions.now() - timedelta(seconds=1)
        db.add(row)
        db.commit()
    main()
    with Session(get_engine()) as db:
        assert db.get(sessions.RefreshCredential, sessions.digest(pair["refresh_token"])) is None
