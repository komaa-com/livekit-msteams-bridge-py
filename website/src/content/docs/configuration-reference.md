---
title: "Configuration Reference"
description: "Every environment variable the bridge reads, with defaults and meaning."
---

The bridge is configured entirely from environment variables - the same names as the Node.js sibling, so one `.env` file drives either implementation. The package ships a fully commented [`.env.example`](https://github.com/komaa-com/livekit-msteams-bridge-py/blob/main/.env.example). Four variables are required.

## Required

| Env | Meaning |
|---|---|
| `WORKER_SHARED_SECRET` | The shared secret from StandIn pairing. Must equal what StandIn holds, or the HMAC upgrade is rejected with `401`. |
| `LIVEKIT_URL` | LiveKit server URL (`wss://<project>.livekit.cloud` or self-hosted). |
| `LIVEKIT_API_KEY` | LiveKit API key; mints join tokens, dispatches agents, deletes rooms. Server-side only. |
| `LIVEKIT_API_SECRET` | LiveKit API secret paired with the key. |

## Agent dispatch

| Env | Default | Meaning |
|---|---|---|
| `LIVEKIT_AGENT_NAME` | unset | The `agent_name` your worker registers with, for **explicit dispatch** (recommended). Unset falls back to automatic dispatch (an unnamed agent joins every room; prototype-only). |
| `LIVEKIT_ROOM_PREFIX` | `msteams-` | Room name prefix; the room is `{prefix}{callId}` (sanitized, capped at 100 chars to match the Node bridge). |
| `LIVEKIT_DELETE_ROOM_ON_END` | `true` | Delete the room at teardown so the agent job ends immediately instead of idling out (billing hygiene). |

## Call governor

| Env | Default | Meaning |
|---|---|---|
| `MAX_CALL_MINUTES` | `0` (off) | Bridge-side hard cap per call, in minutes (fractional allowed). |
| `GOODBYE_TEXT` | a default line | The goodbye line sent to the agent on `teams.goodbye`. |
| `GOODBYE_GRACE_MS` | `8000` | How long the agent gets to speak the goodbye before `session.end`. The call ends this grace + a fixed 500 ms scheduling buffer after the request. |

## Server and transport

| Env | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | TCP port the bridge listens on. |
| `BIND` | `0.0.0.0` | Bind address. |
| `TLS_CERT_PATH` / `TLS_KEY_PATH` | unset | PEM cert/key for native TLS (`wss`). When both are set the bridge serves TLS itself; otherwise front the plain WS with a TLS terminator. |
| `HMAC_FRESHNESS_MS` | `60000` | Two-sided freshness window: a timestamp up to 60 s in the past OR the future is accepted; the replay guard holds a used handshake until the timestamp ages out. |
| `MAX_CONNECTIONS` | `0` (= 64) | Max concurrent connections. |
| `MAX_CONNECTIONS_PER_IP` | `0` (= total cap) | Per-IP cap. Defaults to the total cap because StandIn dials from a small set of egress IPs. |
| `TRUST_PROXY_XFF` | `false` | Trust the first `X-Forwarded-For` hop for the per-IP cap. Only enable behind a proxy you control. |
| `PRE_START_TIMEOUT_MS` | `0` (= 10000) | Drop a connection that authenticates but never sends `session.start`. |
| `WORKER_IDLE_TIMEOUT_MS` | `0` (= 90000) | Dead-peer window: end the call after this long without any worker message (the worker heartbeats every 30 s). Frees the call id for reconnect and ends the agent job. |
| `LOG_LEVEL` | `info` | `debug` \| `info` \| `warn` \| `error`. An invalid value falls back to `info`. |

The bridge also exposes `GET /metrics` (Prometheus text format, no auth): calls total/active, call seconds, upgrade rejections by cause, frames relayed each way, backpressure drops, room connect failures, governor fires, goodbye requests, unparseable frames, and callid mismatches. Like `GET /healthz` it is served on the same port - keep the port private to your network or scrape through your ingress.

:::note
Numeric variables **fail loud**: `MAX_CALL_MINUTES=abc` (or a negative value) stops startup with a clear error rather than silently disabling the governor, and a non-numeric `PORT` stops with a clear message instead of an opaque listen error.
:::

:::caution
`BIND=0.0.0.0` exposes the bridge (and therefore the shared-secret-gated upgrade) on every interface. Bind to loopback and put a TLS-terminating reverse proxy in front, or restrict access at the network layer.
:::

The bridge participant's join token has a fixed **6 h TTL**; set `MAX_CALL_MINUTES` well below that for calls that must end cleanly.
