"""Integration tests: a real BridgeServer on a loopback port, real WebSocket
upgrades, real caps - the transport layer the unit tests can't see."""

import asyncio
import json
import socket
import time

import aiohttp
import pytest

from livekit_msteams_bridge.hmac_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from livekit_msteams_bridge.server import start_server

from conftest import FakeRoomPort, make_config


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def signed_headers(call_id: str, secret: str = "test-secret") -> dict:
    ts = int(time.time() * 1000)
    return {TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: sign(secret, ts, call_id)}


async def fake_connector(cfg, log, call_id, metadata, handlers):
    return FakeRoomPort()


@pytest.fixture
async def running_server():
    cfg = make_config(port=free_port(), max_connections=2, pre_start_timeout_ms=300)
    server = await start_server(cfg, connect_room=fake_connector)
    try:
        yield cfg, server
    finally:
        await server.close()


@pytest.fixture
async def running_server_custom_path():
    cfg = make_config(port=free_port(), ws_path="/agents/msteams", pre_start_timeout_ms=300)
    server = await start_server(cfg, connect_room=fake_connector)
    try:
        yield cfg, server
    finally:
        await server.close()


def url(cfg, path: str = "") -> str:
    return f"http://127.0.0.1:{cfg.port}{path}"


async def test_healthz_and_metrics(running_server):
    cfg, _ = running_server
    async with aiohttp.ClientSession() as s:
        r = await s.get(url(cfg, "/healthz"))
        assert r.status == 200 and (await r.text()) == "ok"
        r = await s.get(url(cfg, "/metrics"))
        assert r.status == 200 and "bridge_room_connect_failures_total" in (await r.text())


async def test_unauthenticated_upgrade_rejected(running_server):
    cfg, _ = running_server
    async with aiohttp.ClientSession() as s:
        with pytest.raises(aiohttp.WSServerHandshakeError) as e:
            await s.ws_connect(url(cfg, "/msteams/calling/call-1"))
        assert e.value.status == 401


async def test_upgrade_on_a_foreign_path_is_rejected_even_when_signed(running_server):
    """The live server must anchor on WS_PATH: a valid HMAC on the old /voice/msteams/stream shape
    (or any other co-hosted route) is a 401, not a call. This is the case that fails if the
    call_id_from_path(path, cfg.ws_path) call site is ever reduced back to one argument."""
    cfg, server = running_server
    async with aiohttp.ClientSession() as s:
        for path in ("/voice/msteams/stream/foreign", "/msteams/calling/a/b", "/msteams/callingX/foreign"):
            with pytest.raises(aiohttp.WSServerHandshakeError) as e:
                await s.ws_connect(url(cfg, path), headers=signed_headers("foreign"))
            assert e.value.status == 401, path
    assert server.sessions == {}


async def test_upgrade_on_a_custom_ws_path(running_server_custom_path):
    cfg, server = running_server_custom_path
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(url(cfg, "/agents/msteams/call-c"), headers=signed_headers("call-c"))
        assert "call-c" in server.sessions
        await ws.close()
        # the built-in default is not also served once WS_PATH moves
        with pytest.raises(aiohttp.WSServerHandshakeError) as e:
            await s.ws_connect(url(cfg, "/msteams/calling/call-d"), headers=signed_headers("call-d"))
        assert e.value.status == 401


async def test_encoded_slash_in_a_call_id_survives_the_upgrade(running_server):
    """aiohttp's request.path is already decoded; reading it instead of raw_path would split this
    callId into two segments and anchoring would reject a legitimate call."""
    cfg, server = running_server
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(url(cfg, "/msteams/calling/call%2Fslash"), headers=signed_headers("call/slash"))
        assert "call/slash" in server.sessions
        await ws.close()


async def test_full_call_roundtrip(running_server):
    cfg, server = running_server
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(url(cfg, "/msteams/calling/call-rt"), headers=signed_headers("call-rt"))
        await ws.send_str(json.dumps({"type": "session.start", "callId": "call-rt", "threadId": "t", "caller": {}}))
        await ws.send_str(json.dumps({"type": "ping", "ts": 7}))
        frame = await asyncio.wait_for(ws.receive(), 3)
        assert json.loads(frame.data) == {"type": "pong", "ts": 7}
        assert "call-rt" in server.sessions
        await ws.close()
        for _ in range(50):
            if "call-rt" not in server.sessions:
                break
            await asyncio.sleep(0.02)
        assert "call-rt" not in server.sessions  # registry evicted on disconnect


async def test_connection_cap_returns_503(running_server):
    cfg, _ = running_server  # max_connections=2
    async with aiohttp.ClientSession() as s:
        ws1 = await s.ws_connect(url(cfg, "/msteams/calling/cap-1"), headers=signed_headers("cap-1"))
        ws2 = await s.ws_connect(url(cfg, "/msteams/calling/cap-2"), headers=signed_headers("cap-2"))
        with pytest.raises(aiohttp.WSServerHandshakeError) as e:
            await s.ws_connect(url(cfg, "/msteams/calling/cap-3"), headers=signed_headers("cap-3"))
        assert e.value.status == 503
        await ws1.close()
        await ws2.close()


async def test_pre_start_timeout_closes_idle_worker(running_server):
    cfg, _ = running_server  # pre_start_timeout_ms=300
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(url(cfg, "/msteams/calling/lazy"), headers=signed_headers("lazy"))
        got_end = False
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            frame = await ws.receive(timeout=3)
            if frame.type == aiohttp.WSMsgType.TEXT and json.loads(frame.data).get("type") == "session.end":
                got_end = True
            if frame.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                break
        assert got_end


async def test_call_seconds_counted_exactly_once(running_server):
    # regression: session teardown AND the server read loop both used to count
    # bridge_call_seconds_total, reporting ~2x the real duration
    from livekit_msteams_bridge.metrics import reset_metrics, render_metrics

    cfg, _ = running_server
    reset_metrics()
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect(url(cfg, "/msteams/calling/call-secs"), headers=signed_headers("call-secs"))
        await ws.send_str(json.dumps({"type": "session.start", "callId": "call-secs", "threadId": "t", "caller": {}}))
        await asyncio.sleep(0.4)
        await ws.close()
    await asyncio.sleep(0.1)
    line = next(ln for ln in render_metrics().splitlines() if ln.startswith("bridge_call_seconds_total "))
    seconds = float(line.split()[1])
    # single-counted: ~0.4 s. The old double-count would report ~0.8 s.
    assert 0.25 <= seconds <= 0.65, line
