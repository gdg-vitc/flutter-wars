"""Exercise Durable Object lifecycle and Worker commit-notification policy.

The Workers SDK runs only in Pyodide. These doubles implement its documented
WebSocket/storage boundary; HTTP/JWT/transaction tests run against the real app.
"""

import asyncio
import importlib
import json
import sys
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from httpx import Headers

from app.core.errors import AppError
from app.core.market_stream import PROTOCOL, StreamAuth, next_alarm


class Storage:
    def __init__(self):
        self.data = {}
        self.alarm = None

    async def get(self, key):
        return self.data.get(key)

    async def put(self, key, value):
        self.data[key] = value

    async def setAlarm(self, value):
        self.alarm = value

    async def deleteAlarm(self):
        self.alarm = None


class Socket:
    def __init__(self):
        self.messages = []
        self.closed = False
        self.attachment = None

    def send(self, value):
        assert not self.closed, "Never publish to a revoked/closed connection"
        self.messages.append(json.loads(value))

    def close(self, code, reason):
        self.closed = True
        self.code = code

    def serializeAttachment(self, value):
        self.attachment = value

    def deserializeAttachment(self):
        return self.attachment


class Context:
    def __init__(self):
        self.storage = Storage()
        self.sockets = []
        self.tasks = []

    def getWebSockets(self):
        return [s for s in self.sockets if not s.closed]

    def acceptWebSocket(self, socket):
        self.sockets.append(socket)

    def waitUntil(self, task):
        self.tasks.append(task)


