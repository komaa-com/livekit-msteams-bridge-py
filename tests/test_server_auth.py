import time

from livekit_msteams_bridge.hmac_auth import (
    LEGACY_SIGNATURE_HEADER,
    LEGACY_TIMESTAMP_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    sign,
)
from livekit_msteams_bridge.server import DEFAULT_WS_PATH, ReplayGuard, authorize_upgrade, call_id_from_path

from conftest import make_config

BASE = "/msteams/calling"


def _headers(secret: str, call_id: str, ts: int | None = None) -> dict:
    ts = ts if ts is not None else int(time.time() * 1000)
    return {TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: sign(secret, ts, call_id)}


def test_call_id_is_the_single_segment_under_the_base_path():
    assert call_id_from_path(f"{BASE}/abc", BASE) == "abc"
    assert call_id_from_path(f"{BASE}/abc?x=1", BASE) == "abc"
    # tolerated shapes: a trailing or doubled slash is still one segment
    assert call_id_from_path(f"{BASE}/abc/", BASE) == "abc"
    assert call_id_from_path(f"{BASE}//abc", BASE) == "abc"
    # decode happens AFTER the split, so an encoded slash is ONE callId and cannot traverse
    assert call_id_from_path(f"{BASE}/abc%2F123", BASE) == "abc/123"


def test_paths_outside_the_base_are_not_ours():
    assert call_id_from_path(BASE, BASE) is None  # base itself carries no callId
    assert call_id_from_path(f"{BASE}/", BASE) is None
    assert call_id_from_path(f"{BASE}/a/b", BASE) is None  # two segments: not ours
    assert call_id_from_path("/msteams/callingX/abc", BASE) is None  # prefix false-positive
    # the whole point of anchoring: the old endpoint shape must no longer answer
    assert call_id_from_path("/voice/msteams/stream/abc", BASE) is None
    assert call_id_from_path("/healthz", BASE) is None
    assert call_id_from_path(None, BASE) is None
    assert call_id_from_path("", BASE) is None


def test_base_path_degrades_instead_of_raising():
    """This runs in the PRE-AUTH upgrade path: an exception here answers an unauthenticated probe
    with a 500 instead of the 401 it earned, so a config that lacks ws_path must fall back."""
    assert call_id_from_path(f"{DEFAULT_WS_PATH}/abc", "") == "abc"
    assert call_id_from_path(f"{DEFAULT_WS_PATH}/abc", None) == "abc"  # type: ignore[arg-type]
    # a hand-built config may carry an unnormalized trailing slash
    assert call_id_from_path(f"{BASE}/abc", f"{BASE}/") == "abc"


def test_malformed_percent_escape_never_raises():
    """Python's unquote repairs a bad escape in place instead of raising (the Node sibling's
    decodeURIComponent throws and yields None). Either way, the pre-auth path must not blow up and
    anchoring still applies."""
    assert call_id_from_path(f"{BASE}/%zz", BASE) == "%zz"
    assert call_id_from_path("/elsewhere/%zz", BASE) is None


def test_custom_ws_path_is_honoured():
    cfg = make_config(ws_path="/custom/base")
    assert authorize_upgrade(cfg, "/custom/base/call-1", _headers("test-secret", "call-1")) == {"callId": "call-1"}
    assert "error" in authorize_upgrade(cfg, f"{BASE}/call-1", _headers("test-secret", "call-1"))


def test_authorize_ok():
    cfg = make_config()
    auth = authorize_upgrade(cfg, f"{BASE}/call-1", _headers("test-secret", "call-1"))
    assert auth == {"callId": "call-1"}


def test_authorize_accepts_legacy_headers():
    """Pre-rename workers send X-OpenClawTeamsBridge-*; they must keep working."""
    cfg = make_config()
    ts = int(time.time() * 1000)
    headers = {LEGACY_TIMESTAMP_HEADER: str(ts), LEGACY_SIGNATURE_HEADER: sign("test-secret", ts, "call-1")}
    assert authorize_upgrade(cfg, f"{BASE}/call-1", headers) == {"callId": "call-1"}


