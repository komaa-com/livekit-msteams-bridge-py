"""One Teams call: pairs the worker WebSocket with one LiveKit room (agent
dispatched into it) and relays audio between them.

Both sides speak 16 kHz mono PCM16: the worker natively, the room via the
SDK's resampling AudioStream/AudioSource - so the hot path is copy-only.

Barge-in note: interruption handling (VAD, turn-taking, cutting the agent
off) lives INSIDE the LiveKit agent session - the room gives the bridge no
interruption event to map to assistant.cancel, so up to ~1 s of already-
relayed agent audio may play out after a barge-in (the worker's own
flush-on-silence smooths this). Documented limitation of the room transport.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import deque
from typing import Any, Awaitable, Callable, Protocol

from .config import BridgeConfig
from .log import Logger, logger
from .metrics import metric_inc
from .protocol import parse_worker_message, pcm16k_bytes_to_ms

# Pending caller-audio cap while the room connects: 250 x 20 ms = 5 s.
MAX_PENDING_AUDIO_FRAMES = 250

# Pending contextual-update cap while the room connects (participants/dtmf).
MAX_PENDING_CONTEXT = 32

# Outbound (bridge->worker) send-buffer cap: above it, drop realtime frames.
MAX_OUTBOUND_BUFFER_BYTES = 1 * 1024 * 1024

# Dead-peer window: worker heartbeats every 30 s -> 3 missed pings ends the call.
DEFAULT_WORKER_IDLE_TIMEOUT_MS = 90_000


def _now_ms() -> float:
    return time.monotonic() * 1000


class WorkerPort(Protocol):
    """What the session needs from the worker connection; the server provides
    the real one, tests fake it."""

    @property
    def is_open(self) -> bool: ...

    @property
    def buffered_bytes(self) -> int: ...

    def send_text(self, payload: str) -> None: ...
    def close(self, code: int, reason: str) -> None: ...


class AgentRoomPort(Protocol):
    """What the relay needs from the LiveKit side of a call. The real
    implementation is LiveKitRoomPort (livekit_room.py); tests fake it."""

    room_name: str

    async def publish_caller_audio(self, base64_pcm: str) -> None: ...
    def send_context(self, text: str) -> None: ...
    def send_goodbye(self, text: str) -> None: ...
    async def close(self) -> None: ...


class RoomHandlers:
    """Callbacks the session wires into the room connector."""

    __slots__ = ("on_agent_audio", "on_agent_joined", "on_closed", "on_error")

    def __init__(
        self,
        on_agent_audio: Callable[[str], None],
        on_agent_joined: Callable[[str], None],
        on_closed: Callable[[str], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        self.on_agent_audio = on_agent_audio
        self.on_agent_joined = on_agent_joined
        self.on_closed = on_closed
        self.on_error = on_error


# Injectable room connector so tests can substitute a fake room.
RoomConnector = Callable[[BridgeConfig, Logger, str, dict[str, str], RoomHandlers], Awaitable[AgentRoomPort]]


class _TileSink:
    """video_relay.TileSink over a session: worker socket state + the outbound
    AUDIO media clock (the ts contract that makes A/V skew measurable)."""

    __slots__ = ("_session",)

    def __init__(self, session: "CallSession") -> None:
        self._session = session

    def is_open(self) -> bool:
        return self._session.worker.is_open

    def buffered_bytes(self) -> int:
        return self._session.worker.buffered_bytes

    def now_media_ms(self) -> int:
        return round(self._session._out_timestamp_ms)

    def send_frame(self, seq: int, ts: int, data_base64: str, width: int, height: int) -> None:
        sent = self._session._send_to_worker(
            {
                "type": "display.frame",
                "seq": seq,
                "ts": ts,
                "mime": "image/jpeg",
                "dataBase64": data_base64,
                "width": width,
                "height": height,
            }
        )
        if sent:
            metric_inc("bridge_video_frames_sent_total")


class CallSession:
    """Relay for a single authenticated worker connection.

    The server feeds inbound worker frames via handle_worker_message() and
    signals disconnect via handle_worker_close(); everything outbound goes
    through the WorkerPort.
    """

    def __init__(
        self,
        cfg: BridgeConfig,
        worker: WorkerPort,
        call_id: str,
        connect_room: RoomConnector,
        on_closed: Any = None,
    ) -> None:
        self.cfg = cfg
        self.worker = worker
        self.call_id = call_id
        self.log = logger(f"call:{call_id[:12]}")
        self._connect_room = connect_room
        self._on_closed = on_closed

        self.room: AgentRoomPort | None = None
        self.closed = False
        self.session_started = False

        # outbound audio bookkeeping (bridge -> worker)
        self._out_seq = 0
        self._out_timestamp_ms = 0.0
        # backpressure log throttle
        self._dropped_frames = 0
        self._last_backpressure_warn_ms = 0.0

        # caller audio / context arriving while the room is still connecting
        self._pending_audio: deque[str] = deque(maxlen=MAX_PENDING_AUDIO_FRAMES)
        self._pending_context: deque[str] = deque(maxlen=MAX_PENDING_CONTEXT)

        # Teams recording gate: nothing is persisted by this bridge, but the
        # state is tracked and surfaced to the agent as context.
        self._recording_active = False

        # EXPERIMENTAL avatar video relay (LIVEKIT_TILE_VIDEO): unwire on teardown
        self._stop_avatar_relay: Callable[[], None] | None = None

        # governors
        self._governor_handle: asyncio.TimerHandle | None = None
        self._goodbye_handle: asyncio.TimerHandle | None = None
        self._goodbye_in_progress = False

        self._started_at = time.monotonic()

        # dead-peer detection (worker heartbeats every 30 s; a half-open socket
        # would otherwise hold the room + the 409 dedup lock for hours)
        self._last_worker_activity_ms = _now_ms()
        idle_ms = cfg.worker_idle_timeout_ms if cfg.worker_idle_timeout_ms > 0 else DEFAULT_WORKER_IDLE_TIMEOUT_MS
        self._idle_ms = idle_ms
        self._idle_task = asyncio.create_task(self._idle_watchdog(max(0.02, min(idle_ms / 6000, 15.0))))

    # ---- lifecycle wiring (called by the server's read loop) ----

    @property
    def has_started(self) -> bool:
        """Whether session.start has arrived (the server's pre-start timer asks)."""
        return self.session_started

    def handle_worker_message(self, raw: str | bytes) -> None:
        self._last_worker_activity_ms = _now_ms()  # any inbound frame proves the peer is alive
        try:
            self._on_worker_message(raw)
        except Exception as err:
            # a handler error must never escape into the server's read loop
            self.log.error(f"error handling worker message: {err}")

    def handle_worker_close(self) -> None:
        self._teardown("worker-closed")

    def handle_worker_error(self, err: Exception) -> None:
        self.log.warn(f"worker socket error: {err}")
        self._teardown("worker-error")

    async def _idle_watchdog(self, interval_s: float) -> None:
        while not self.closed:
            await asyncio.sleep(interval_s)
            if self.closed:
                return
            if _now_ms() - self._last_worker_activity_ms > self._idle_ms:
                self.log.warn(f"no worker message in {int(self._idle_ms)}ms (dead peer?); ending the call")
                self.end_call("worker-idle-timeout")
                return

    # ---- worker -> bridge ----

    def _on_worker_message(self, raw: str | bytes) -> None:
        msg = parse_worker_message(raw)
        if msg is None:
            metric_inc("bridge_worker_frames_unparseable_total")
            self.log.warn("unparseable worker frame; dropping")
            return
        mtype = msg["type"]
        if mtype == "session.start":
            if self.session_started:
                self.log.warn("duplicate session.start ignored")
                return
            # Mark started SYNCHRONOUSLY: audio frames can arrive between this
            # message and the scheduled coroutine's first step, and they must be
            # buffered (not dropped) for the flush after connect.
            self.session_started = True
            asyncio.ensure_future(self._on_session_start_safe(msg))
        elif mtype == "audio.frame":
            # hot path: caller audio -> room. While the room is still connecting,
            # buffer (bounded) so the caller's first words are not lost.
            payload = msg.get("payloadBase64")
            if not isinstance(payload, str):
                return
            if self.room is not None:
                metric_inc("bridge_frames_to_agent_total")
                asyncio.ensure_future(self._publish_caller_audio_safe(payload))
            elif self.session_started:
                self._pending_audio.append(payload)  # deque drops the oldest at cap
        elif mtype == "ping":
            self._send_to_worker({"type": "pong", "ts": msg.get("ts")})
        elif mtype == "participants":
            count = msg.get("count")
            if isinstance(count, (int, float)):
                if count == 1:
                    self._push_context("This is a 1:1 call with a single human caller.")
                elif count > 1:
                    self._push_context(
                        f"There are {int(count)} human participants on this call. Stay quiet unless directly addressed."
                    )
                # count 0 = roster momentarily empty/unknown; say nothing rather than claim a 1:1
        elif mtype == "dtmf":
            digit = msg.get("digit")
            if isinstance(digit, str) and digit:
                self._push_context(f'The caller pressed the "{digit}" key on their keypad.')
        elif mtype == "recording.status":
            active = msg.get("status") == "active"
            self.log.info(f"recording.status = {msg.get('status')}")
            # surface the compliance-relevant state change to the agent so it can
            # disclose/adjust ("this call is being recorded")
            if active != self._recording_active:
                self._recording_active = active
                self._push_context(
                    "The Microsoft Teams call recording is now ACTIVE."
                    if active
                    else "The Microsoft Teams call recording is not active."
                )
        elif mtype == "video.frame":
            # The Teams tile is rendered by the worker's own avatar; inbound video
            # to the agent is a future feature (publish as a room video track).
            self.log.debug("video.frame ignored (no room video publish in v1)")
        elif mtype == "assistant.say":
            # worker-side governor: ask the agent to speak, the worker tears down
            # after. An empty text would ask the agent to say nothing - fall back
            # to the configured goodbye line.
            text = msg.get("text")
            text = text.strip() if isinstance(text, str) else ""
            self._perform_goodbye(text or self.cfg.goodbye_text)
        elif mtype == "session.end":
            self.log.info(f"session.end from worker: {msg.get('reason')}")
            self._teardown("worker-session-end")
        else:
            self.log.debug(f"ignoring worker message type {mtype}")

    async def _publish_caller_audio_safe(self, payload: str) -> None:
        room = self.room
        if room is None:
            return
        try:
            await room.publish_caller_audio(payload)
        except Exception as err:
            self.log.warn(f"publish_caller_audio failed: {err}")

    async def _on_session_start_safe(self, msg: dict[str, Any]) -> None:
        try:
            await self._on_session_start(msg)
        except Exception as err:
            self.log.error(f"session.start handling failed: {err}")

    async def _on_session_start(self, msg: dict[str, Any]) -> None:
        if self.closed:
            # a session.end/close raced ahead of this queued handler: do not create
            # a room + dispatch a billed agent job that nothing owns
            return
        msg_call_id = msg.get("callId")
        if msg_call_id and msg_call_id != self.call_id:
            metric_inc("bridge_callid_mismatch_total")
            self.log.error(f"session.start callId {msg_call_id} != URL callId {self.call_id}; closing")
            self.end_call("callid-mismatch")
            return
        direction = msg.get("direction") or "inbound"
        recording = msg.get("recordingStatus") or "unknown"
        self.log.info(f"session.start (direction={direction}, recording={recording})")
        self._recording_active = recording == "active"

        # Dispatch metadata: nullable caller fields are defaulted, never null; the
        # AAD id is included only when Teams provides one (per-person, never shared).
        caller = msg.get("caller") or {}
        metadata: dict[str, str] = {
            "source": "msteams",
            "caller_name": (caller.get("displayName") or "").strip() or "caller",
            "tenant_id": (caller.get("tenantId") or "").strip() or "unknown-tenant",
            "call_direction": (msg.get("direction") or "").strip() or "inbound",
        }
        aad_id = (caller.get("aadId") or "").strip()
        if aad_id:
            metadata["user_id"] = aad_id

        handlers = RoomHandlers(
            on_agent_audio=self._emit_audio_to_worker,
            on_agent_joined=lambda identity: self.log.info(f'agent "{identity}" joined the room'),
            on_closed=self._on_room_closed,
            on_error=lambda err: self.log.warn(f"room error: {err}"),
        )
        try:
            try:
                room = await self._connect_room(self.cfg, self.log, self.call_id, metadata, handlers)
            except Exception as first_err:
                # one retry with a short delay: a transient LiveKit blip should
                # not end the call on the first attempt
                self.log.warn(f"room connect failed ({first_err}); retrying once")
                await asyncio.sleep(0.3)
                if self.closed:
                    return
                room = await self._connect_room(self.cfg, self.log, self.call_id, metadata, handlers)
        except Exception as err:
            metric_inc("bridge_room_connect_failures_total")
            self.log.error(f"could not join the LiveKit room: {err}")
            self.end_call("agent-unavailable")
            return

        # the worker may have dropped DURING the connect; a room nothing owns
        # would leak a live agent job that nothing ever closes
        if self.closed:
            self.log.info("worker closed during room connect; leaving the orphaned room")
            asyncio.ensure_future(self._close_room_safe(room))
            return
        self.room = room

        while self._pending_audio:
            metric_inc("bridge_frames_to_agent_total")
            asyncio.ensure_future(self._publish_caller_audio_safe(self._pending_audio.popleft()))
        while self._pending_context:
            room.send_context(self._pending_context.popleft())
        self.log.info(f'LiveKit room "{room.room_name}" relaying')

        # EXPERIMENTAL: avatar video relay onto the Teams tile (default off).
        # Optional on the port protocol; fakes without it simply skip the feature.
        if self.cfg.tile_video != "off" and hasattr(room, "start_avatar_relay"):
            try:
                self._stop_avatar_relay = room.start_avatar_relay(_TileSink(self))
            except Exception as err:
                self.log.warn(f"avatar video relay failed to start: {err}")

        # bridge-side governor: LiveKit doesn't know about your billing
        if self.cfg.max_call_minutes > 0:
            loop = asyncio.get_running_loop()
            self._governor_handle = loop.call_later(self.cfg.max_call_minutes * 60, self._on_governor_limit)
            self.log.info(f"governor armed: max {self.cfg.max_call_minutes:g} min")

    def _on_room_closed(self, reason: str) -> None:
        self.log.info(f"room closed: {reason}")
        self.end_call("agent-disconnected")

    @staticmethod
    async def _close_room_safe(room: AgentRoomPort) -> None:
        try:
            await room.close()
        except Exception:
            pass

    def _push_context(self, text: str) -> None:
        if self.room is not None:
            self.room.send_context(text)
        elif self.session_started and not self.closed:
            self._pending_context.append(text)  # deque drops the oldest at cap

    # ---- governors ----

    def _on_governor_limit(self) -> None:
        if self.closed:
            return
        self.log.info("governor: call time limit reached")
        metric_inc("bridge_governor_time_limit_total")
        self._perform_goodbye(self.cfg.goodbye_text)
        # One deadline: the goodbye request is a data publish with no reported
        # duration, so the grace IS the budget (nothing async can wedge the
        # call open past it).
        loop = asyncio.get_running_loop()
        self._goodbye_handle = loop.call_later(
            (self.cfg.goodbye_grace_ms + 500) / 1000, lambda: self.end_call("time-limit")
        )

    def _perform_goodbye(self, text: str) -> None:
        """Ask the agent to say the goodbye (data topic "teams.goodbye"; the
        agent implements the actual speech - there is no bridge-side TTS on the
        room transport). Both governors funnel here; first one wins. The
        worker-side playback is flushed first (assistant.cancel) so Teams-side
        buffered agent audio cannot eat the grace window; whether the AGENT
        interrupts its own in-flight turn to speak the goodbye is the agent's
        choice (see the example agents' teams.goodbye handler)."""
        if self._goodbye_in_progress:
            self.log.info("goodbye already in progress; ignoring duplicate")
            return
        self._goodbye_in_progress = True
        metric_inc("bridge_goodbyes_requested_total")
        self.log.info("requesting agent goodbye")
        # turnId 0: the bridge does not track worker turn ids; the worker's
        # playback flush ignores the value, the field only has to serialize
        # (same contract as the Node bridge).
        self._send_to_worker({"type": "assistant.cancel", "turnId": 0})
        if self.room is not None:
            self.room.send_goodbye(text)

    # ---- plumbing ----

    def _emit_audio_to_worker(self, base64_pcm: str) -> None:
        frame = {
            "type": "audio.frame",
            "seq": self._out_seq,
            "timestampMs": round(self._out_timestamp_ms),
            "payloadBase64": base64_pcm,
        }
        self._out_seq += 1
        # exact decoded length (frames are <=1 KB, so the decode is cheap and is
        # correct for unpadded base64 where arithmetic on the string length drifts)
        self._out_timestamp_ms += pcm16k_bytes_to_ms(len(base64.b64decode(base64_pcm)))
        metric_inc("bridge_frames_to_worker_total")
        self._send_to_worker(frame)

    def _send_to_worker(self, msg: dict[str, Any]) -> bool:
        """Send one frame; False when the frame was dropped (socket closed or
        realtime backpressure), True when it was queued for delivery."""
        if not self.worker.is_open:
            return False
        # Backpressure: only the bulky realtime frames are droppable; control
        # frames (pong, session.end, assistant.cancel) are tiny and
        # semantically load-bearing.
        droppable = msg.get("type") in ("audio.frame", "display.image", "display.frame")
        if droppable and self.worker.buffered_bytes > MAX_OUTBOUND_BUFFER_BYTES:
            self._dropped_frames += 1
            metric_inc("bridge_frames_dropped_total")
            now = _now_ms()
            if now - self._last_backpressure_warn_ms >= 1000:
                self.log.warn(
                    f"worker send backpressure: dropped {self._dropped_frames} frame(s) "
                    f"(buffered {self.worker.buffered_bytes} bytes)"
                )
                self._last_backpressure_warn_ms = now
                self._dropped_frames = 0
            return False
        self.worker.send_text(json.dumps(msg))
        return True

    def shutdown(self, reason: str) -> None:
        """Graceful external shutdown (SIGTERM drain)."""
        self.end_call(reason)

    def end_call(self, reason: str) -> None:
        if not self.closed:
            self._send_to_worker({"type": "session.end", "reason": reason})
        self._teardown(reason)

    def _teardown(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        self.log.info(f"teardown: {reason}")
        # bridge_call_seconds_total is recorded by the server's read loop (single
        # owner) - do not also count it here or every call reports ~2x.
        for handle in (self._governor_handle, self._goodbye_handle):
            if handle:
                handle.cancel()
        self._governor_handle = None
        self._goodbye_handle = None
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        if self._stop_avatar_relay is not None:
            try:
                self._stop_avatar_relay()
            except Exception:
                pass
            self._stop_avatar_relay = None
        if self.room is not None:
            asyncio.ensure_future(self._close_room_safe(self.room))
            self.room = None
        try:
            self.worker.close(1000, reason)
        except Exception:
            pass
        self._pending_audio.clear()
        self._pending_context.clear()
        try:
            if self._on_closed is not None:
                self._on_closed()
        except Exception:
            pass  # registry callback must never raise back into teardown
