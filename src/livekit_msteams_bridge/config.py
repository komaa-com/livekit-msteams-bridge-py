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

from .vision import AmbientVisionConfig, resolve_ambient_vision_config

DEFAULT_GOODBYE = "I'm sorry, we've reached the time limit for this call. Thank you for calling, goodbye!"

# Same default as the OpenClaw and Hermes plugins, so ONE StandIn identity URL shape works for every
# backend and the portal's bare-host completion is correct here too.
DEFAULT_WS_PATH = "/msteams/calling"


@dataclass(frozen=True)
class BridgeConfig:
    port: int
    """TCP port the bridge listens on for worker WebSocket upgrades."""
    host: str
    """Bind address."""
    ws_path: str
    """Base path the worker WebSocket is anchored on; a call dials `{ws_path}/{callId}`.
    Anchoring is what stops the bridge answering on every route it is handed, which would
    collide with anything else co-hosted on the same origin."""
    bridge_secret: str
    """Must equal the shared secret the StandIn media bridge signs with (HMAC upgrade check).
    Named for what it authenticates (StandIn TO this bridge): "worker" was ambiguous, since
    LiveKit has agent workers and StandIn has its own workers, and neither is this."""
    livekit_url: str
    """LiveKit server URL (wss://<project>.livekit.cloud or self-hosted)."""
    livekit_api_key: str
    """LiveKit API key; mints join tokens + dispatches agents + deletes rooms. Server-side only."""
    livekit_api_secret: str
    """LiveKit API secret paired with the key."""
    tile_video: str
    """Relay the agent avatar's video track onto the Teams tile.
    "auto" (default; the agent participant) | "off" | a specific identity.
    By default the bridge relays an avatar agent's video onto the caller's Teams
    tile; set "off" to opt out. Voice-only agents are unaffected either way (they
    publish no avatar video, so "auto" finds nothing to relay)."""
    tile_video_fps: float
    """Send rate for the relayed tile stream (frames/s). Default 15."""
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
    """Goodbye line sent to the agent (data topic "msteams.goodbye") on governor cutoff."""
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
    ambient_vision: AmbientVisionConfig
    """Continuous visual awareness of the call (screen-share / camera -> the agent).
    OFF by default: it is the one knob that costs money per frame."""


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
    value = _optional_num_from_env(name)
    return fallback if value is None else value


def _optional_num_from_env(name: str) -> float | None:
    """As _num_from_env, but reports "the operator said nothing" as None so a feature's own
    defaults table can fill the gap - the env layer must not restate a default, or the two copies
    eventually disagree about what "unset" means."""
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return None
    try:
        n = float(raw)
    except ValueError:
        raise ValueError(f'Env var {name}="{raw}" is not a number') from None
    if n != n or n in (float("inf"), float("-inf")) or n < 0:
        raise ValueError(f'Env var {name}="{raw}" is not a non-negative number')
    return n


def _optional_bool_from_env(name: str) -> bool | None:
    """Strict boolean: only "true"/"false". Fail loud for the same reason numerics do - a typo such
    as REQUIRE_RECORDING_STATUS=yes must stop startup, not quietly resolve to false and disable a
    compliance gate."""
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError(f'Env var {name}="{raw}" must be "true" or "false"')


def normalize_ws_path(raw: str) -> str:
    """Leading slash, no trailing slash, so a prefix comparison is a plain startswith().

    Fails LOUD here, at startup, on an empty or root path: those would anchor the bridge on every
    route again, silently. The request-time counterpart (server.call_id_from_path) does the
    opposite and degrades quietly, because it runs pre-auth where raising is the worse answer."""
    trimmed = (raw or "").strip().rstrip("/")
    if not trimmed:
        raise ValueError("WS_PATH must be a non-empty path such as /msteams/calling")
    return trimmed if trimmed.startswith("/") else f"/{trimmed}"


def load_config() -> BridgeConfig:
    # UNSET takes the default; WS_PATH="" is NOT unset and therefore throws, matching the
    # loud-on-typo policy of the numeric and boolean readers.
    raw_ws_path = os.environ.get("WS_PATH")
    return BridgeConfig(
        port=int(_num_from_env("PORT", 8080)),
        host=os.environ.get("BIND", "").strip() or "0.0.0.0",
        ws_path=normalize_ws_path(DEFAULT_WS_PATH if raw_ws_path is None else raw_ws_path),
        bridge_secret=_required("BRIDGE_SECRET"),
        livekit_url=_required("LIVEKIT_URL"),
        livekit_api_key=_required("LIVEKIT_API_KEY"),
        livekit_api_secret=_required("LIVEKIT_API_SECRET"),
        tile_video=os.environ.get("LIVEKIT_TILE_VIDEO", "").strip() or "auto",
        tile_video_fps=_num_from_env("LIVEKIT_TILE_VIDEO_FPS", 15),
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
        # Possibly-None values into the feature's single resolver: the defaults live in vision.py,
        # not here, so the docs and the env layer cannot drift apart.
        ambient_vision=resolve_ambient_vision_config(
            enabled=_optional_bool_from_env("AMBIENT_VISION"),
            max_per_minute=_optional_num_from_env("MAX_VISION_PER_MINUTE"),
            require_recording_status=_optional_bool_from_env("REQUIRE_RECORDING_STATUS"),
        ),
    )
