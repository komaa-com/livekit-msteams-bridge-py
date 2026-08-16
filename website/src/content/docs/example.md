---
title: "Run the Example"
description: "A guided walkthrough: run the example voice agent, run the bridge embedding, connect StandIn, take a call, then swap in the bitHuman avatar."
---

Three example projects show a full working setup, all in this repository: a minimal bridge embedding ([`examples/basic-bridge`](https://github.com/komaa-com/livekit-msteams-bridge-py/tree/main/examples/basic-bridge)) and two ready-to-run agents ([`examples/voice-agent`](https://github.com/komaa-com/livekit-msteams-bridge-py/tree/main/examples/voice-agent) and [`examples/avatar-agent`](https://github.com/komaa-com/livekit-msteams-bridge-py/tree/main/examples/avatar-agent)). This page walks through them so you understand every moving part.

## What a working setup needs

A LiveKit call has **three** processes, two of them yours:

1. **Your agent worker** - registers with your LiveKit project under an `agent_name` and waits for dispatch.
2. **This bridge** - creates a room per Teams call, dispatches the agent into it, relays audio.
3. **StandIn** (hosted) - joins the Teams call and dials the bridge.

## 1. Run the example voice agent

The example agents are plain LiveKit agents - nothing Teams-specific. The pipeline is picked from what your environment offers: Azure speech-to-speech realtime, Azure STT/LLM/TTS, or plain OpenAI (see the example's README).

```bash
git clone https://github.com/komaa-com/livekit-msteams-bridge-py
cd livekit-msteams-bridge-py/examples/voice-agent
cp .env.example .env   # LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET + one model stack
uv sync
uv run python -m livekit.agents download-files
uv run worker.py dev
```

(Plain pip works too: `pip install -r requirements.txt && python worker.py dev`.)

The worker registers as **`standin-agent`** and waits - it will not join anything until the bridge creates a room and dispatches it.

## 2. Run the bridge example

```bash
pip install livekit-msteams-bridge
git clone https://github.com/komaa-com/livekit-msteams-bridge-py
cd livekit-msteams-bridge-py/examples/basic-bridge
cp .env.example .env   # same LiveKit project + LIVEKIT_AGENT_NAME=standin-agent + BRIDGE_SECRET
python main.py
```

It prints the WebSocket URL to give StandIn:

```text
Point your StandIn identity's agent WebSocket URL at ws://<this-host>:9442/msteams/calling
```

The `main.py` is the recommended embedding shape in ~25 lines: `load_dotenv()`, `load_config()` (fails loud on any misconfiguration), `await start_server(cfg)`, and a graceful `await server.close()` on Ctrl-C / SIGTERM that ends live calls with a spoken-protocol `session.end` rather than a hard drop.

## 3. Connect StandIn and call

1. Expose port 9442 with a tunnel (`tailscale funnel --bg --set-path /msteams/calling http://127.0.0.1:9442/msteams/calling`, which serves the bridge at `wss://<your-tailnet-host>/msteams/calling` with no port; `cloudflared tunnel --url http://localhost:9442`; or `ngrok http 9442`).
2. In your [StandIn dashboard](https://standin.komaa.com/dashboard), set the identity's **Agent voice URL** to the `wss://.../msteams/calling` form and make sure the shared secret equals `BRIDGE_SECRET`.
3. Call your Teams bot (or join the sandbox meeting). The bridge creates the room, dispatches `standin-agent`, and the agent answers.

## 4. Swap in the avatar agent

[`examples/avatar-agent`](https://github.com/komaa-com/livekit-msteams-bridge-py/tree/main/examples/avatar-agent) is the same pipeline plus a lip-synced **bitHuman** avatar. Two extra variables in that agent's `.env` (`BITHUMAN_API_SECRET`, `BITHUMAN_MODEL_PATH`), then:

```bash
cd ../avatar-agent && uv sync && uv run worker.py dev
```

Stop the voice worker first: both register as `standin-agent`, and explicit dispatch resolves a single name. The caller hears the avatar's audio; the avatar's video stays in the room (the Teams tile is rendered by StandIn's own animated avatar - see [Agents and Dispatch](/livekit-msteams-bridge-py/agents-and-dispatch/)).

## What the example agents demonstrate

Each example shows the three integration points your own agent can use:

- **`agent_name`** in `WorkerOptions` - the dispatch contract with `LIVEKIT_AGENT_NAME`.
- **`ctx.job.metadata`** - per-call caller context (`caller_name`, `tenant_id`, `call_direction`, `user_id` when known) for greetings and personalization.
- **`msteams.context` / `msteams.goodbye` / `msteams.vision` data topics** - call context, the governor's goodbye handler (interrupt the current turn, speak the line), and the opt-in ambient-vision byte stream.

Details and copy-paste handlers: [Agents and Dispatch](/livekit-msteams-bridge-py/agents-and-dispatch/).

## From example to your own service

- Keep your own agent worker exactly as it is for WebRTC users - just give it an `agent_name`.
- Embed the bridge (`await start_server(load_config())`) or run the stock CLI.
- Set the [governor variables](/livekit-msteams-bridge-py/governors-and-privacy/) (`MAX_CALL_MINUTES`, `GOODBYE_TEXT`) before production.
- For tests, inject a fake room with the `connect_room` argument - see [Library API](/livekit-msteams-bridge-py/library-api/).
