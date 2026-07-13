import asyncio
import base64
import json

from livekit_msteams_bridge.session import MAX_OUTBOUND_BUFFER_BYTES, CallSession

from conftest import FakeRoomPort, FakeWorkerPort, make_config, settle


def make_session(cfg=None, worker=None, room=None):
    worker = worker or FakeWorkerPort()
    room = room or FakeRoomPort()

    async def connector(cfg_, log, call_id, metadata, handlers):
        connector.handlers = handlers  # type: ignore[attr-defined]
        connector.metadata = metadata  # type: ignore[attr-defined]
        return room

    session = CallSession(cfg or make_config(), worker, "call-1", connect_room=connector)
    return session, worker, room, connector


def start_msg(**kw):
    msg = {
        "type": "session.start",
        "callId": "call-1",
        "threadId": "t",
        "caller": {"displayName": "Alice", "tenantId": "ten", "aadId": "aad-1"},
        "direction": "inbound",
    }
    msg.update(kw)
    return json.dumps(msg)


async def test_session_start_connects_room_with_metadata():
    session, worker, room, connector = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    assert session.has_started
    md = connector.metadata
    assert md["source"] == "msteams"
    assert md["caller_name"] == "Alice"
    assert md["tenant_id"] == "ten"
    assert md["user_id"] == "aad-1"
    session.end_call("test-done")


async def test_anonymous_caller_gets_no_user_id():
    session, worker, room, connector = make_session()
    session.handle_worker_message(start_msg(caller={}))
    await settle()
    md = connector.metadata
    assert "user_id" not in md
    assert md["caller_name"] == "caller"
    session.end_call("test-done")


async def test_audio_buffered_until_room_open_then_flushed():
    session, worker, room, _ = make_session()
    session.handle_worker_message(start_msg())
    session.handle_worker_message(
        json.dumps({"type": "audio.frame", "seq": 1, "timestampMs": 0, "payloadBase64": "QUJD"})
    )
    await settle()
    session.handle_worker_message(
        json.dumps({"type": "audio.frame", "seq": 2, "timestampMs": 20, "payloadBase64": "REVG"})
    )
    await settle()
    assert room.audio == ["QUJD", "REVG"]
    session.end_call("test-done")


async def test_callid_mismatch_ends_call():
    session, worker, room, _ = make_session()
    session.handle_worker_message(start_msg(callId="other-call"))
    await settle()
    assert session.closed
    ends = worker.of_type("session.end")
    assert ends and ends[0]["reason"] == "callid-mismatch"


async def test_agent_audio_relayed_with_seq_and_timestamp():
    session, worker, room, connector = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    pcm = base64.b64encode(b"\x00" * 640).decode()
    connector.handlers.on_agent_audio(pcm)
    connector.handlers.on_agent_audio(pcm)
    frames = worker.of_type("audio.frame")
    assert [f["seq"] for f in frames] == [0, 1]
    assert frames[0]["timestampMs"] == 0
    assert frames[1]["timestampMs"] == 20  # 640 bytes = 20 ms
    session.end_call("test-done")


async def test_worker_ping_gets_pong():
    session, worker, room, _ = make_session()
    session.handle_worker_message(json.dumps({"type": "ping", "ts": 777}))
    pongs = worker.of_type("pong")
    assert pongs and pongs[0]["ts"] == 777
    session.end_call("test-done")


async def test_participants_context_zero_says_nothing():
    session, worker, room, _ = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    session.handle_worker_message(json.dumps({"type": "participants", "count": 0}))
    assert room.context == []
    session.handle_worker_message(json.dumps({"type": "participants", "count": 3}))
    assert any("3 human participants" in c for c in room.context)
    session.handle_worker_message(json.dumps({"type": "participants", "count": 1}))
    assert any("1:1 call" in c for c in room.context)
    session.end_call("test-done")


async def test_dtmf_requires_string_digit():
    session, worker, room, _ = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    session.handle_worker_message(json.dumps({"type": "dtmf"}))
    session.handle_worker_message(json.dumps({"type": "dtmf", "digit": 42}))
    assert room.context == []
    session.handle_worker_message(json.dumps({"type": "dtmf", "digit": "5"}))
    assert any('"5"' in c for c in room.context)
    session.end_call("test-done")


async def test_recording_status_change_surfaces_context():
    session, worker, room, _ = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    session.handle_worker_message(json.dumps({"type": "recording.status", "status": "active"}))
    assert any("ACTIVE" in c for c in room.context)
    # unchanged state repeats say nothing
    n = len(room.context)
    session.handle_worker_message(json.dumps({"type": "recording.status", "status": "active"}))
    assert len(room.context) == n
    session.handle_worker_message(json.dumps({"type": "recording.status", "status": "stopped"}))
    assert any("not active" in c for c in room.context)
    session.end_call("test-done")


async def test_context_buffered_before_room_connects():
    worker = FakeWorkerPort()
    room = FakeRoomPort()
    release = asyncio.Event()

    async def slow_connector(cfg_, log, call_id, metadata, handlers):
        await release.wait()
        return room

    session = CallSession(make_config(), worker, "call-1", connect_room=slow_connector)
    session.handle_worker_message(start_msg())
    await settle()
    session.handle_worker_message(json.dumps({"type": "participants", "count": 2}))
    assert room.context == []  # still connecting
    release.set()
    await settle()
    assert any("2 human participants" in c for c in room.context)  # flushed after connect
    session.end_call("test-done")


