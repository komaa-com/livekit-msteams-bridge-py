---
title: "Run the Example"
description: "A guided walkthrough: run the example voice agent, run the bridge embedding, connect StandIn, take a call, then swap in the bitHuman avatar."
---

Two example projects show a full working setup - the repository ships a minimal bridge embedding ([`examples/basic-bridge`](https://github.com/komaa-com/livekit-msteams-bridge-py/tree/main/examples/basic-bridge)), and the Node sibling repository ships ready-to-run **agents** ([`examples/agents`](https://github.com/komaa-com/livekit-msteams-bridge/tree/main/examples/agents), Python agents that work with either bridge). This page walks through both so you understand every moving part.

## What a working setup needs

A LiveKit call has **three** processes, two of them yours:

1. **Your agent worker** - registers with your LiveKit project under an `agent_name` and waits for dispatch.
2. **This bridge** - creates a room per Teams call, dispatches the agent into it, relays audio.
3. **StandIn** (hosted) - joins the Teams call and dials the bridge.

## 1. Run the example voice agent

The example agents are plain LiveKit agents (OpenAI STT/LLM/TTS + Silero VAD) - nothing Teams-specific:

```bash
git clone https://github.com/komaa-com/livekit-msteams-bridge
cd livekit-msteams-bridge/examples/agents
cp .env.example .env   # LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, OPENAI_API_KEY
uv sync
uv run voice_agent.py download-files
uv run voice_agent.py dev
```

(Plain pip works too: `pip install -r requirements.txt && python voice_agent.py dev`.)

The worker registers as **`standin-voice-agent`** and waits - it will not join anything until the bridge creates a room and dispatches it.

## 2. Run the bridge example

```bash
pip install livekit-msteams-bridge
git clone https://github.com/komaa-com/livekit-msteams-bridge-py
cd livekit-msteams-bridge-py/examples/basic-bridge
cp .env.example .env   # same LiveKit project + LIVEKIT_AGENT_NAME=standin-voice-agent + WORKER_SHARED_SECRET
python main.py
```

It prints the WebSocket URL to give StandIn:

```text
Point your StandIn identity's agent WebSocket URL at ws://<this-host>:8080/voice/msteams/stream
```

The `main.py` is the recommended embedding shape in ~25 lines: `load_dotenv()`, `load_config()` (fails loud on any misconfiguration), `await start_server(cfg)`, and a graceful `await server.close()` on Ctrl-C / SIGTERM that ends live calls with a spoken-protocol `session.end` rather than a hard drop.

## 3. Connect StandIn and call

1. Expose port 8080 with a tunnel (`tailscale funnel --bg --https=8080 8080`, `cloudflared tunnel --url http://localhost:8080`, or `ngrok http 8080`).
2. In your [StandIn dashboard](https://standin.komaa.com/dashboard), set the identity's **Agent voice URL** to the `wss://.../voice/msteams/stream` form and make sure the shared secret equals `WORKER_SHARED_SECRET`.
3. Call your Teams bot (or join the sandbox meeting). The bridge creates the room, dispatches `standin-voice-agent`, and the agent answers.

## 4. Swap in the avatar agent

`avatar_agent.py` is the same pipeline plus a lip-synced **bitHuman** avatar. Two extra variables in the agent's `.env` (`BITHUMAN_API_SECRET`, `BITHUMAN_MODEL_PATH`), then:

```bash
uv run avatar_agent.py dev
```

and restart the bridge with `LIVEKIT_AGENT_NAME=standin-avatar-agent`. The caller hears the avatar's audio; the avatar's video stays in the room (the Teams tile is rendered by StandIn's own animated avatar - see [Agents and Dispatch](/livekit-msteams-bridge-py/agents-and-dispatch/)).

## What the example agents demonstrate

Each example shows the three integration points your own agent can use:

- **`agent_name`** in `WorkerOptions` - the dispatch contract with `LIVEKIT_AGENT_NAME`.
- **`ctx.job.metadata`** - per-call caller context (`caller_name`, `tenant_id`, `call_direction`, `user_id` when known) for greetings and personalization.
- **`teams.context` / `teams.goodbye` data topics** - call context and the governor's goodbye handler (interrupt the current turn, speak the line).

Details and copy-paste handlers: [Agents and Dispatch](/livekit-msteams-bridge-py/agents-and-dispatch/).

## From example to your own service

- Keep your own agent worker exactly as it is for WebRTC users - just give it an `agent_name`.
- Embed the bridge (`await start_server(load_config())`) or run the stock CLI.
- Set the [governor variables](/livekit-msteams-bridge-py/governors-and-privacy/) (`MAX_CALL_MINUTES`, `GOODBYE_TEXT`) before production.
- For tests, inject a fake room with the `connect_room` argument - see [Library API](/livekit-msteams-bridge-py/library-api/).
