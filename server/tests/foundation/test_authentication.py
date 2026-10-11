from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from sqlmodel import Session, SQLModel

from app.core.db import get_engine
from app.modules.authentication.jwt import create_access_token, decode_access_token
from app.modules.authentication.model import Team, TeamMembership, UserIdentity


def _seed_team_a(preloaded: bool = False) -> tuple[int, int, str, str]:
    suffix = uuid4().hex
    subject = f"google-user-{suffix}"
    email = f"a+{suffix}@example.test"
    SQLModel.metadata.create_all(get_engine())
    with Session(get_engine()) as session:
        team = Team(name=f"Team A {suffix}")
        user = UserIdentity(google_subject=None if preloaded else subject, email=email)
        session.add(team)
        session.add(user)
        session.commit()
        session.refresh(team)
        session.refresh(user)
        assert team.id is not None and user.id is not None
        session.add(TeamMembership(user_identity_id=user.id, team_id=team.id, role="participant"))
        session.commit()
        return user.id, team.id, subject, email


def test_google_login_and_me_resolve_current_membership(app, client):
    user_id, team_id, subject, email = _seed_team_a()
    app.state.google_token_verifier = lambda credential: (
        {"google_subject": subject, "email": email} if credential == "valid-google-token" else None
    )

    login = client.post("/auth/google", json={"credential": "valid-google-token"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json() == {
        "user_id": str(user_id),
        "email": email,
        "team_id": str(team_id),
        "role": "participant",
        "auth_type": "JWT",
    }


def test_preloaded_participant_binds_google_subject_on_first_login(app, client):
    user_id, team_id, subject, email = _seed_team_a(preloaded=True)
    app.state.google_token_verifier = lambda credential: {"google_subject": subject, "email": email}

    login = client.post("/auth/google", json={"credential": "first-login-token"})
    assert login.status_code == 200
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert me.status_code == 200
    assert me.json()["user_id"] == str(user_id)
    assert me.json()["team_id"] == str(team_id)

    with Session(get_engine()) as session:
        bound_user = session.get(UserIdentity, user_id)
        assert bound_user is not None
        assert bound_user.google_subject == subject


def test_invalid_or_unregistered_google_identity_is_rejected(app, client):
    SQLModel.metadata.create_all(get_engine())
    app.state.google_token_verifier = lambda credential: None
    invalid = client.post("/auth/google", json={"credential": "bad-token"})
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "INVALID_GOOGLE_CREDENTIAL"

    app.state.google_token_verifier = lambda credential: {
        "google_subject": "unregistered",
        "email": "unknown@example.test",
    }
    unregistered = client.post("/auth/google", json={"credential": "valid-but-unknown"})
    assert unregistered.status_code == 403
    assert unregistered.json()["error"]["code"] == "ACCOUNT_NOT_ALLOWED"


def test_server_side_oauth_callback_exchanges_code_and_issues_jwt(app, client):
    user_id, team_id, subject, email = _seed_team_a()
    app.state.google_code_exchange = lambda code: "verified-google-id-token"
    app.state.google_token_verifier = lambda token: (
        {"google_subject": subject, "email": email} if token == "verified-google-id-token" else None
    )
    state = "test-oauth-state"
    client.cookies.set("google_oauth_state", state)
    callback = client.get(f"/auth/google/callback?code=auth-code&state={state}", follow_redirects=False)
    assert callback.status_code == 302
    redirect = urlsplit(callback.headers["location"])
    assert (redirect.scheme, redirect.netloc) == ("flutterwars", "auth-callback")
    parameters = parse_qs(redirect.query)
    assert set(parameters) == {"access_token", "refresh_token"}
    token = parameters["access_token"][0]
    claims = decode_access_token(token, app.state.settings)
    assert "sid" in claims
    from datetime import UTC, datetime

    remaining = claims["exp"] - datetime.now(UTC).timestamp()
    assert 890 < remaining <= 900
    refreshed = client.post("/auth/refresh", json={"refresh_token": parameters["refresh_token"][0]})
    assert refreshed.status_code == 200
    assert refreshed.json()["expires_in"] == 900

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user_id"] == str(user_id)
    assert me.json()["team_id"] == str(team_id)


def test_server_side_oauth_callback_rejects_state_mismatch(app, client):
    SQLModel.metadata.create_all(get_engine())
    client.cookies.set("google_oauth_state", "expected-state")
    response = client.get("/auth/google/callback?code=auth-code&state=wrong-state")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OAUTH_STATE_INVALID"


def test_jwt_team_and_role_claims_cannot_override_database_membership(app, client):
    user_id, team_a_id, _, email = _seed_team_a()
    with Session(get_engine()) as session:
        team_b = Team(name=f"Team B {uuid4().hex}")
        session.add(team_b)
        session.commit()
        session.refresh(team_b)
        assert team_b.id is not None

    forged_team_token = create_access_token(
        user_id=str(user_id),
        email=email,
        team_id=str(team_b.id),
        role="organizer",
        settings=app.state.settings,
    )
    denied = client.get("/auth/me", headers={"Authorization": f"Bearer {forged_team_token}"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "TEAM_ACCESS_DENIED"

    valid_token = create_access_token(
        user_id=str(user_id),
        email=email,
        team_id=str(team_a_id),
        role="organizer",
        settings=app.state.settings,
    )
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {valid_token}"})
    assert me.status_code == 200
    assert me.json()["role"] == "participant"
