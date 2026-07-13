from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from livekit_msteams_bridge.config import BridgeConfig


def make_config(**overrides: Any) -> BridgeConfig:
    base: dict[str, Any] = dict(
        port=8080,
        host="127.0.0.1",
        worker_shared_secret="test-secret",
        livekit_url="wss://test.livekit.cloud",
        livekit_api_key="APItest",
        livekit_api_secret="secret",
        tile_video="off",
        tile_video_fps=10,
        livekit_agent_name="teams-voice-agent",
        livekit_room_prefix="msteams-",
        livekit_delete_room_on_end=True,
        max_call_minutes=0,
        goodbye_text="goodbye",
        goodbye_grace_ms=100,
        hmac_freshness_ms=60_000,
        max_connections=0,
        max_connections_per_ip=0,
        pre_start_timeout_ms=0,
        worker_idle_timeout_ms=0,
        trust_proxy=False,
        tls_cert_path=None,
        tls_key_path=None,
    )
    base.update(overrides)
    return BridgeConfig(**base)


class FakeWorkerPort:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None
        self.buffered = 0

    @property
    def is_open(self) -> bool:
        return self.closed is None

    @property
    def buffered_bytes(self) -> int:
        return self.buffered

    def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)

    def of_type(self, mtype: str) -> list[dict]:
        return [m for m in self.sent if m.get("type") == mtype]


class FakeRoomPort:
    def __init__(self, room_name: str = "msteams-test") -> None:
        self.room_name = room_name
        self.audio: list[str] = []
        self.context: list[str] = []
        self.goodbyes: list[str] = []
        self.closed = False

    async def publish_caller_audio(self, base64_pcm: str) -> None:
        self.audio.append(base64_pcm)

    def send_context(self, text: str) -> None:
        self.context.append(text)

    def send_goodbye(self, text: str) -> None:
        self.goodbyes.append(text)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_worker() -> FakeWorkerPort:
    return FakeWorkerPort()


@pytest.fixture
def fake_room() -> FakeRoomPort:
    return FakeRoomPort()


async def settle() -> None:
    """Let pending callbacks/tasks run."""
    for _ in range(6):
        await asyncio.sleep(0)
