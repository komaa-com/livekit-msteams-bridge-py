# Microsoft Teams Bridge for LiveKit Agents (Python)

Put a [LiveKit Agent](https://docs.livekit.io/agents/) on a real **Microsoft Teams call** - voice-only, or with a video avatar whose face and voice the caller sees and hears in Teams.

The hosted **StandIn media bridge** ([standin.komaa.com](https://standin.komaa.com)) joins the Teams call and dials into this bridge over an HMAC-authenticated WebSocket. Per call, the bridge creates one LiveKit room, **dispatches your agent into it** (explicit dispatch by `agent_name`), joins as a participant, publishes the caller's audio, and relays the agent's audio back to Teams. You run no Teams media stack yourself.

```text
Microsoft Teams call
       |
       v
StandIn media bridge       (hosted; joins the call)
       |   HMAC WebSocket, PCM 16 kHz
       v
this bridge                (you run it)
       |   WebRTC (room, one per call)
       v
LiveKit room  <--dispatch--  your LiveKit Agent
                             (STT + LLM + TTS + turn-taking, any plugin stack)
```

Both sides speak 16 kHz mono PCM16: the wire protocol natively, the room via the SDK's resampling `AudioSource`/`AudioStream` - the bridge itself never transcodes.

## Features

- **Any LiveKit agent answers Teams calls** - your existing agent, any STT/LLM/TTS/realtime plugin combo, needs no Teams-specific code. The bridge dispatches it by `agent_name` with per-call metadata (caller name, tenant, direction, AAD id when known).
- **One room per call** - clean lifecycle: room created at `session.start`, agent dispatched via the join token, room deleted at teardown so the agent job ends immediately.
- **Turn-taking is the agent's own** - VAD, interruption and endpointing all run inside your LiveKit agent session, exactly as they do for WebRTC users.
- **Group-call awareness** - participant counts, speaker changes, DTMF digits and recording state reach the agent as data messages on the `msteams.context` topic.
- **Avatar video on the Teams tile** - an avatar agent's video is relayed onto the caller's tile by default (`LIVEKIT_TILE_VIDEO=auto`); voice-only agents are unaffected.
- **Ambient vision (opt-in)** - the caller's screen-share and camera reach the agent as attributed images on the `msteams.vision` byte-stream topic, only when the scene changes and inside a per-call spend cap.
- **Call governor** - a bridge-side `MAX_CALL_MINUTES` hard cap, with the goodbye spoken by the agent over `msteams.goodbye`, plus the StandIn-side cutoff.
- **Hardened transport** - replay-proof single-use HMAC upgrade, connection caps checked before crypto, payload caps, pre-start timeout, dead-peer detection, graceful SIGTERM drain.
- **Observability** - `GET /healthz` and `GET /metrics` (Prometheus text format): calls, rejections, relayed and dropped frames.

> **Not yet at parity with the Node.js sibling.** Two of its features are not implemented here: the
> group-call gate (`GROUP_CALL_REQUIRE_ADDRESS` / `GROUP_CALL_WAKE_PHRASES`, "speak only when
> addressed" in a meeting) and the no-answer fallback (`STALE_CALL_REAPER_SECONDS`, ending a call
> whose agent never joined). Both are on the roadmap below. Everything else, including the wire
> protocol and the environment variable names, is the same.

## Install

```bash
pip install livekit-msteams-bridge
```

Requires Python 3.10+.

## Run

This is the whole configuration - five values, all required, no optional keys. Everything else has a
default that is already correct. Put them in a `.env` file in the working directory, which is loaded
automatically (an existing environment variable always wins):

```bash
# --- LiveKit project (LiveKit Cloud, or your self-hosted server) ---
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=API...
LIVEKIT_API_SECRET=...

# The exact agent_name your worker registers with
# (WorkerOptions(entrypoint_fnc=..., agent_name="standin-agent")). A worker that registers a name is
# reachable ONLY by explicit dispatch, so a mismatch here is the classic silent failure: the room is
# created, the worker never gets a job, and the caller hears nothing.
LIVEKIT_AGENT_NAME=standin-agent

# The connection secret from the StandIn portal. Must byte-match, or the handshake is rejected with
# 401 - which looks like silence from the caller's point of view.
BRIDGE_SECRET=paste-the-value-from-the-StandIn-portal
```

Then run it:

```bash
livekit-msteams-bridge
```

The bridge listens on **`:8080`** at **`/msteams/calling`** by default (`PORT`, `WS_PATH`); StandIn
appends `/{callId}` per call. It answers ONLY under that path, so anything else co-hosted on the same
origin keeps its own routes. Expose the port with a tunnel and register the public `wss://` URL as
your identity's **Agent voice URL** in the StandIn dashboard - never the local `ws://` bind.

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

## Examples

Three runnable examples, each with its own README:

| Example | What it is |
|---|---|
| [`examples/basic-bridge/`](./examples/basic-bridge/) | Embed the package in your own project instead of running the CLI: `load_config()` + `start_server()`. |
| [`examples/voice-agent/`](./examples/voice-agent/) | A working voice agent the bridge dispatches onto a Teams call: a speech pipeline plus silero VAD. Ships a Dockerfile. |
| [`examples/avatar-agent/`](./examples/avatar-agent/) | The same pipeline plus a video avatar, so the caller sees a face on the Teams tile and hears its synchronized voice. Ships a Dockerfile. |

Both agent examples show the three Teams integration points: `agent_name` for dispatch,
`ctx.job.metadata` for per-call context, and the `msteams.*` data topics.

## Configuration

Every setting is an environment variable, and [`.env.example`](./.env.example) ships fully commented
with the package.

**[Configuration reference](https://komaa-com.github.io/livekit-msteams-bridge-py/configuration-reference/)**
documents all of them: what each does, its default, and when to change it.

Two that catch people out:

- `LIVEKIT_AGENT_NAME` must equal the `agent_name` your worker registers with. A mismatch creates the
  room, dispatches nobody, and the caller hears silence.
- `LIVEKIT_TILE_VIDEO` relays an avatar agent's video onto the Teams tile. If the caller sees no
  video, check that your StandIn connection has video enabled: the relay draws onto the tile StandIn
  publishes, so if that tile does not exist the bridge streams valid frames into nothing and has no
  way to detect it.

## Endpoints

- `GET /healthz` - liveness.
- `GET /metrics` - Prometheus counters (calls, rejections, relayed/dropped frames).
- `GET /{...}/{callId}` + WebSocket upgrade - the worker wire, HMAC-signed with
  `X-StandIn-Timestamp` / `X-StandIn-Signature` over
  `"{timestampMs}.{callId}"`.

## Roadmap

Where the bridge stands today, and what each of these needs to move:

- **Barge-in flush**: interruption handling lives inside your LiveKit agent session (VAD,
  turn-taking), exactly as for WebRTC callers. The room transport gives the bridge no interruption
  event to relay, so the worker's own flush-on-silence smooths the tail end. An agent-published data
  event could close this later.
- **Caller video as a room track**: inbound Teams `video.frame` is not published into the room in
  this version. With `AMBIENT_VISION=true` it reaches the agent as attributed images on
  `msteams.vision` instead, which is what carries the "Sara's shared screen" attribution a raw track
  cannot.
- **Long calls**: the bridge participant's join token has a fixed 6 h TTL. Calls meant to outlast
  that need a re-join strategy; in practice set `MAX_CALL_MINUTES` well below it.
- **Group-call gate**: the Node.js sibling can withhold the agent's audio in a meeting until a caller
  addresses it by name (`GROUP_CALL_REQUIRE_ADDRESS`, `GROUP_CALL_WAKE_PHRASES`). Not implemented
  here yet, so in a group call this bridge relays the agent's audio the whole time.
- **No-answer fallback**: the Node.js sibling ends a call whose agent never joined the room after
  `STALE_CALL_REAPER_SECONDS`. Not implemented here yet, so a dispatch that never lands leaves the
  caller on a silent call until they hang up or `MAX_CALL_MINUTES` fires.

## Security notes

- `/healthz` and `/metrics` are **unauthenticated**; only the WebSocket upgrade is HMAC-gated. They
  expose no call content, just liveness and counters, but keep the port behind your ingress or
  tunnel rules if you would rather not publish call volumes.
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
