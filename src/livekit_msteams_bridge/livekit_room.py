"""The real LiveKit side of a call: one room per Teams call, the bridge joins
as a publishing participant, the agent is dispatched into the same room
(explicit dispatch via the AgentDispatch service when LIVEKIT_AGENT_NAME is
set - LiveKit's recommended model; the dispatch is created right after the
room exists, which is exactly our shape: one fresh room per call).

Audio in:  worker audio.frame (PCM16K base64) -> AudioSource.capture_frame
Audio out: first remote audio track -> AudioStream resampled to 16 kHz mono
           -> worker audio.frame (the SDK resamples to the requested rate,
           so the hot path stays copy-only on our side)
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import timedelta
from typing import Any

from livekit import api, rtc

from .config import BridgeConfig
from .video_relay import start_video_relay
from .log import Logger

SAMPLE_RATE = 16_000
NUM_CHANNELS = 1

# Data topics the agent can listen on (documented in the README).
TOPIC_CONTEXT = "teams.context"
TOPIC_GOODBYE = "teams.goodbye"

_SAFE_CALL_ID = re.compile(r"[^A-Za-z0-9._@:-]")


def is_agent_participant(participant: Any) -> bool:
    """True when the participant is a LiveKit agent worker (dispatched via the
    agent framework). Used to bind "the agent" by KIND rather than by
    first-audio-wins, so a monitor/recorder/debugger that publishes audio first
    can neither be mistaken for the agent nor block the real agent's audio."""
    try:
        return int(participant.kind) == int(rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)
    except (AttributeError, TypeError, ValueError):
        return False


def _http_url(ws_url: str) -> str:
    """LiveKitAPI speaks HTTP; accept the ws(s):// form users configure."""
    if ws_url.startswith("wss://"):
        return "https://" + ws_url[len("wss://") :]
    if ws_url.startswith("ws://"):
        return "http://" + ws_url[len("ws://") :]
    return ws_url