async def test_assistant_say_sends_goodbye_topic_and_dedups():
    session, worker, room, _ = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    session.handle_worker_message(json.dumps({"type": "assistant.say", "text": "bye now"}))
    session.handle_worker_message(json.dumps({"type": "assistant.say", "text": "bye again"}))
    assert room.goodbyes == ["bye now"]  # first one wins
    cancels = worker.of_type("assistant.cancel")
    assert cancels  # playback flushed so buffered audio can't eat the grace
    session.end_call("test-done")


async def test_governor_fires_goodbye_then_time_limit():
    cfg = make_config(max_call_minutes=0.0005, goodbye_grace_ms=50)  # 30 ms limit
    session, worker, room, _ = make_session(cfg=cfg)
    session.handle_worker_message(start_msg())
    await settle()
    await asyncio.sleep(0.7)
    assert room.goodbyes == ["goodbye"]
    ends = worker.of_type("session.end")
    assert ends and ends[-1]["reason"] == "time-limit"
    assert session.closed


async def test_worker_close_tears_down_room():
    session, worker, room, _ = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    session.handle_worker_close()
    await settle()
    assert session.closed
    assert room.closed


async def test_worker_dropped_during_connect_closes_orphan_room():
    worker = FakeWorkerPort()
    room = FakeRoomPort()
    release = asyncio.Event()

    async def slow_connector(cfg_, log, call_id, metadata, handlers):
        await release.wait()
        return room

    session = CallSession(make_config(), worker, "call-1", connect_room=slow_connector)
    session.handle_worker_message(start_msg())
    await settle()
    session.handle_worker_close()  # worker drops while the room is still connecting
    release.set()
    await settle()
    assert room.closed  # the orphaned room (and its agent job) is closed


async def test_room_closed_ends_call():
    session, worker, room, connector = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    connector.handlers.on_closed("agent left")
    await settle()
    assert session.closed
    ends = worker.of_type("session.end")
    assert ends and ends[0]["reason"] == "agent-disconnected"


async def test_backpressure_drops_audio_keeps_control():
    session, worker, room, connector = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    worker.buffered = MAX_OUTBOUND_BUFFER_BYTES + 1
    pcm = base64.b64encode(b"\x00" * 640).decode()
    connector.handlers.on_agent_audio(pcm)
    assert worker.of_type("audio.frame") == []  # dropped
    session.handle_worker_message(json.dumps({"type": "ping", "ts": 1}))
    assert worker.of_type("pong")  # control frames always pass
    session.end_call("test-done")


async def test_video_frame_ignored():
    session, worker, room, _ = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    session.handle_worker_message(
        json.dumps(
            {
                "type": "video.frame",
                "source": "camera",
                "ts": 0,
                "width": 1,
                "height": 1,
                "mime": "image/jpeg",
                "dataBase64": "",
            }
        )
    )
    assert not session.closed  # ignored, no crash
    session.end_call("test-done")


async def test_junk_frames_dropped():
    session, worker, room, _ = make_session()
    session.handle_worker_message("not json at all")
    session.handle_worker_message(json.dumps({"noType": True}))
    assert not session.closed
    session.end_call("test-done")


async def test_empty_assistant_say_falls_back_to_configured_goodbye():
    session, worker, room, _ = make_session()
    session.handle_worker_message(start_msg())
    await settle()
    session.handle_worker_message(json.dumps({"type": "assistant.say", "text": "   "}))
    assert room.goodbyes == ["goodbye"]  # cfg.goodbye_text, not the blank string
    session.end_call("test-done")


async def test_room_connect_retries_once():
    worker = FakeWorkerPort()
    room = FakeRoomPort()
    attempts = []

    async def flaky_connector(cfg_, log, call_id, metadata, handlers):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient blip")
        return room

    session = CallSession(make_config(), worker, "call-1", connect_room=flaky_connector)
    session.handle_worker_message(start_msg())
    await asyncio.sleep(0.5)  # covers the 0.3 s retry delay
    assert len(attempts) == 2
    assert session.room is room  # second attempt succeeded, call is up
    assert worker.of_type("session.end") == []
    session.end_call("test-done")


async def test_room_connect_failing_twice_ends_call():
    worker = FakeWorkerPort()

    async def dead_connector(cfg_, log, call_id, metadata, handlers):
        raise RuntimeError("livekit down")

    session = CallSession(make_config(), worker, "call-1", connect_room=dead_connector)
    session.handle_worker_message(start_msg())
    await asyncio.sleep(0.5)
    assert session.closed
    ends = worker.of_type("session.end")
    assert ends and ends[0]["reason"] == "agent-unavailable"


async def test_display_frame_wire_shape():
    """The avatar tile sink emits exactly the schema's field names.

    The drift guard's surface check cannot see display.frame field drift (every
    required field name also appears in another message this bridge constructs),
    so this test IS the wire-shape protection for the hand-written construction
    site: it exercises the real sink and pins the exact JSON keys.
    """
    from livekit_msteams_bridge.session import _TileSink

    session, worker, room, connector = make_session()
    _TileSink(session).send_frame(7, 1234, "AQID", 640, 360)
    frames = worker.of_type("display.frame")
    assert len(frames) == 1
    frame = frames[0]
    assert sorted(frame.keys()) == [
        "dataBase64",
        "height",
        "mime",
        "seq",
        "ts",
        "type",
        "width",
    ]
    assert frame["seq"] == 7
    assert frame["ts"] == 1234
    assert frame["mime"] == "image/jpeg"
    assert frame["dataBase64"] == "AQID"
    assert frame["width"] == 640
    assert frame["height"] == 360
