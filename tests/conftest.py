from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from livekit_msteams_bridge.config import BridgeConfig
from livekit_msteams_bridge.vision import AMBIENT_VISION_DEFAULTS, VisionImage


def make_config(**overrides: Any) -> BridgeConfig:
    base: dict[str, Any] = dict(
        port=9442,
        host="127.0.0.1",
        ws_path="/msteams/calling",
        bridge_secret="test-secret",
        livekit_url="wss://test.livekit.cloud",
        livekit_api_key="APItest",
        livekit_api_secret="secret",
        tile_video="off",
        tile_video_fps=10,
        livekit_agent_name="standin-agent",
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
        ambient_vision=AMBIENT_VISION_DEFAULTS,
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
        self.vision: list[VisionImage] = []
        # One-shot delivery failure, so a test can exercise the refund/retry path.
        self.fail_next_vision = False
        self.closed = False

    async def publish_caller_audio(self, base64_pcm: str) -> None:
        self.audio.append(base64_pcm)

    def send_context(self, text: str) -> None:
        self.context.append(text)

    def send_goodbye(self, text: str) -> None:
        self.goodbyes.append(text)

    async def send_vision(self, image: VisionImage) -> None:
        if self.fail_next_vision:
            self.fail_next_vision = False
            raise RuntimeError("vision publish failed")
        self.vision.append(image)

    async def close(self) -> None:
        self.closed = True


class VisionlessRoomPort:
    """A room with NO send_vision route (an older embedder's port, or a narrower fake): the session
    must disable ambient vision for that call and otherwise carry on. Written out rather than
    subclassed from FakeRoomPort, because the whole point is the missing attribute."""

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
    """Let pending callbacks/tasks run.

    A bare `sleep(0)` loop only drains callbacks that are already ready; the ambient-vision flush
    chain parks on real awaits (deliver -> room), so a short real sleep is needed as well."""
    for _ in range(6):
        await asyncio.sleep(0)
    await asyncio.sleep(0.005)
    for _ in range(6):
        await asyncio.sleep(0)
