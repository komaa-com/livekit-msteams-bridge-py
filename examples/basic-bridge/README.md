# basic-bridge (Python)

Minimal, working embedding of `livekit-msteams-bridge`: `load_config()` + `start_server()`.

## Run

```bash
pip install livekit-msteams-bridge
cp .env.example .env   # fill in LIVEKIT_URL/KEY/SECRET, LIVEKIT_AGENT_NAME, WORKER_SHARED_SECRET
python main.py
```

A LiveKit call needs **two** processes: your agent runs as a worker (registered under the same
`agent_name` as `LIVEKIT_AGENT_NAME`), and this bridge dispatches it into a per-call room.

It prints the WebSocket URL to give StandIn. Expose port 8080 with a tunnel (Tailscale Funnel,
cloudflared, ngrok, ...), set your StandIn identity's **Agent voice URL** to the `wss://` URL, and
place a Teams call - the bridge creates a room, dispatches your agent, and the agent answers.

Full setup walkthrough (including ready-to-run example agents): https://docs.komaa.com/livekit/example
