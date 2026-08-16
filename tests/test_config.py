import pytest

from livekit_msteams_bridge.config import load_config, normalize_ws_path

REQUIRED = {
    "BRIDGE_SECRET": "s",
    "LIVEKIT_URL": "wss://x.livekit.cloud",
    "LIVEKIT_API_KEY": "k",
    "LIVEKIT_API_SECRET": "sec",
}

OPTIONAL = [
    "PORT",
    "WS_PATH",
    "LIVEKIT_AGENT_NAME",
    "LIVEKIT_ROOM_PREFIX",
    "LIVEKIT_DELETE_ROOM_ON_END",
    "AMBIENT_VISION",
    "MAX_VISION_PER_MINUTE",
    "REQUIRE_RECORDING_STATUS",
]


def _set_required(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)


def _clean_env(monkeypatch):
    for k in list(REQUIRED) + OPTIONAL:
        monkeypatch.delenv(k, raising=False)
    _set_required(monkeypatch)


def test_defaults(monkeypatch):
    _clean_env(monkeypatch)
    cfg = load_config()
    assert cfg.port == 9442
    assert cfg.livekit_agent_name is None
    assert cfg.livekit_room_prefix == "msteams-"
    assert cfg.livekit_delete_room_on_end is True
    assert cfg.max_call_minutes == 0
    # What an identity registered with a bare host reaches: StandIn appends /{callId}.
    assert cfg.ws_path == "/msteams/calling"


def test_missing_required(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    with pytest.raises(ValueError, match="LIVEKIT_API_SECRET"):
        load_config()


def test_missing_bridge_secret(monkeypatch):
    """Renamed from WORKER_SHARED_SECRET with no alias and no fallback: an unset value must stop
    startup rather than quietly authenticate nobody-knows-who."""
    _set_required(monkeypatch)
    monkeypatch.delenv("BRIDGE_SECRET", raising=False)
    with pytest.raises(ValueError, match="BRIDGE_SECRET"):
        load_config()


def test_old_secret_name_is_not_an_alias(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.delenv("BRIDGE_SECRET", raising=False)
    monkeypatch.setenv("WORKER_SHARED_SECRET", "s")
    with pytest.raises(ValueError, match="BRIDGE_SECRET"):
        load_config()


def test_non_numeric_fails_loud(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("MAX_CALL_MINUTES", "abc")
    with pytest.raises(ValueError, match="MAX_CALL_MINUTES"):
        load_config()


def test_negative_fails_loud(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("MAX_CALL_MINUTES", "-1")
    with pytest.raises(ValueError, match="non-negative"):
        load_config()


def test_delete_room_opt_out(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("LIVEKIT_DELETE_ROOM_ON_END", "false")
    assert load_config().livekit_delete_room_on_end is False


# ---- WS_PATH ----


def test_normalize_ws_path_table():
    assert normalize_ws_path("/msteams/calling") == "/msteams/calling"
    assert normalize_ws_path("msteams/calling") == "/msteams/calling"
    assert normalize_ws_path("/msteams/calling/") == "/msteams/calling"
    assert normalize_ws_path("  /msteams/calling//  ") == "/msteams/calling"
    # idempotent, and the result is safe for a plain startswith() prefix test
    assert normalize_ws_path(normalize_ws_path("msteams/calling/")) == "/msteams/calling"


@pytest.mark.parametrize("raw", ["", "   ", "/", "///"])
def test_normalize_ws_path_fails_loud_on_a_rootless_path(raw):
    """The startup layer fails LOUD (unlike the request layer, which degrades): an empty WS_PATH
    would anchor the bridge on every route again, silently."""
    with pytest.raises(ValueError, match="WS_PATH"):
        normalize_ws_path(raw)


def test_ws_path_from_env(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("WS_PATH", "custom/base/")
    assert load_config().ws_path == "/custom/base"


def test_empty_ws_path_is_not_unset(monkeypatch):
    """UNSET takes the default; WS_PATH="" is a typo and must stop startup."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("WS_PATH", "")
    with pytest.raises(ValueError, match="WS_PATH"):
        load_config()


# ---- ambient vision ----


def test_ambient_vision_defaults_off(monkeypatch):
    _clean_env(monkeypatch)
    v = load_config().ambient_vision
    assert v.enabled is False  # the knob that costs money is opt-in
    assert v.max_per_minute == 30
    assert v.require_recording_status is True


def test_ambient_vision_from_env(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("AMBIENT_VISION", "true")
    monkeypatch.setenv("MAX_VISION_PER_MINUTE", "5")
    monkeypatch.setenv("REQUIRE_RECORDING_STATUS", "false")
    v = load_config().ambient_vision
    assert (v.enabled, v.max_per_minute, v.require_recording_status) == (True, 5, False)


def test_explicit_zero_budget_survives_the_resolver(monkeypatch):
    """A truthiness merge would silently restore 30 here; 0 must mean DISABLED."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("AMBIENT_VISION", "true")
    monkeypatch.setenv("MAX_VISION_PER_MINUTE", "0")
    assert load_config().ambient_vision.max_per_minute == 0


@pytest.mark.parametrize("key", ["AMBIENT_VISION", "REQUIRE_RECORDING_STATUS"])
def test_bool_env_is_strict(monkeypatch, key):
    """REQUIRE_RECORDING_STATUS=yes must stop startup, not quietly disable a compliance gate."""
    _clean_env(monkeypatch)
    monkeypatch.setenv(key, "yes")
    with pytest.raises(ValueError, match=key):
        load_config()
