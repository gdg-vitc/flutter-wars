"""Stream tickets and the development-server WebSocket transport.

Cloudflare routes the same WebSocket path to a hibernating Durable Object. Local
ASGI servers use the shared snapshot/authentication functions with periodic reads.
"""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from sqlmodel import Session

from app.core.auth import Principal, get_principal
from app.core.db import get_session, session_factory
from app.core.errors import AppError
from app.core.market_stream import PROTOCOL, authorize, issue_ticket, read_ticket, snapshot
from app.modules.market import service
from app.modules.market.models import RoundStatus

router = APIRouter(tags=["market stream"])


@router.post("/market/rounds/{round_id}/stream-ticket")
def stream_ticket(
    round_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
    _: Principal = Depends(get_principal),
) -> dict:
    rnd = service.get_round(session, round_id)
    if rnd.status == RoundStatus.DRAFT:
        raise AppError("ROUND_NOT_FOUND", "Round not found.", 404)
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise AppError("AUTHENTICATION_REQUIRED", "Authentication is required.", 401)
    return issue_ticket(header[7:], round_id, request.app.state.settings)


@router.websocket("/market/rounds/{round_id}/stream")
async def market_socket(websocket: WebSocket, round_id: UUID) -> None:
    settings = websocket.app.state.settings
    try:
        auth = read_ticket(websocket.headers.get("sec-websocket-protocol", ""), round_id, settings)
        with session_factory() as session:
            authorize(session, auth, settings)
            initial = snapshot(session, round_id, datetime.now(UTC))
    except AppError:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol=PROTOCOL)
    await websocket.send_json(initial)

    async def receive() -> None:
        while True:
            message = await websocket.receive_text()
            if message != "ping":
                await websocket.close(code=1008)
                return
            await websocket.send_json({"type": "pong", "server_time": datetime.now(UTC).isoformat()})

    listener = asyncio.create_task(receive())
    previous = initial["revision"]
    try:
        while not listener.done():
            done, _ = await asyncio.wait([listener], timeout=1)
            if done:
                break

            def load():
                with session_factory() as session:
                    authorize(session, auth, settings)
                    return snapshot(session, round_id, datetime.now(UTC))

            payload = await asyncio.to_thread(load)
            if payload["revision"] != previous:
                await websocket.send_json(payload)
                previous = payload["revision"]
    except AppError:
        await websocket.close(code=1008)
    except WebSocketDisconnect:
        pass
    finally:
        listener.cancel()
        with suppress(asyncio.CancelledError, WebSocketDisconnect):
            await listener