def test_authorize_bad_signature():
    cfg = make_config()
    headers = _headers("wrong-secret", "call-1")
    assert "error" in authorize_upgrade(cfg, f"{BASE}/call-1", headers)


def test_authorize_stale_timestamp():
    cfg = make_config()
    old = int(time.time() * 1000) - 120_000
    headers = _headers("test-secret", "call-1", old)
    auth = authorize_upgrade(cfg, f"{BASE}/call-1", headers)
    assert auth.get("error") == "stale or missing timestamp"


def test_authorize_missing_headers():
    cfg = make_config()
    assert "error" in authorize_upgrade(cfg, f"{BASE}/call-1", {})


def test_authorize_rejects_a_valid_signature_on_the_wrong_path():
    """Path anchoring runs BEFORE authentication, so even a perfectly signed handshake on a foreign
    route is rejected - the bridge must stop answering on every URL it is handed."""
    cfg = make_config()
    headers = _headers("test-secret", "call-1")
    auth = authorize_upgrade(cfg, "/voice/msteams/stream/call-1", headers)
    assert auth.get("error") == "expected /msteams/calling/{callId}"


def test_authorize_empty_secret_fails_closed():
    cfg = make_config(bridge_secret="")
    headers = _headers("", "call-1")
    assert "error" in authorize_upgrade(cfg, f"{BASE}/call-1", headers)


def test_replay_guard_blocks_second_use():
    cfg = make_config()
    replay = ReplayGuard(cfg.hmac_freshness_ms)
    headers = _headers("test-secret", "call-1")
    assert authorize_upgrade(cfg, f"{BASE}/call-1", headers, replay) == {"callId": "call-1"}
    assert authorize_upgrade(cfg, f"{BASE}/call-1", headers, replay).get("error") == "replayed handshake"


def test_replay_guard_expiry():
    guard = ReplayGuard(1000)
    now = 1_000_000
    assert guard.claim("c", now, "sig", now)
    assert not guard.claim("c", now, "sig", now + 10)
    # after ts + window the record expires (is_fresh would reject the tuple anyway)
    assert guard.claim("c", now, "sig", now + 1001)


def test_unauthenticated_probe_consumes_no_replay_slot():
    cfg = make_config()
    replay = ReplayGuard(cfg.hmac_freshness_ms)
    ts = int(time.time() * 1000)
    bad = {TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: "0" * 64}
    assert "error" in authorize_upgrade(cfg, f"{BASE}/call-1", bad, replay)
    assert replay.size == 0


def test_replay_guard_expiry_with_real_signature():
    # a captured, VALIDLY SIGNED handshake must be single-use inside the window
    # and unusable after it (is_fresh rejects it before the guard even runs)
    from livekit_msteams_bridge.hmac_auth import is_fresh

    cfg = make_config(hmac_freshness_ms=1000)
    replay = ReplayGuard(cfg.hmac_freshness_ms)
    ts = int(time.time() * 1000)
    sig = sign("test-secret", ts, "call-r")
    headers = {TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: sig}
    assert authorize_upgrade(cfg, f"{BASE}/call-r", headers, replay) == {"callId": "call-r"}
    # immediate replay: rejected by the guard
    assert authorize_upgrade(cfg, f"{BASE}/call-r", headers, replay).get("error") == "replayed handshake"
    # after ts + window the guard record expires, but freshness rejects the
    # stale timestamp anyway - the tuple is dead either way
    later = ts + cfg.hmac_freshness_ms + 1
    assert replay.claim("call-r", ts, sig, later)  # guard record aged out...
    assert not is_fresh(ts, cfg.hmac_freshness_ms, later)  # ...but is_fresh closes the door
