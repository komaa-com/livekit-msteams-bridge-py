"""Ambient vision, unit level: budget, latch, source order, queue, media gate, release.

Each test here pins a decision that costs money or leaks memory when it silently regresses.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from livekit_msteams_bridge.vision import (
    AMBIENT_VISION_DEFAULTS,
    MAX_QUEUED_AMBIENT_IMAGES,
    AmbientVision,
    AmbientVisionConfig,
    VisionBudget,
    VisionImage,
    caption_for,
    describe_frame_owner,
    fallback_owner,
    resolve_ambient_vision_config,
    vision_source_of,
)


class NoopLog:
    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


def frame(**over: Any) -> dict[str, Any]:
    msg: dict[str, Any] = {
        "type": "video.frame",
        "source": "screenshare",
        "ts": 1,
        "width": 1280,
        "height": 720,
        "mime": "image/jpeg",
        "dataBase64": "AAAA",
    }
    msg.update(over)
    return msg


async def settle() -> None:
    """Let the flush chain (which parks on real awaits) run to completion."""
    for _ in range(4):
        await asyncio.sleep(0)
    await asyncio.sleep(0.005)
    for _ in range(4):
        await asyncio.sleep(0)


class Harness:
    def __init__(self, **over: Any) -> None:
        self.delivered: list[VisionImage] = []
        self.permitted = True
        self.ready = True
        self.now_ms = 1_000.0
        self.fail_next = False
        self.hold: asyncio.Future[None] | None = None
        cfg = resolve_ambient_vision_config(enabled=True, **over)
        self.vision = AmbientVision(
            call_id="c1",
            config=cfg,
            log=NoopLog(),
            media_permitted=lambda: self.permitted,
            sink_ready=lambda: self.ready,
            deliver=self._deliver,
            now=lambda: self.now_ms,
        )

    async def _deliver(self, image: VisionImage) -> None:
        if self.hold is not None:
            await self.hold
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("deliver failed")
        self.delivered.append(image)


# ---- pure helpers ----


def test_only_known_sources_are_relayed():
    assert vision_source_of("screenshare") == "screenshare"
    assert vision_source_of("camera") == "camera"
    assert vision_source_of("whiteboard") is None
    assert vision_source_of(None) is None


def test_attribution_degrades_instead_of_vanishing():
    assert describe_frame_owner("screenshare", "Sara") == "Sara's shared screen"
    assert describe_frame_owner("camera", "Sara") == "Sara's camera"
    assert describe_frame_owner("camera", "   ") is None
    assert describe_frame_owner("camera", None) is None
    assert fallback_owner("screenshare") == "a shared screen"
    assert fallback_owner("camera") == "a camera"
    assert caption_for("Sara's camera") == "Live frame of Sara's camera."


def test_defaults_table():
    assert AMBIENT_VISION_DEFAULTS == AmbientVisionConfig(
        enabled=False, max_per_minute=30, require_recording_status=True
    )
    assert resolve_ambient_vision_config() == AMBIENT_VISION_DEFAULTS


def test_resolver_merges_per_key_not_on_truthiness():
    resolved = resolve_ambient_vision_config(enabled=True, max_per_minute=0, require_recording_status=False)
    assert (resolved.enabled, resolved.max_per_minute, resolved.require_recording_status) == (True, 0, False)


# ---- budget ----


@pytest.mark.parametrize("cap", [0, -1])
def test_zero_or_negative_budget_disables(cap):
    """0 DISABLES the cap; it never means "unlimited"."""
    assert not VisionBudget(cap).try_consume("c", 0)


def test_budget_caps_then_slides():
    budget = VisionBudget(2)
    assert budget.try_consume("c", 1_000)
    assert budget.try_consume("c", 1_100)
    assert not budget.try_consume("c", 1_200)
    # the first hit ages out of the 60 s window and the slot frees up
    assert budget.try_consume("c", 61_500)


def test_budget_is_per_call_and_refundable():
    budget = VisionBudget(1)
    assert budget.try_consume("a", 0)
    assert budget.try_consume("b", 0)  # a different call has its own window
    assert not budget.try_consume("a", 0)
    budget.refund("a")
    assert budget.try_consume("a", 0)
    budget.refund("never-seen")  # no-op, not a crash
    budget.release("a")
    assert budget.try_consume("a", 0)


# ---- AmbientVision ----


async def test_disabled_instance_never_delivers():
    h = Harness()
    disabled = AmbientVision(
        call_id="c1",
        config=resolve_ambient_vision_config(enabled=False),
        log=NoopLog(),
        media_permitted=lambda: True,
        sink_ready=lambda: True,
        deliver=h._deliver,
        now=lambda: 0,
    )
    disabled.offer(frame())
    await settle()
    assert h.delivered == []


async def test_delivers_once_and_skips_identical_bytes():
    h = Harness()
    h.vision.offer(frame())
    await settle()
    assert len(h.delivered) == 1
    assert h.delivered[0].owner == "a shared screen"
    assert h.delivered[0].caption == "Live frame of a shared screen."
    assert h.delivered[0].data_base64 == "AAAA"  # never transcoded

    h.vision.offer(frame())  # a frozen screen costs nothing
    await settle()
    assert len(h.delivered) == 1

    h.vision.offer(frame(dataBase64="BBBB"))
    await settle()
    assert len(h.delivered) == 2


async def test_latch_is_per_source():
    h = Harness()
    h.vision.offer(frame(source="camera", participantName="Sara"))
    await settle()
    h.vision.offer(frame(source="screenshare", participantName="Ben"))
    await settle()
    assert [(i.source, i.owner) for i in h.delivered] == [
        ("camera", "Sara's camera"),
        ("screenshare", "Ben's shared screen"),
    ]


async def test_screenshare_wins_the_last_budget_slot():
    h = Harness(max_per_minute=1)
    h.vision.offer(frame(source="camera", dataBase64="CAM"))
    h.vision.offer(frame(source="screenshare", dataBase64="SCR"))
    await settle()
    # screenshare is tried first and exhaustion BREAKS the pass, so the camera is not sent
    assert [i.source for i in h.delivered] == ["screenshare"]


async def test_failed_delivery_refunds_and_stays_retryable():
    h = Harness(max_per_minute=1)
    h.fail_next = True
    h.vision.offer(frame())
    await settle()
    assert h.delivered == []
    # the slot was refunded AND the latch was not set, so the same frame goes out next pass
    h.vision.offer(frame())
    await settle()
    assert len(h.delivered) == 1


async def test_frames_before_the_room_is_up_are_queued_and_bounded():
    h = Harness()
    h.ready = False
    for i in range(8):
        h.vision.offer(frame(dataBase64=f"S{i}"))
        await settle()
    assert h.vision.queued_count == MAX_QUEUED_AMBIENT_IMAGES
    h.ready = True
    h.vision.flush()
    await settle()
    # oldest-evicted: ambient context is about NOW
    assert [i.data_base64 for i in h.delivered] == [f"S{i}" for i in range(2, 8)]
    assert h.vision.queued_count == 0


async def test_media_gate_blocks_capture_not_just_delivery():
    """Nothing is stored before the gate opens, so nothing captured beforehand can surface later."""
    h = Harness()
    h.permitted = False
    h.vision.offer(frame(dataBase64="SECRET"))
    await settle()
    assert h.delivered == []
    assert h.vision.queued_count == 0

    h.permitted = True
    h.vision.flush()
    await settle()
    assert h.delivered == []  # the pre-gate frame is gone, not merely withheld

    h.vision.offer(frame(dataBase64="OK"))
    await settle()
    assert [i.data_base64 for i in h.delivered] == ["OK"]


async def test_unknown_source_and_empty_payload_are_ignored():
    h = Harness()
    h.vision.offer(frame(source="whiteboard"))
    h.vision.offer(frame(dataBase64=""))
    h.vision.offer(frame(dataBase64=None))
    await settle()
    assert h.delivered == []


async def test_geometry_is_coerced_from_untyped_wire_values():
    h = Harness()
    h.vision.offer(frame(width="wide", height=720.9, ts=None))
    await settle()
    assert (h.delivered[0].width, h.delivered[0].height, h.delivered[0].ts) == (0, 720, 0)


async def test_concurrent_flush_does_not_pay_twice():
    h = Harness()
    h.hold = asyncio.get_running_loop().create_future()
    h.vision.offer(frame())
    await asyncio.sleep(0)
    h.vision.flush()  # a second pass over the same unlatched frame
    h.vision.flush()
    h.hold.set_result(None)
    h.hold = None
    await settle()
    assert len(h.delivered) == 1


async def test_release_makes_the_instance_inert():
    h = Harness()
    h.vision.offer(frame())
    await settle()
    h.vision.release()
    h.vision.offer(frame(dataBase64="AFTER"))
    h.vision.flush()
    await settle()
    assert [i.data_base64 for i in h.delivered] == ["AAAA"]
    assert h.vision.queued_count == 0