@pytest.fixture
def sdk(monkeypatch):
    class Base:
        def __init__(self, ctx, env):
            self.ctx, self.env = ctx, env

    class Response:
        def __init__(self, body=None, status=200, **kwargs):
            self.status, self.body = status, body
            self.kwargs = kwargs

    workers = ModuleType("workers")
    workers.DurableObject = workers.WorkerEntrypoint = Base
    workers.Response = Response
    workers.asgi = SimpleNamespace(fetch=None)
    js = ModuleType("js")
    js.WebSocketPair = SimpleNamespace(new=lambda: SimpleNamespace(object_values=lambda: (Socket(), Socket())))
    pyodide = ModuleType("pyodide")
    http = ModuleType("pyodide.http")
    http.pyfetch = None
    monkeypatch.setitem(sys.modules, "pg8000", ModuleType("pg8000"))
    for name, module in (("workers", workers), ("js", js), ("pyodide", pyodide), ("pyodide.http", http)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "cloudflare/src"))
    for name in ("worker", "round_stream"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    stream = importlib.import_module("round_stream")
    worker = importlib.import_module("worker")
    monkeypatch.setattr(worker, "_wait_database_slot", lambda: asyncio.sleep(0))
    monkeypatch.setattr(stream, "session_factory", lambda: nullcontext(None))
    monkeypatch.setattr(stream.RoundStream, "_app", lambda self: SimpleNamespace(state=SimpleNamespace(settings=None)))
    return stream, worker, workers


def _request(round_id, *, method="GET", path=None):
    return SimpleNamespace(
        method=method,
        url=f"https://test{path or f'/market/rounds/{round_id}/stream'}",
        headers=Headers({"Upgrade": "websocket", "Sec-WebSocket-Protocol": PROTOCOL}),
    )


def test_hibernation_reconnect_change_broadcast_and_recovery_alarm(sdk, monkeypatch):
    stream, _, _ = sdk
    round_id = uuid4()
    ctx = Context()
    payload = {"revision": "first", "server_time": datetime.now(UTC).isoformat(), "valid_until": None, "stock": 10}
    monkeypatch.setattr(stream, "read_ticket", lambda *args: StreamAuth("member", datetime.now(UTC).timestamp() + 60))
    monkeypatch.setattr(stream, "authorize", lambda *args: None)
    monkeypatch.setattr(stream, "authorize_many", lambda session, tokens, settings: set(tokens))
    monkeypatch.setattr(stream, "snapshot", lambda *args: dict(payload))

    async def scenario():
        obj = stream.RoundStream(ctx, None)
        response = await obj.fetch(_request(round_id))
        assert response.status == 101
        assert response.kwargs["headers"]["Sec-WebSocket-Protocol"] == PROTOCOL
        first = ctx.sockets[0]
        assert first.messages[-1]["stock"] == 10 and first.messages[-1]["sequence"] == 1
        # New object instance, same durable storage and socket attachments.
        obj = stream.RoundStream(ctx, None)
        await obj.alarm()
        assert len(first.messages) == 1  # duplicate alarm does not duplicate a snapshot
        payload.update(revision="second", stock=7)
        await obj.alarm()  # recovers a dropped trade notification
        assert first.messages[-1]["stock"] == 7 and first.messages[-1]["sequence"] == 2
        assert ctx.storage.alarm is not None
        await obj.fetch(_request(round_id))
        second = ctx.sockets[1]
        assert second.messages[-1]["stock"] == 7 and second.messages[-1]["sequence"] == 2
        first.close(1000, "done")
        second.close(1000, "done")
        await obj.alarm()
        assert ctx.storage.alarm is None

    asyncio.run(scenario())


def test_revocation_closes_socket_without_publishing(sdk, monkeypatch):
    stream, _, _ = sdk
    ctx = Context()
    round_id = uuid4()
    monkeypatch.setattr(stream, "read_ticket", lambda *args: StreamAuth("member", datetime.now(UTC).timestamp() + 60))
    monkeypatch.setattr(stream, "authorize", lambda *args: None)
    monkeypatch.setattr(stream, "authorize_many", lambda session, tokens, settings: set(tokens))
    monkeypatch.setattr(stream, "snapshot", lambda *args: {"revision": "a", "valid_until": None})

    async def scenario():
        obj = stream.RoundStream(ctx, None)
        await obj.fetch(_request(round_id))

        def deny(*args):
            raise AppError("TEAM_ACCESS_DENIED", "Access denied", 403)

        monkeypatch.setattr(stream, "authorize", deny)
        monkeypatch.setattr(stream, "authorize_many", lambda *args: set())
        await obj.alarm()
        assert ctx.sockets[0].closed and ctx.sockets[0].code == 1008
        assert len(ctx.sockets[0].messages) == 1 and ctx.storage.alarm is None

    asyncio.run(scenario())


def test_alarm_retries_transient_failure_and_limits_connections(sdk, monkeypatch):
    stream, _, _ = sdk
    ctx = Context()
    ctx.sockets = [Socket() for _ in range(128)]
    monkeypatch.setattr(stream, "read_ticket", lambda *args: StreamAuth("member", datetime.now(UTC).timestamp() + 60))

    async def scenario():
        obj = stream.RoundStream(ctx, None)
        assert (await obj.fetch(_request(uuid4()))).status == 503

        async def fail():
            raise RuntimeError("temporary database failure")

        monkeypatch.setattr(obj, "_refresh", fail)
        with pytest.raises(RuntimeError):
            await obj.alarm()
        assert ctx.storage.alarm is not None

    asyncio.run(scenario())


def test_alarm_uses_price_boundary_session_expiry_and_recovery_bound():
    now = datetime.now(UTC)
    expiry = now.timestamp() + 60
    assert next_alarm({"valid_until": None}, now, expiry) == now.timestamp() + 5
    assert next_alarm({"valid_until": (now + timedelta(seconds=2)).isoformat()}, now, expiry) == now.timestamp() + 2
    assert next_alarm({"valid_until": None}, now, now.timestamp() + 1) == now.timestamp() + 1


@pytest.mark.parametrize(
    ("method", "path", "status", "notify"),
    [
        ("POST", "/market/purchase", 200, True),
        ("POST", "/market/sell", 200, True),
        ("POST", "/market/purchase", 409, False),
        ("PATCH", "/admin/market/listings/x/pricing", 200, True),
        ("POST", "/admin/market/rounds/x/close", 200, True),
        ("GET", "/market/listings", 200, False),
        ("POST", "/market/rounds/x/stream-ticket", 200, False),
    ],
)
def test_worker_notifies_only_after_successful_mutations(sdk, monkeypatch, method, path, status, notify):
    _, worker, workers = sdk
    ctx = Context()
    committed = []
    notified = []
    monkeypatch.setattr(worker, "get_app", lambda env: None)

    async def serve(*args):
        if status < 300:
            committed.append(True)
        return workers.Response(status=status)

    async def signal(env, changed_path):
        assert committed
        notified.append(changed_path)

    monkeypatch.setattr(workers.asgi, "fetch", serve)
    monkeypatch.setattr(worker, "notify_streams", signal)

    async def scenario():
        response = await worker.Default(ctx, None).fetch(_request(uuid4(), method=method, path=path))
        assert response.status == status
        for task in ctx.tasks:
            await task
        assert bool(notified) == notify

    asyncio.run(scenario())


def test_worker_serializes_requests_across_entrypoint_instances(sdk, monkeypatch):
    _, worker, workers = sdk
    monkeypatch.setattr(worker, "get_app", lambda env: None)

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        active = 0
        peak = 0

        async def serve(*args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            entered.set()
            await release.wait()  # synchronous sockets can yield just like this
            active -= 1
            return workers.Response(status=401)

        monkeypatch.setattr(worker, "_app", serve)

        async def adapter(application, *args):
            await application({"type": "http"}, None, None)
            return workers.Response(status=401)

        monkeypatch.setattr(workers.asgi, "fetch", adapter)
        request = _request(uuid4(), method="POST", path="/auth/refresh")
        first = asyncio.create_task(worker.Default(Context(), None).fetch(request))
        await entered.wait()
        second = asyncio.create_task(worker.Default(Context(), None).fetch(request))
        await asyncio.sleep(0)
        assert peak == 1
        release.set()
        responses = await asyncio.gather(first, second)
        assert [r.status for r in responses] == [401, 401]
        assert peak == 1 and not worker._database_lock.locked()

    asyncio.run(scenario())


def test_worker_releases_database_lock_after_cancelled_request(sdk, monkeypatch):
    _, worker, workers = sdk
    monkeypatch.setattr(worker, "get_app", lambda env: None)

    async def scenario():
        entered = asyncio.Event()

        async def serve(*args):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(worker, "_app", serve)

        async def adapter(application, *args):
            await application({"type": "http"}, None, None)
            return workers.Response(status=401)

        monkeypatch.setattr(workers.asgi, "fetch", adapter)
        request = _request(uuid4(), method="POST", path="/auth/refresh")
        first = asyncio.create_task(worker.Default(Context(), None).fetch(request))
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not worker._database_lock.locked()

        async def recovered(*args):
            return workers.Response(status=401)

        monkeypatch.setattr(worker, "_app", recovered)
        assert (await worker.Default(Context(), None).fetch(request)).status == 401

    asyncio.run(scenario())


def test_asgi_lifespan_does_not_wait_for_transaction_lock(sdk, monkeypatch):
    _, worker, _ = sdk

    async def scenario():
        called = []

        async def app(scope, receive, send):
            called.append(scope["type"])

        monkeypatch.setattr(worker, "_app", app)
        async with worker._database_lock:
            await asyncio.wait_for(worker._serialized_app({"type": "lifespan"}, None, None), timeout=1)
        assert called == ["lifespan"]

    asyncio.run(scenario())


def test_refresh_burst_queues_only_one_successor_and_samples_latest_commit(sdk, monkeypatch):
    stream, _, _ = sdk

    async def run():
        obj = stream.RoundStream(Context(), None)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        revision = [1]

        async def fetch(request):
            calls.append(revision[0])
            if len(calls) == 1:
                entered.set()
                await release.wait()
            return stream.Response(None, status=204)

        monkeypatch.setattr(obj, "_fetch", fetch)
        request = _request(uuid4(), method="POST", path="/refresh")
        first = asyncio.create_task(obj.fetch(request))
        await entered.wait()
        burst = [asyncio.create_task(obj.fetch(request)) for _ in range(100)]
        await asyncio.sleep(0)
        revision[0] = 101
        release.set()
        responses = await asyncio.gather(first, *burst)
        assert all(r.status == 204 for r in responses)
        assert calls == [1, 101]
        assert not obj._refresh_pending

    asyncio.run(run())


def test_cancelled_refresh_waiter_does_not_suppress_future_notifications(sdk, monkeypatch):
    stream, _, _ = sdk

    async def run():
        obj = stream.RoundStream(Context(), None)
        await obj._lock.acquire()
        request = _request(uuid4(), method="POST", path="/refresh")
        waiter = asyncio.create_task(obj.fetch(request))
        await asyncio.sleep(0)
        assert obj._refresh_pending
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not obj._refresh_pending
        obj._lock.release()
        assert (await obj.fetch(request)).status == 204

    asyncio.run(run())
