# livekit-msteams-bridge (Python)

Put a **LiveKit Agent** - including avatar agents - on **Microsoft Teams voice/video calls**.

> PyPI package: **`livekit-msteams-bridge`** - the `-py` suffix is only in this repository's
> name, to distinguish it from the [Node.js sibling repo](https://github.com/komaa-com/livekit-msteams-bridge).

This is the Python sibling of [`@komaa/livekit-msteams-bridge`](https://www.npmjs.com/package/@komaa/livekit-msteams-bridge)
(Node.js) - same wire contract, same environment variables, drop-in interchangeable behind the same
`.env` file. The Node and Python packages version independently, so a version-number gap between them is expected; both implement the same StandIn wire protocol and interoperate with the hosted service identically. It terminates the StandIn media bridge wire protocol on one side and a LiveKit room on
the other:

- **One room per call**: the bridge creates a fresh room, joins as a participant, and dispatches
  your agent into it (explicit dispatch by `agent_name` - LiveKit's recommended model).
- **No transcoding on our side**: the worker speaks 16 kHz mono PCM16 natively; the room side uses
  the SDK's resampling `AudioSource`/`AudioStream`, so the bridge itself never transcodes.
- **Agent integration without Teams code**: per-call metadata (`ctx.job.metadata` - caller name,
  tenant, direction, AAD id when known) plus three data topics: `msteams.context` (participants, DTMF,
  recording state), `msteams.goodbye` (the governor's goodbye line to speak) and `msteams.vision`
  (opt-in byte stream carrying the caller's screen-share/camera as attributed images).
- **Call governors**: a bridge-side hard time cap (the agent speaks the goodbye), plus the
  StandIn-side governor.
- **Hardened**: HMAC-signed upgrades with replay guard, connection caps, dead-peer detection,
  graceful SIGTERM drain, Prometheus `/metrics`.

[StandIn](https://standin.komaa.com) is the hosted media bridge that joins the Teams call and dials
this bridge - you run no Teams media stack yourself.

**Documentation**: [komaa-com.github.io/livekit-msteams-bridge-py](https://komaa-com.github.io/livekit-msteams-bridge-py/)
(getting started, example walkthrough, agents and dispatch, configuration and library reference,
wire protocol). Teams/StandIn setup lives at [docs.komaa.com](https://docs.komaa.com/livekit/installation).

## Install

```bash
pip install livekit-msteams-bridge
```

Requires Python 3.10+.

## Run

```bash
LIVEKIT_URL=wss://your-project.livekit.cloud \
LIVEKIT_API_KEY=API... \
LIVEKIT_API_SECRET=... \
LIVEKIT_AGENT_NAME=standin-agent \
BRIDGE_SECRET=... \
livekit-msteams-bridge
```

A `.env` file in the working directory is loaded automatically (existing environment wins). The
bridge listens on `ws://0.0.0.0:8080/msteams/calling` by default (`WS_PATH`); StandIn appends
`/{callId}` per call. The bridge answers ONLY under that path, so anything else co-hosted on the
same origin keeps its own routes. Expose the port with a tunnel and register the `wss://` URL as your identity's
**Agent voice URL** in the StandIn dashboard.

`LIVEKIT_AGENT_NAME` must equal the `agent_name` your worker registers with
(`WorkerOptions(entrypoint_fnc=..., agent_name="standin-agent")`). A mismatch is the classic
silent failure: the room is created, the caller hears nothing, and the worker never gets a job.

## Embed

```python
import asyncio
from livekit_msteams_bridge import load_config, start_server

async def main():
    server = await start_server(load_config())
    await asyncio.Event().wait()  # run until cancelled

asyncio.run(main())
```

Tests can inject a fake room with the `connect_room` argument - see the test suite's
`FakeRoomPort` for the shape.

## Configuration

Everything is environment variables; names are identical to the Node package.

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `BRIDGE_SECRET` | yes | - | Must equal the shared secret from StandIn pairing (HMAC upgrade check). |
| `LIVEKIT_URL` | yes | - | LiveKit server URL (`wss://<project>.livekit.cloud` or self-hosted). |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | yes | - | Mint join tokens, dispatch agents, delete rooms. Server-side only. |
| `LIVEKIT_AGENT_NAME` | no | - | Named agent for explicit dispatch (recommended). Unset = automatic dispatch (prototype-only). |
| `LIVEKIT_ROOM_PREFIX` | no | `msteams-` | Room name prefix; the room is `{prefix}{callId}` (sanitized). |
| `LIVEKIT_DELETE_ROOM_ON_END` | no | `true` | Delete the room at teardown so the agent job ends immediately (billing hygiene). |
| `LIVEKIT_TILE_VIDEO` | no | `auto` | Relay an avatar agent's video onto the Teams tile. `auto` = the agent participant; or a specific participant identity; `off` disables the relay and keeps StandIn's built-in animated avatar. Voice-only agents are unaffected (no avatar video to relay). |
| `LIVEKIT_TILE_VIDEO_FPS` | no | `15` | Send rate for the relayed tile stream (frames/s). |
| `MAX_CALL_MINUTES` | no | `0` (off) | Bridge-side hard cap per call; on expiry the agent is asked to say goodbye, then the call ends. |
| `GOODBYE_TEXT` / `GOODBYE_GRACE_MS` | no | (default line) / `8000` | Goodbye wording (sent on `msteams.goodbye`) and playout grace. The call ends `GOODBYE_GRACE_MS` + a fixed 500 ms scheduling buffer after the goodbye request. |
| `PORT` / `BIND` | no | `8080` / `0.0.0.0` | Listen port / bind address. |
| `WS_PATH` | no | `/msteams/calling` | Base path the worker WebSocket is anchored on; a call dials `{WS_PATH}/{callId}`. Same shape as the OpenClaw and Hermes plugins, so one StandIn identity URL works for every backend. `/healthz` and `/metrics` stay at the root. |
| `AMBIENT_VISION` | no | `false` | Publish the caller's newest screen-share/camera frame to the agent on `msteams.vision`. OFF by default: it is the knob that costs money per frame. |
| `MAX_VISION_PER_MINUTE` | no | `30` | Per-call vision spend cap over a sliding 60 s window. **`0` disables** (the sibling OpenClaw plugin reads 0 as unlimited; that inversion is not carried over). |
| `REQUIRE_RECORDING_STATUS` | no | `true` | Hold vision frames until Teams reports the recording active. Nothing is even stored before then, so nothing captured beforehand can surface later. |

> **`LIVEKIT_TILE_VIDEO` needs an outbound video tile on your StandIn connection.** The relay draws
> onto the tile StandIn publishes into the call, so that tile has to exist: the connection needs its
> avatar and video enabled. If it is off, this bridge streams perfectly valid frames and they are
> discarded on arrival - the caller simply sees no video, and the bridge has no way to know. If your
> agent publishes video, `LIVEKIT_TILE_VIDEO` is `auto`, and the caller still sees nothing, check that
> setting on your StandIn connection first; recent worker builds log a one-time warning naming it.
| `HMAC_FRESHNESS_MS` | no | `60000` | Two-sided freshness window: a timestamp up to 60 s in the past OR the future is accepted, and the replay guard holds a used handshake until the timestamp ages out. |
| `MAX_CONNECTIONS` / `MAX_CONNECTIONS_PER_IP` | no | `64` / = total | Connection caps. |
| `PRE_START_TIMEOUT_MS` | no | `10000` | Drop a worker that authenticates but never sends `session.start`. |
| `WORKER_IDLE_TIMEOUT_MS` | no | `90000` | Dead-peer window (the worker heartbeats every 30 s). |
| `TRUST_PROXY_XFF` | no | `false` | Trust the first `X-Forwarded-For` hop for the per-IP cap. |
| `TLS_CERT_PATH` / `TLS_KEY_PATH` | no | - | Serve native TLS (`wss`). Otherwise front the plain WS with a TLS terminator. |
| `LOG_LEVEL` | no | `info` | `debug` / `info` / `warn` / `error`. |

## Endpoints

- `GET /healthz` - liveness.
- `GET /metrics` - Prometheus counters (calls, rejections, relayed/dropped frames).
- `GET /{...}/{callId}` + WebSocket upgrade - the worker wire, HMAC-signed with
  `X-OpenClawTeamsBridge-Timestamp` / `X-OpenClawTeamsBridge-Signature` over
  `"{timestampMs}.{callId}"`.

Notes for operators:

- `/healthz` and `/metrics` are **unauthenticated** (only the WebSocket upgrade is HMAC-gated).
  They expose no call content - just liveness and counters - but if you would rather not leak call
  volumes, keep the port behind your ingress/tunnel rules.
- Barge-in: interruption handling lives **inside your LiveKit agent session** (VAD, turn-taking),
  exactly as for WebRTC callers; the room transport gives the bridge no interruption event to relay,
  so the worker's own flush-on-silence smooths the tail end.
- The bridge participant's join token has a fixed **6 h TTL**. Calls that should be allowed to run
  longer than that need a re-join strategy; in practice set `MAX_CALL_MINUTES` well below it.
- Inbound Teams **video** (`video.frame`) is not forwarded to the room in this version - the bot's
  tile is rendered by the worker's own avatar. Publishing caller video as a room track is on the
  roadmap.
- The Docker image exposes port **8080** and does not remap `PORT`/`BIND` at the Docker layer; use
  `-e PORT=... -p <host>:<port>` together if you change them.

## Agent integration points

Your agent needs no Teams-specific code, but three integration points are available:

- **`agent_name`** in `WorkerOptions` - must match `LIVEKIT_AGENT_NAME` for explicit dispatch.
- **`ctx.job.metadata`** (JSON) - per-call context: `source`, `caller_name`, `tenant_id`,
  `call_direction`, and `user_id` (AAD id when Teams provides one).
- **Data topics** - `msteams.context` (participant count, DTMF, recording state), `msteams.goodbye`
  (the governor's goodbye line; have your handler speak it and interrupt the current turn), and
  `msteams.vision` (a byte stream of attributed screen-share/camera images, when the bridge runs
  with `AMBIENT_VISION=true`; read it with `room.register_byte_stream_handler`).

## License

MIT (c) Komaa DigiTech
