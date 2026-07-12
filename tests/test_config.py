import pytest

from livekit_msteams_bridge.config import load_config

REQUIRED = {
    "WORKER_SHARED_SECRET": "s",
    "LIVEKIT_URL": "wss://x.livekit.cloud",
    "LIVEKIT_API_KEY": "k",
    "LIVEKIT_API_SECRET": "sec",
}


def _set_required(monkeypatch):
    for k, v in REQUIRED.items():
        monkeypatch.setenv(k, v)


def test_defaults(monkeypatch):
    for k in list(REQUIRED) + ["PORT", "LIVEKIT_AGENT_NAME", "LIVEKIT_ROOM_PREFIX", "LIVEKIT_DELETE_ROOM_ON_END"]:
        monkeypatch.delenv(k, raising=False)
    _set_required(monkeypatch)
    cfg = load_config()
    assert cfg.port == 8080
    assert cfg.livekit_agent_name is None
    assert cfg.livekit_room_prefix == "msteams-"
    assert cfg.livekit_delete_room_on_end is True
    assert cfg.max_call_minutes == 0


def test_missing_required(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    with pytest.raises(ValueError, match="LIVEKIT_API_SECRET"):
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