class LiveKitRoomPort:
    """AgentRoomPort implementation over livekit-rtc. Thin: publish/subscribe
    plumbing only; relay logic lives in session.py."""

    def __init__(self, cfg: BridgeConfig, log: Logger, room_name: str) -> None:
        self._cfg = cfg
        self._log = log
        self.room_name = room_name
        self._room: rtc.Room | None = None
        self._source: rtc.AudioSource | None = None
        self._closed = False
        # The identity whose audio we relay = "the agent". Captured on first
        # audio subscribe; only THIS identity leaving ends the call (a monitor /
        # debugger / second participant leaving must not tear the Teams call down).
        self._agent_identity: str | None = None
        # One live pump keyed by track sid, RESET when the stream ends or the
        # track unsubscribes - an agent that unpublishes and re-publishes its
        # audio (avatar track swaps, mute-cycle republish) gets pumped again
        # instead of going silent for the rest of the call.
        self._active_pump_sid: str | None = None

    # ---- connect ----

    @classmethod
    async def connect(
        cls,
        cfg: BridgeConfig,
        log: Logger,
        call_id: str,
        metadata: dict[str, str],
        handlers: Any,
    ) -> "LiveKitRoomPort":
        # Sanitize: callId comes from a decoded URL segment (%2F would smuggle
        # "/"); keep room names to a safe charset and a conservative length.
        safe_call_id = _SAFE_CALL_ID.sub("-", call_id)
        # 100 matches the Node bridge so both implementations derive the SAME
        # room name for the same call (they must be interchangeable). Teams call
        # ids are ~50-80 chars; if your LiveKit server enforces a shorter room
        # name limit, shorten LIVEKIT_ROOM_PREFIX.
        room_name = f"{cfg.livekit_room_prefix}{safe_call_id}"[:100]
        port = cls(cfg, log, room_name)

        token = (
            api.AccessToken(cfg.livekit_api_key, cfg.livekit_api_secret)
            .with_identity("msteams-bridge")
            .with_ttl(timedelta(hours=6))
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .to_jwt()
        )

        room = rtc.Room()
        port._room = room
        port._wire_events(room, handlers)
        try:
            await room.connect(cfg.livekit_url, token, rtc.RoomOptions(auto_subscribe=True, dynacast=False))
        except Exception:
            port._closed = True
            raise
        log.info(f'LiveKit room "{room_name}" joined')

        try:
            # Explicit dispatch AFTER the room exists (connect creates it): the
            # documented Python pattern. Metadata reaches the agent as
            # ctx.job.metadata.
            if cfg.livekit_agent_name:
                await port._dispatch_agent(metadata)
                log.info(f'agent "{cfg.livekit_agent_name}" dispatched')

            source = rtc.AudioSource(SAMPLE_RATE, NUM_CHANNELS)
            track = rtc.LocalAudioTrack.create_audio_track("teams-caller", source)
            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_MICROPHONE
            await room.local_participant.publish_track(track, options)
            port._source = source
        except Exception:
            await port.close()
            raise
        return port

    async def _dispatch_agent(self, metadata: dict[str, str]) -> None:
        lkapi = api.LiveKitAPI(
            _http_url(self._cfg.livekit_url), self._cfg.livekit_api_key, self._cfg.livekit_api_secret
        )
        try:
            await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=self._cfg.livekit_agent_name or "",
                    room=self.room_name,
                    metadata=json.dumps(metadata),
                )
            )
        finally:
            await lkapi.aclose()

    def _wire_events(self, room: rtc.Room, handlers: Any) -> None:
        @room.on("track_subscribed")
        def _on_track_subscribed(
            track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant
        ) -> None:
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            # Bind "the agent" by participant KIND, not first-audio-wins: in a
            # room where a non-agent (monitor, recorder, second worker) publishes
            # audio first, first-audio-wins would bind the wrong identity AND
            # block the real agent's track behind the single-pump gate. Fall back
            # to first-audio only when the participant kind is unavailable AND no
            # agent has been bound yet (automatic-dispatch prototypes).
            if self._agent_identity is None:
                if not is_agent_participant(participant) and self._cfg.livekit_agent_name:
                    self._log.info(
                        f'ignoring audio from non-agent participant "{participant.identity}" '
                        "(waiting for the dispatched agent)"
                    )
                    return
                self._agent_identity = participant.identity
                handlers.on_agent_joined(participant.identity)
            elif participant.identity != self._agent_identity:
                self._log.debug(f'ignoring audio from non-agent participant "{participant.identity}"')
                return
            self._log.info(f'subscribed to agent audio from "{participant.identity}"')
            self._start_pump(track, participant.identity, handlers)

        @room.on("track_unsubscribed")
        def _on_track_unsubscribed(
            track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant
        ) -> None:
            if track.sid and track.sid == self._active_pump_sid:
                self._active_pump_sid = None  # allow a re-published track to pump

        @room.on("participant_disconnected")
        def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
            self._log.info(f'participant "{participant.identity}" left the room')
            # only the AGENT leaving ends the call
            if self._agent_identity and participant.identity == self._agent_identity:
                handlers.on_closed(f"agent {participant.identity} disconnected")

        @room.on("disconnected")
        def _on_disconnected(*args: Any) -> None:
            # Disconnected is FINAL: the SDK retries transient drops internally
            # (reconnecting/reconnected) before this fires.
            handlers.on_closed("room disconnected")

    def _start_pump(self, track: rtc.Track, identity: str, handlers: Any) -> None:
        if self._active_pump_sid:
            return  # one agent voice at a time; the next subscribe after it ends takes over
        self._active_pump_sid = track.sid or "unknown"

        async def pump() -> None:
            try:
                # request 16 kHz mono: the SDK resamples, keeping our side copy-only
                stream = rtc.AudioStream.from_track(track=track, sample_rate=SAMPLE_RATE, num_channels=NUM_CHANNELS)
                async for event in stream:
                    if self._closed:
                        break
                    pcm = event.frame.data.tobytes()
                    handlers.on_agent_audio(base64.b64encode(pcm).decode("ascii"))
            except Exception as err:
                if not self._closed:
                    handlers.on_error(err)
            finally:
                self._active_pump_sid = None
                self._log.debug(f'audio pump for "{identity}" ended')

        asyncio.ensure_future(pump())

    # ---- avatar video relay (EXPERIMENTAL, LIVEKIT_TILE_VIDEO) ----

    def start_avatar_relay(self, sink: Any) -> Any:
        """Arm the display.frame relay on this room; returns its stop().
        The relay keys off the SAME agent identity this port binds for audio
        (design finding D: never invent a second binding)."""
        assert self._room is not None
        return start_video_relay(
            self._cfg.tile_video,
            self._cfg.tile_video_fps,
            self._log,
            self._room,
            lambda: self._agent_identity,
            sink,
        )

    # ---- AgentRoomPort ----

    async def publish_caller_audio(self, base64_pcm: str) -> None:
        """Caller audio (base64 PCM16K) -> the room's published track."""
        source = self._source
        if source is None or self._closed:
            return
        buf = base64.b64decode(base64_pcm)
        # PCM16 = 2 bytes/sample: reject malformed frames loudly instead of
        # silently truncating an odd byte
        if len(buf) < 2 or len(buf) % 2 != 0:
            raise ValueError(f"malformed PCM16 payload ({len(buf)} bytes)")
        frame = rtc.AudioFrame(
            data=buf, sample_rate=SAMPLE_RATE, num_channels=NUM_CHANNELS, samples_per_channel=len(buf) // 2
        )
        await source.capture_frame(frame)

    def _publish_data(self, text: str, topic: str) -> None:
        room = self._room
        if room is None or self._closed:
            return
        payload = json.dumps({"text": text}).encode("utf-8")

        async def send() -> None:
            try:
                await room.local_participant.publish_data(payload, reliable=True, topic=topic)
            except Exception as err:
                self._log.warn(f"{topic} publish failed: {err}")

        asyncio.ensure_future(send())

    def send_context(self, text: str) -> None:
        """Non-interrupting context for the agent (data topic "teams.context")."""
        self._publish_data(text, TOPIC_CONTEXT)

    def send_goodbye(self, text: str) -> None:
        """Governor goodbye request for the agent (data topic "teams.goodbye")."""
        self._publish_data(text, TOPIC_GOODBYE)

    async def close(self) -> None:
        """Leave (and by default delete) the room."""
        if self._closed:
            return
        self._closed = True
        if self._room is not None:
            try:
                await self._room.disconnect()
            except Exception:
                pass
        if self._cfg.livekit_delete_room_on_end:
            # end the agent's job immediately instead of letting the room idle out
            try:
                lkapi = api.LiveKitAPI(
                    _http_url(self._cfg.livekit_url), self._cfg.livekit_api_key, self._cfg.livekit_api_secret
                )
                try:
                    await lkapi.room.delete_room(api.DeleteRoomRequest(room=self.room_name))
                finally:
                    await lkapi.aclose()
            except Exception as err:
                self._log.warn(f"delete_room failed (room will idle out): {err}")


connect_livekit_room = LiveKitRoomPort.connect
