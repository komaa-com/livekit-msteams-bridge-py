"""The HMAC scheme the StandIn media bridge signs upgrades with.

signature = HMAC-SHA256(secret, "{timestampMs}.{callId}") hex-lowercased.
The worker sends it on the WS upgrade in X-StandIn-Timestamp /
X-StandIn-Signature; the bridge replays the computation.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import math
import time

TIMESTAMP_HEADER = "x-standin-timestamp"
SIGNATURE_HEADER = "x-standin-signature"


def sign(secret: str, timestamp_ms: int | str, call_id: str) -> str:
    payload = f"{timestamp_ms}.{call_id}".encode("utf-8")
    return _hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp_ms: int | str, call_id: str, signature: str) -> bool:
    """Constant-time verification; False on any missing OR malformed input
    rather than raising - compare_digest raises TypeError on non-ASCII str
    input, and an attacker-supplied header must never turn a 401 into a 500."""
    if not secret or not call_id or not signature:
        return False
    try:
        expected = sign(secret, timestamp_ms, call_id)
        return _hmac.compare_digest(expected, signature.lower())
    except (TypeError, ValueError):
        return False


def is_fresh(timestamp_ms: float, window_ms: float, now_ms: float | None = None) -> bool:
    """Timestamp freshness check (the worker documents a +/-60s replay window)."""
    if not isinstance(timestamp_ms, (int, float)) or not math.isfinite(timestamp_ms):
        return False
    now = time.time() * 1000 if now_ms is None else now_ms
    return abs(now - timestamp_ms) <= window_ms
