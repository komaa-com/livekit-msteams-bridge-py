"""Avatar video relay (video_relay.py): participant selection semantics and the
optional-Pillow JPEG encode path. Mirrors the Node package's videoRelay tests
so the two implementations stay behaviorally interchangeable."""

import io

from livekit_msteams_bridge.log import logger
from livekit_msteams_bridge.video_relay import (
    PUBLISH_ON_BEHALF,
    TILE_H,
    TILE_W,
    load_jpeg_encoder,
    select_participant,
)


class FakeParticipant:
    def __init__(self, identity: str, attributes: dict | None = None):
        self.identity = identity
        self.attributes = attributes or {}


class FakeRoom:
    def __init__(self, *participants: FakeParticipant):
        self.remote_participants = {p.identity: p for p in participants}


LOG = logger("test")


def test_explicit_identity_mode_selects_exactly_that_participant():
    room = FakeRoom(FakeParticipant("agent"), FakeParticipant("bithuman-avatar"))
    assert select_participant(room, "bithuman-avatar", "agent").identity == "bithuman-avatar"
    assert select_participant(room, "nobody", "agent") is None


def test_auto_prefers_publish_on_behalf_of_the_agent():
    avatar = FakeParticipant("bithuman-avatar", {PUBLISH_ON_BEHALF: "agent"})
    room = FakeRoom(FakeParticipant("agent"), avatar, FakeParticipant("monitor"))
    assert select_participant(room, "auto", "agent") is avatar


def test_auto_ignores_on_behalf_of_someone_else():
    stranger = FakeParticipant("other-avatar", {PUBLISH_ON_BEHALF: "other-agent"})
    agent = FakeParticipant("agent")
    room = FakeRoom(stranger, agent)
    assert select_participant(room, "auto", "agent") is agent


def test_auto_selects_the_agent_itself_when_it_publishes_both():
    # The verified real-world setup: the avatar IS the audio-relayed participant
    # (kind-based binding lands on it), publishing audio and video together.
    both = FakeParticipant("avatar-worker", {PUBLISH_ON_BEHALF: "agent-that-left"})
    room = FakeRoom(both, FakeParticipant("monitor"))
    assert select_participant(room, "auto", "avatar-worker") is both


def test_auto_without_a_bound_identity_selects_nothing():
    room = FakeRoom(FakeParticipant("someone", {PUBLISH_ON_BEHALF: "x"}))
    assert select_participant(room, "auto", None) is None


def test_jpeg_encoder_resizes_to_the_tile():
    encode = load_jpeg_encoder(LOG)
    if encode is None:  # Pillow not installed in this env: the no-op path is the contract
        return
    from PIL import Image

    rgb = bytes(3 * 100 * 80)  # black 100x80 input, deliberately not tile-sized
    jpeg = encode(rgb, 100, 80)
    assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI
    img = Image.open(io.BytesIO(jpeg))
    assert img.size == (TILE_W, TILE_H)
