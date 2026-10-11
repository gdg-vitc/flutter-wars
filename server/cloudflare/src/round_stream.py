"""One hibernating WebSocket Durable Object per market round.

Only the default Worker exposes upgrades. /refresh is an internal binding call
made after committed HTTP mutations; alarms also recover missed notifications.
Connection credentials survive hibernation in attachments, never in URLs/logs.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from js import WebSocketPair
from workers import DurableObject, Response

from app.core.db import session_factory
from app.core.errors import AppError
from app.core.market_stream import (
    MAX_CONNECTIONS,
    PROTOCOL,
    StreamAuth,
    authorize,
    authorize_many,
    next_alarm,
    read_ticket,
    snapshot,
)

log = logging.getLogger("flutterwars.stream")


class RoundStream(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        # Serialize async storage operations and broadcasts; idle locks do not prevent hibernation.
        self._lock = asyncio.Lock()
        self._refresh_pending = False

    async def fetch(self, request):
        refresh = request.method == "POST" and urlparse(request.url).path == "/refresh"
        if refresh and self._refresh_pending:
            return Response(None, status=204)
        if refresh:
            self._refresh_pending = True
        try:
            await self._lock.acquire()
        except BaseException:
            # Only the admitted waiter owns the pending flag; a cancelled waiter
            # must not suppress later committed-change notifications forever.
            if refresh:
                self._refresh_pending = False
            raise
        try:
            # Clear before sampling so a commit during the refresh can queue one successor.
            if refresh:
                self._refresh_pending = False
            from worker import database_access

            async with database_access():
                return await self._fetch(request)
        finally:
            self._lock.release()

    def _app(self):
        from worker import get_app

        return get_app(self.env)

    async def _fetch(self, request):
        if request.method == "POST" and urlparse(request.url).path == "/refresh":
            if self.ctx.getWebSockets():
                await self._refresh()
            return Response(None, status=204)
        if request.method != "GET" or (request.headers.get("Upgrade") or "").lower() != "websocket":
            return Response("WebSocket upgrade required", status=426)
        try:
            round_id = UUID(urlparse(request.url).path.split("/")[-2])
            app = self._app()
            auth = read_ticket(request.headers.get("Sec-WebSocket-Protocol") or "", round_id, app.state.settings)
            stored = await self.ctx.storage.get("round_id")
            if stored is not None and stored != str(round_id):
                return Response("Wrong round", status=400)
            if len(self.ctx.getWebSockets()) >= MAX_CONNECTIONS:
                return Response("Round stream is full", status=503)
            with session_factory() as session:
                authorize(session, auth, app.state.settings)
                snapshot(session, round_id, datetime.now(UTC))
        except (AppError, ValueError) as exc:
            status = exc.status_code if isinstance(exc, AppError) else 400
            return Response("Stream unavailable", status=status)
        await self.ctx.storage.put("round_id", str(round_id))
        client, server = WebSocketPair.new().object_values()
        self.ctx.acceptWebSocket(server)
        server.serializeAttachment(json.dumps({"access_token": auth.access_token, "expires_at": auth.expires_at}))
        await self._refresh(force=server)
        return Response(None, status=101, web_socket=client, headers={"Sec-WebSocket-Protocol": PROTOCOL})

    async def _publish(self, payload, sockets, *, force=None):
        record = await self.ctx.storage.get("last")
        record = json.loads(record) if record else {"revision": None, "sequence": 0}
        changed = record["revision"] != payload["revision"]
        if changed:
            record = {"revision": payload["revision"], "sequence": record["sequence"] + 1}
            await self.ctx.storage.put("last", json.dumps(record))
        message = json.dumps(payload | {"sequence": record["sequence"]})
        for socket in sockets:
            if changed or socket == force:
                try:
                    socket.send(message)
                except Exception:
                    socket.close(1011, "Reconnect")

    async def _refresh(self, *, force=None):
        app = self._app()
        round_id = UUID(await self.ctx.storage.get("round_id"))
        now = datetime.now(UTC)
        active = []
        connections = [(socket, json.loads(socket.deserializeAttachment())) for socket in self.ctx.getWebSockets()]
        sockets = []
        with session_factory() as session:
            authorized = authorize_many(session, [data["access_token"] for _, data in connections], app.state.settings)
            for socket, data in connections:
                auth = StreamAuth(data["access_token"], data["expires_at"])
                try:
                    if auth.access_token not in authorized:
                        raise AppError("TEAM_ACCESS_DENIED", "Access denied", 403)
                    active.append(auth)
                    sockets.append(socket)
                except AppError:
                    socket.close(1008, "Sign in again")
            if not active:
                await self.ctx.storage.deleteAlarm()
                return
            payload = snapshot(session, round_id, now)
        await self._publish(payload, sockets, force=force)
        await self.ctx.storage.setAlarm(
            int(next_alarm(payload, datetime.now(UTC), min(auth.expires_at for auth in active)) * 1000)
        )

    async def alarm(self):
        async with self._lock:
            from worker import database_access

            async with database_access():
                await self._alarm()

    async def _alarm(self):
        if not self.ctx.getWebSockets():
            await self.ctx.storage.deleteAlarm()
            return
        try:
            await self._refresh()
        except AppError:
            for socket in self.ctx.getWebSockets():
                socket.close(1008, "Stream unavailable")
            await self.ctx.storage.deleteAlarm()
        except Exception:
            # Keep a recovery alarm beyond the platform's finite automatic retries.
            await self.ctx.storage.setAlarm(int((datetime.now(UTC).timestamp() + 5) * 1000))
            log.warning("market stream refresh failed")
            raise

    async def webSocketMessage(self, ws, message):
        if message != "ping":
            ws.close(1008, "Only ping is supported")
            return
        try:
            app = self._app()
            data = json.loads(ws.deserializeAttachment())
            from worker import database_access

            async with database_access():
                with session_factory() as session:
                    authorize(session, StreamAuth(data["access_token"], data["expires_at"]), app.state.settings)
            ws.send(json.dumps({"type": "pong", "server_time": datetime.now(UTC).isoformat()}))
        except AppError:
            ws.close(1008, "Sign in again")

    async def webSocketClose(self, ws, code, reason, was_clean):
        ws.close(code, reason)

    async def webSocketError(self, ws, error):
        ws.close(1011, "Reconnect")
