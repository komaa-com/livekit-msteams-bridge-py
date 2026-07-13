"""Dependency-free counters exposed at GET /metrics in the Prometheus text
exposition format (0.0.4). Telephony ops need at minimum: how many calls,
how many right now, and what is being rejected/dropped."""

from __future__ import annotations

_counts: dict[str, float] = {}

_META: dict[str, tuple[str, str]] = {
    "bridge_calls_total": ("Calls accepted (worker sessions created)", "counter"),
    "bridge_calls_active": ("Live calls right now", "gauge"),
    "bridge_call_seconds_total": ("Total call duration in seconds", "counter"),
    "bridge_upgrades_rejected_auth_total": ("Upgrades rejected: bad/stale/replayed HMAC", "counter"),
    "bridge_upgrades_rejected_cap_total": ("Upgrades rejected: connection caps", "counter"),
    "bridge_upgrades_rejected_duplicate_total": ("Upgrades rejected: callId already live (409)", "counter"),
    "bridge_frames_to_agent_total": ("Caller audio frames published to the room", "counter"),
    "bridge_frames_to_worker_total": ("Agent audio frames relayed to the worker", "counter"),
    "bridge_frames_dropped_total": ("Frames dropped under worker backpressure", "counter"),
    "bridge_video_frames_sent_total": ("Avatar video frames relayed to the worker", "counter"),
    "bridge_video_frames_dropped_total": ("Avatar video frames dropped under worker backpressure", "counter"),
    "bridge_room_connect_failures_total": ("LiveKit room connect failures", "counter"),
    "bridge_governor_time_limit_total": ("Calls ended by the bridge-side time limit", "counter"),
    "bridge_goodbyes_requested_total": ("Goodbye requests sent to the agent (either governor)", "counter"),
    "bridge_worker_frames_unparseable_total": ("Inbound worker frames dropped as unparseable", "counter"),
    "bridge_callid_mismatch_total": ("session.start callId != authenticated URL callId", "counter"),
}


def metric_inc(name: str, by: float = 1) -> None:
    _counts[name] = _counts.get(name, 0) + by


def metric_dec(name: str) -> None:
    metric_inc(name, -1)


def reset_metrics() -> None:
    """Zero every counter. For test isolation only - never call in production."""
    _counts.clear()


def render_metrics() -> str:
    lines: list[str] = []
    for name, (help_text, mtype) in _META.items():
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name} {_counts.get(name, 0):g}")
    return "\n".join(lines) + "\n"
