from uuid import UUID, uuid4

import jwt
import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.errors import AppError
from app.core.market_stream import PROTOCOL, issue_ticket, read_ticket
from tests.e2e.test_competition_flow import _ok, _open_round, _setup_team, _widget


def _ticket(client, round_id, headers):
    return _ok(client.post(f"/market/rounds/{round_id}/stream-ticket", headers=headers), 200)


def test_stream_updates_after_committed_purchase_and_close(client, organizer, team_login):
    _, member = _setup_team(client, organizer, team_login, credits=1000)
    wid = _widget(client, organizer)
    rnd = _open_round(client, organizer, "trading", [{"widget_id": wid, "base_price": 100, "supply": 10}])
    ticket = _ticket(client, rnd["id"], member)
    protocols = [PROTOCOL, f"ticket.{ticket['ticket']}"]
    with client.websocket_connect(ticket["path"], subprotocols=protocols) as stream:
        first = stream.receive_json()
        assert first["type"] == "market_snapshot" and first["status"] == "open"
        listing = first["listings"][0]
        assert listing["stock_remaining"] == 10 and listing["price"] == 100
        before_rest = _ok(client.get("/market/listings", headers=member))
        assert before_rest["listings"][0]["stock_remaining"] == 10
        _ok(
            client.post(
                "/market/purchase",
                headers=member,
                json={
                    "listing_id": listing["listing_id"],
                    "quantity": 3,
                    "idempotency_key": str(uuid4()),
                },
            ),
            200,
        )
        after = stream.receive_json()
        assert after["listings"][0]["stock_remaining"] == 7
        assert after["revision"] != first["revision"] and after["server_time"]
        assert _ok(client.get("/market/listings", headers=member))["listings"][0]["stock_remaining"] == 7
        denied = client.post(
            "/market/purchase",
            headers=member,
            json={
                "listing_id": listing["listing_id"],
                "quantity": 100,
                "idempotency_key": str(uuid4()),
            },
        )
        assert denied.status_code == 409
        stream.send_text("ping")
        assert stream.receive_json()["type"] == "pong"
        _ok(client.post(f"/admin/market/rounds/{rnd['id']}/close", headers=organizer), 200)
        closed = stream.receive_json()
        assert closed["status"] == "closed" and closed["valid_until"] is None
        assert closed["listings"][0]["stock_remaining"] == 7
    # A reconnect always gets the current full snapshot, even without a new trade.
    ticket = _ticket(client, rnd["id"], member)
    with client.websocket_connect(ticket["path"], subprotocols=[PROTOCOL, f"ticket.{ticket['ticket']}"]) as stream:
        assert stream.receive_json()["status"] == "closed"


def test_ticket_scope_and_separate_signing_key(client, app, organizer, team_login):
    _, member = _setup_team(client, organizer, team_login, credits=1000)
    rnd = _open_round(
        client, organizer, "trading", [{"widget_id": _widget(client, organizer), "base_price": 100, "supply": 10}]
    )
    ticket = _ticket(client, rnd["id"], member)
    assert client.post(f"/market/rounds/{rnd['id']}/stream-ticket").status_code == 401
    assert client.get("/market", headers={"Authorization": f"Bearer {ticket['ticket']}"}).status_code == 401
    with pytest.raises(AppError, match="invalid or expired"):
        read_ticket(f"{PROTOCOL}, ticket.{ticket['ticket']}", uuid4(), app.state.settings)
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(ticket["path"]):
        pass
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(ticket["path"], subprotocols=[PROTOCOL, "ticket.invalid"]),
    ):
        pass
    # A previously valid ticket cannot bypass revocation of current team access.
    auth = jwt.decode(
        member["Authorization"][7:],
        app.state.settings.jwt_secret_key.get_secret_value(),
        algorithms=[app.state.settings.jwt_algorithm],
    )
    from sqlalchemy import text

    from app.core.db import get_engine

    with get_engine().begin() as connection:
        connection.execute(text("UPDATE team SET status = 'disabled' WHERE id = :id"), {"id": auth["team_id"]})
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(ticket["path"], subprotocols=[PROTOCOL, f"ticket.{ticket['ticket']}"]),
    ):
        pass


def test_ticket_rejects_expired_credentials_and_draft_round(client, app, organizer):
    market = client.post("/admin/market", headers=organizer, json={"name": "Draft ticket market"})
    assert market.status_code in (201, 409)
    rnd = _ok(client.post("/admin/market/rounds", headers=organizer, json={"name": "Draft", "kind": "trading"}), 201)
    assert client.post(f"/market/rounds/{rnd['id']}/stream-ticket", headers=organizer).status_code == 404
    expired = jwt.encode(
        {"sub": "1", "exp": 1},
        app.state.settings.jwt_secret_key.get_secret_value(),
        algorithm=app.state.settings.jwt_algorithm,
    )
    with pytest.raises(AppError):
        issue_ticket(expired, UUID(rnd["id"]), app.state.settings)


def test_stream_identity_checks_are_batched_and_team_scoped(client, app, organizer, team_login):
    from sqlalchemy import event, text
    from sqlmodel import Session

    from app.contracts.identity import IdentityGateway
    from app.core.db import get_engine
    from app.core.services import gateway

    first_id, first = _setup_team(client, organizer, team_login)
    _, second = _setup_team(client, organizer, team_login)
    one, two = first["Authorization"][7:], second["Authorization"][7:]
    secret = app.state.settings.jwt_secret_key.get_secret_value()
    algorithm = app.state.settings.jwt_algorithm
    claims = jwt.decode(two, secret, algorithms=[algorithm])
    wrong_team = jwt.encode(claims | {"team_id": str(first_id)}, secret, algorithm=algorithm)
    expired = jwt.encode(claims | {"exp": 1}, secret, algorithm=algorithm)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(get_engine(), "before_cursor_execute", record)
    try:
        with Session(get_engine()) as session:
            allowed = gateway(IdentityGateway, session).authorized_stream_tokens(
                [one, two, one, wrong_team, expired, "invalid"],
                app.state.settings,
            )
        assert allowed == {one, two} and len(statements) == 1
    finally:
        event.remove(get_engine(), "before_cursor_execute", record)
    with get_engine().begin() as connection:
        connection.execute(text("UPDATE team SET status='disabled' WHERE id=:id"), {"id": first_id})
    with Session(get_engine()) as session:
        assert gateway(IdentityGateway, session).authorized_stream_tokens([one, two], app.state.settings) == {two}
