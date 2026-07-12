---
title: "Library API"
description: "Embed the bridge in your own asyncio app: start_server, custom room connectors for testing, HMAC helpers, and protocol helpers."
---

The package is both a CLI and an importable Python library. Everything below is exported from the package root, and the package ships `py.typed`.

```python
from livekit_msteams_bridge import load_config, start_server
```

## Run the bridge in your own service

`load_config()` reads the same environment variables as the CLI and raises a clear `ValueError` when a required variable is missing or a numeric one is not a number. `start_server(cfg)` is a coroutine that starts listening and returns a `BridgeServer` handle.

```python
import asyncio
from livekit_msteams_bridge import load_config, start_server

async def main():
    server = await start_server(load_config())
    print("bridge up")
    try:
        await asyncio.Event().wait()   # run until cancelled
    finally:
        await server.close()           # drains live calls (session.end + close)

asyncio.run(main())
```

`server.drain()` ends every live call gracefully without stopping the listener; `server.close()` drains and stops. The CLI wires SIGTERM/SIGINT to this for you - in your own app, hook your shutdown path to `server.close()` so a rolling deploy never hard-drops a call.

## Custom room connector (testing)

The `connect_room` argument to `start_server` is an async factory that returns an `AgentRoomPort`. The default creates a real LiveKit room and dispatches your agent; tests substitute a fake so no server is needed.

```python
from livekit_msteams_bridge import load_config, start_server

async def fake_connector(cfg, log, call_id, metadata, handlers):
    class FakeRoom:
        room_name = f"fake-{call_id}"
        async def publish_caller_audio(self, b64): ...
        def send_context(self, text): ...
        def send_goodbye(self, text): ...
        async def close(self): ...
    # push agent audio at any time with handlers.on_agent_audio("<base64 pcm>")
    return FakeRoom()

server = await start_server(load_config(), connect_room=fake_connector)
```

The repository's own [test suite](https://github.com/komaa-com/livekit-msteams-bridge-py/tree/main/tests) uses exactly this shape - `tests/conftest.py` has a reusable `FakeRoomPort`.

`RoomHandlers` carries the four callbacks the room side reports through: `on_agent_audio(base64_pcm)`, `on_agent_joined(identity)`, `on_closed(reason)`, `on_error(err)`.

## HMAC helpers

Useful if you build tools that talk to the bridge, or want to test the upgrade.

```python
import time
from livekit_msteams_bridge import sign, verify, is_fresh, TIMESTAMP_HEADER, SIGNATURE_HEADER

ts = int(time.time() * 1000)
signature = sign(secret, ts, call_id)   # HMAC-SHA256(secret, f"{ts}.{call_id}") hex
# send as headers X-OpenClawTeamsBridge-Timestamp / -Signature
verify(secret, ts, call_id, signature)  # constant-time, False on any missing input
is_fresh(ts, 60_000)                    # within the two-sided freshness window?
```

## Protocol helpers

Wire messages are plain dicts (they arrive and leave as JSON). `parse_worker_message(raw)` is the guarded parser (returns `None` on junk), and `pcm16k_bytes_to_ms(n)` converts PCM byte counts to milliseconds. See the [Wire Protocol](/livekit-msteams-bridge-py/wire-protocol/) for the full contract.

## Also exported

- `authorize_upgrade`, `call_id_from_path`, `ReplayGuard` - the upgrade-authorization primitives.
- `CallSession`, `WorkerPort`, `AgentRoomPort`, `RoomConnector`, `RoomHandlers` - the per-call relay class and its transport protocols (advanced embedding).
- `LiveKitRoomPort`, `connect_livekit_room`, `TOPIC_CONTEXT`, `TOPIC_GOODBYE` - the real room connector and the data-topic names.
- `load_dotenv` - the tiny `.env` loader the CLI uses.
- `render_metrics`, `reset_metrics`, `logger` - metrics text, the test-isolation reset, and the minimal leveled logger.
