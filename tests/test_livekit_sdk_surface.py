"""Guard against LiveKit SDK API drift: every rtc/api symbol livekit_room.py
relies on must exist with the expected shape. This is what lets the dependency
pin ride a minor-version range without a live LiveKit server in CI."""

import inspect
from datetime import timedelta

from livekit import api, rtc

from livekit_msteams_bridge.livekit_room import _http_url, is_agent_participant


def test_rtc_audio_surface():
    assert hasattr(rtc, "AudioSource") and hasattr(rtc, "AudioFrame")
    assert hasattr(rtc.LocalAudioTrack, "create_audio_track")
    sig = inspect.signature(rtc.AudioStream.from_track)
    assert "sample_rate" in sig.parameters and "num_channels" in sig.parameters
    frame = rtc.AudioFrame(data=b"\x00" * 640, sample_rate=16000, num_channels=1, samples_per_channel=320)
    assert len(frame.data.tobytes()) == 640


def test_rtc_room_surface():
    sig = inspect.signature(rtc.Room.connect)
    assert "options" in sig.parameters
    assert hasattr(rtc, "RoomOptions")
    opts = rtc.TrackPublishOptions()
    opts.source = rtc.TrackSource.SOURCE_MICROPHONE
    pd = inspect.signature(rtc.LocalParticipant.publish_data)
    assert "topic" in pd.parameters and "reliable" in pd.parameters
    assert int(rtc.ParticipantKind.PARTICIPANT_KIND_AGENT) >= 0
    assert hasattr(rtc.TrackKind, "KIND_AUDIO")


def test_api_surface():
    token = (
        api.AccessToken("k", "s" * 32)
        .with_identity("i")
        .with_ttl(timedelta(hours=6))
        .with_grants(
            api.VideoGrants(room_join=True, room="r", can_publish=True, can_subscribe=True, can_publish_data=True)
        )
        .to_jwt()
    )
    assert token.count(".") == 2
    assert hasattr(api, "LiveKitAPI")
    assert hasattr(api, "CreateAgentDispatchRequest")
    assert hasattr(api, "DeleteRoomRequest")
    req = api.CreateAgentDispatchRequest(agent_name="a", room="r", metadata="{}")
    assert req.agent_name == "a"


def test_is_agent_participant():
    class Agent:
        kind = int(rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)

    class Standard:
        kind = 0

    class NoKind:
        pass

    assert is_agent_participant(Agent())
    assert not is_agent_participant(Standard())
    assert not is_agent_participant(NoKind())


def test_http_url_normalization():
    assert _http_url("wss://x.livekit.cloud") == "https://x.livekit.cloud"
    assert _http_url("ws://localhost:7880") == "http://localhost:7880"
    assert _http_url("https://x") == "https://x"
