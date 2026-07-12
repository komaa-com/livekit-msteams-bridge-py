"""Bridge configuration, entirely from environment variables.

The worker-side contract (HMAC secret, wire protocol) must match the StandIn
media bridge; the LiveKit side needs a server URL, API key/secret, and
(recommended) a named agent for explicit dispatch. Environment variable names
are identical to the Node package (@komaa/livekit-msteams-bridge), so the two
are drop-in interchangeable behind the same .env file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_GOODBYE = "I'm sorry, we've reached the time limit for this call. Thank you for calling, goodbye!"


@dataclass(frozen=True)
class BridgeConfig:
    port: int
    """TCP port the bridge listens on for worker WebSocket upgrades."""
    host: str
    """Bind address."""
    worker_shared_secret: str
    """Must equal the shared secret the StandIn media bridge signs with (HMAC upgrade check)."""
    livekit_url: str
    """LiveKit server URL (wss://<project>.livekit.cloud or self-hosted)."""
    livekit_api_key: str
    """LiveKit API key; mints join tokens + dispatches agents + deletes rooms. Server-side only."""
    livekit_api_secret: str
    """LiveKit API secret paired with the key."""
    livekit_agent_name: str | None
    """Named agent for EXPLICIT dispatch (recommended by LiveKit): the agent
    registered with WorkerOptions.agent_name. None = rely on automatic dispatch
    (agents with no name join every room; prototype-only per LiveKit docs)."""
    livekit_room_prefix: str
    """Room name prefix; the room is `{prefix}{callId}` (sanitized)."""
    livekit_delete_room_on_end: bool
    """Delete the LiveKit room at teardown so the agent job ends immediately (billing hygiene)."""
    max_call_minutes: float
    """Bridge-side call governor: hard cap on call duration in minutes (fractional
    allowed). 0 = disabled. LiveKit doesn't know about your billing; on limit the
    bridge asks the agent to say goodbye (data topic), waits the grace, then ends the call."""
    goodbye_text: str
    """Goodbye line sent to the agent (data topic "teams.goodbye") on governor cutoff."""
    goodbye_grace_ms: float
    """How long to let the goodbye play before session.end (the bridge cannot know the real duration)."""
    hmac_freshness_ms: float
    """Allowed clock skew for the HMAC timestamp, in ms (the worker documents +/-60s)."""
    max_connections: int
    """Max concurrent worker connections (0 = default 64)."""
    max_connections_per_ip: int
    """Max concurrent connections from one remote IP (0 = default: same as max_connections)."""
    pre_start_timeout_ms: float
    """Drop a worker that authenticates but never sends session.start after this many ms (0 = default 10s)."""
    worker_idle_timeout_ms: float
    """Dead-peer window: end the call after this many ms without ANY worker message
    (0 = default 90s; the worker heartbeats every 30s)."""
    trust_proxy: bool
    """Trust X-Forwarded-For for the per-IP cap (only behind a proxy you control)."""
    tls_cert_path: str | None
    """PEM cert path for native TLS (wss). When cert + key are both set the bridge serves
    TLS itself; otherwise it is plain WS and MUST be fronted by a TLS terminator."""
    tls_key_path: str | None
    """PEM key path for native TLS (wss)."""


def _required(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise ValueError(f"Missing required env var {name}")
    return v


def _optional(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


def _num_from_env(name: str, fallback: float) -> float:
    """Parse a numeric env var, failing LOUD on non-numeric or negative values: a
    typo like MAX_CALL_MINUTES=abc or -1 must stop startup, not silently disable
    the governor (all these knobs are counts/durations where negative is never
    meaningful)."""
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return fallback
    try:
        n = float(raw)
    except ValueError:
        raise ValueError(f'Env var {name}="{raw}" is not a number') from None
    if n != n or n in (float("inf"), float("-inf")) or n < 0:
        raise ValueError(f'Env var {name}="{raw}" is not a non-negative number')
    return n


def load_config() -> BridgeConfig:
    return BridgeConfig(
        port=int(_num_from_env("PORT", 8080)),
        host=os.environ.get("BIND", "").strip() or "0.0.0.0",
        worker_shared_secret=_required("WORKER_SHARED_SECRET"),
        livekit_url=_required("LIVEKIT_URL"),
        livekit_api_key=_required("LIVEKIT_API_KEY"),
        livekit_api_secret=_required("LIVEKIT_API_SECRET"),
        livekit_agent_name=_optional("LIVEKIT_AGENT_NAME"),
        livekit_room_prefix=os.environ.get("LIVEKIT_ROOM_PREFIX", "").strip() or "msteams-",
        livekit_delete_room_on_end=os.environ.get("LIVEKIT_DELETE_ROOM_ON_END") != "false",
        max_call_minutes=_num_from_env("MAX_CALL_MINUTES", 0),
        goodbye_text=os.environ.get("GOODBYE_TEXT", "").strip() or DEFAULT_GOODBYE,
        goodbye_grace_ms=_num_from_env("GOODBYE_GRACE_MS", 8000),
        hmac_freshness_ms=_num_from_env("HMAC_FRESHNESS_MS", 60_000),
        max_connections=int(_num_from_env("MAX_CONNECTIONS", 0)),
        max_connections_per_ip=int(_num_from_env("MAX_CONNECTIONS_PER_IP", 0)),
        pre_start_timeout_ms=_num_from_env("PRE_START_TIMEOUT_MS", 0),
        worker_idle_timeout_ms=_num_from_env("WORKER_IDLE_TIMEOUT_MS", 0),
        trust_proxy=os.environ.get("TRUST_PROXY_XFF") == "true",
        tls_cert_path=_optional("TLS_CERT_PATH"),
        tls_key_path=_optional("TLS_KEY_PATH"),
    )
