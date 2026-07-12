---
title: "Getting Started"
description: "Install the bridge, configure the required variables, run your agent worker, connect a StandIn identity, and make your first Teams call."
---

By the end of this page a LiveKit agent answers a Microsoft Teams call. You need Python `>= 3.10`, a LiveKit server (a [LiveKit Cloud](https://cloud.livekit.io) project or self-hosted) with an API key/secret, a LiveKit agent worker, and a StandIn identity (the sandbox is enough).

## 1. Run your agent worker

A LiveKit call needs **two** processes: your agent runs as a worker, and the bridge dispatches it into a per-call room. Register the worker under an agent name:

```python
cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="teams-voice-agent"))
```

Any existing LiveKit agent works unchanged - or start from the ready-made examples (a minimal voice pipeline and a bitHuman avatar) referenced in [Run the Example](/livekit-msteams-bridge-py/example/).

## 2. Install and run the bridge

```bash
pip install livekit-msteams-bridge
```

As a CLI:

```bash
LIVEKIT_URL=wss://your-project.livekit.cloud \
LIVEKIT_API_KEY=API... \
LIVEKIT_API_SECRET=... \
LIVEKIT_AGENT_NAME=teams-voice-agent \
WORKER_SHARED_SECRET=... \
  livekit-msteams-bridge
```

A `.env` file in the working directory is loaded automatically (existing environment wins). Or embedded in your own asyncio app:

```python
import asyncio
from livekit_msteams_bridge import load_config, start_server

async def main():
    await start_server(load_config())  # same env variables as the CLI
    await asyncio.Event().wait()

asyncio.run(main())
```

:::caution
`LIVEKIT_AGENT_NAME` must equal the `agent_name` your worker registers with, and both processes must point at the **same LiveKit project**. A mismatch is the classic silent failure: the room is created, the caller hears nothing, and the worker never gets a job.
:::

Every option is an environment variable; the package ships a fully commented [`.env.example`](https://github.com/komaa-com/livekit-msteams-bridge-py/blob/main/.env.example), and the [Configuration Reference](/livekit-msteams-bridge-py/configuration-reference/) documents each one. The bridge listens on `0.0.0.0:8080` by default and exposes `GET /healthz` for liveness checks.

`WORKER_SHARED_SECRET` comes from StandIn in the next step.

## 3. Connect a StandIn identity

StandIn is the hosted service that joins the Teams call and dials into your bridge. Pick a tier at [standin.komaa.com](https://standin.komaa.com) (sandbox for an instant trial), pair, and you get a **shared secret**.

1. Put the secret in `WORKER_SHARED_SECRET` (both sides must match exactly).
2. Point the identity's **agent WebSocket URL** at your bridge, for example `wss://lk-bridge.example.com:8080/voice/msteams/stream`. StandIn appends `/{callId}` per call.
3. Restart the bridge if you changed the env.

StandIn dials in **from the internet**, so a laptop or private host needs a public URL. A tunnel gives you one and terminates TLS (so you get `wss://` for free). Run one pointing at port `8080`:

Tailscale Funnel:

```bash
tailscale funnel --bg --https=8080 8080
```

Cloudflare Tunnel:

```bash
cloudflared tunnel --url http://localhost:8080
```

ngrok:

```bash
ngrok http 8080
```

For a fixed production host use an ingress/load balancer, or serve TLS natively with `TLS_CERT_PATH` + `TLS_KEY_PATH`. Never give StandIn a plain `ws://` URL outside local testing.

More detail (tiers, what pairing does, cutoff behavior): [Connecting to StandIn](/livekit-msteams-bridge-py/connecting-to-standin/).

## 4. Make the first call

Call your Teams bot (or join the sandbox meeting). In the bridge logs you should see the call arrive, the room open, the agent dispatched, and the relay start:

```text
INFO  [server] worker connected for call 19:meeting_ab... (1/64)
INFO  [call:19:meeting_ab] session.start (direction=inbound, recording=unknown)
INFO  [call:19:meeting_ab] LiveKit room "msteams-19-meeting_ab..." joined
INFO  [call:19:meeting_ab] agent "teams-voice-agent" dispatched
INFO  [call:19:meeting_ab] LiveKit room "msteams-19-meeting_ab..." relaying
```

Speak, and the agent answers in its own voice. If the call connects but something is off, [Troubleshooting](/livekit-msteams-bridge-py/troubleshooting/) maps every error you are likely to see (`401` handshake, `agent-unavailable`, silent agent) to its cause.
