"""Ambient vision: keep the agent continuously visually aware of the Teams call.

While a call is live, the newest picture of what each participant is SHARING (screen-share) and
SHOWING (camera) is handed to the agent as labelled context - without the caller invoking anything.
Nothing here ever makes the agent speak: a delivered frame is context it uses on its next natural
turn.

On this transport there is no model session the bridge can push into, so "deliver" means "publish
onto the room" (see the optional `send_vision` route on AgentRoomPort, a byte stream on the
`msteams.vision` topic). The queue below exists because the one moment delivery is impossible is
while the room is still connecting, so frames are held in a small newest-wins buffer and flushed the
instant the room is up.

OPT-IN. Vision tokens are the dominant cost of continuous perception, so `enabled` defaults to
false: a bridge nobody configured must not start spending because a worker happened to send video.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Mapping

from .log import Logger

# Safety-net poll interval. Frame ARRIVAL is the real trigger; this only catches frames that were
# skipped for a reason that later went away - the recording gate was closed, the room was still
# connecting, or the minute's budget was spent. A static screen sends no new frame, so without this
# the agent would never see it. Armed lazily on the first frame, so a call with no video has no timer.
AMBIENT_VISION_BACKSTOP_MS = 6_000

# Fallback-queue cap. Ambient context is about NOW, so eviction is oldest-first. Unbounded here
# means hundreds of megabytes retained per long call (50-200 KB of base64 per frame).
MAX_QUEUED_AMBIENT_IMAGES = 6

# Screen-share FIRST, camera second - so a tight budget spends its last slot on the screen, which
# carries far more information than a talking head.
VISION_SOURCE_ORDER: tuple[str, ...] = ("screenshare", "camera")

VisionSource = Literal["screenshare", "camera"]


def vision_source_of(raw: Any) -> VisionSource | None:
    """Map a wire `video.frame.source` onto a source we relay; unknown sources are ignored."""
    return raw if raw in ("screenshare", "camera") else None


@dataclass(frozen=True)
class AmbientVisionConfig:
    enabled: bool
    """Master switch. Off by default: this is the knob that costs money."""
    max_per_minute: float
    """Per-call spend cap over a sliding 60-second window.

    NOTE a deliberate divergence from the sibling OpenClaw plugin, where `0` meant UNLIMITED - the
    inverse of what everyone reads it as, and its only kill switch. Here `0` DISABLES, and the
    separate `enabled` flag is how the feature is turned on. Set a large number for "effectively
    unlimited"."""
    require_recording_status: bool
    """Hold frames back until Teams reports the call recording as active (Media Access obligation)."""


# Defaults, in one place so the env layer and the docs cannot drift apart.
AMBIENT_VISION_DEFAULTS = AmbientVisionConfig(enabled=False, max_per_minute=30, require_recording_status=True)


def resolve_ambient_vision_config(
    enabled: bool | None = None,
    max_per_minute: float | None = None,
    require_recording_status: bool | None = None,
) -> AmbientVisionConfig:
    """Merge per key on "the operator said nothing" (None), never on truthiness: an explicit
    MAX_VISION_PER_MINUTE=0 or AMBIENT_VISION=false must survive instead of being replaced by the
    default it happens to look falsy next to."""
    return AmbientVisionConfig(
        enabled=AMBIENT_VISION_DEFAULTS.enabled if enabled is None else enabled,
        max_per_minute=AMBIENT_VISION_DEFAULTS.max_per_minute if max_per_minute is None else max_per_minute,
        require_recording_status=(
            AMBIENT_VISION_DEFAULTS.require_recording_status
            if require_recording_status is None
            else require_recording_status
        ),
    )


@dataclass(frozen=True)
class VisionImage:
    """One picture handed to the agent, already attributed."""

    source: VisionSource
    mime: str
    data_base64: str
    """Base64 image bytes, exactly as the worker sent them (the bridge never transcodes)."""
    width: int
    height: int
    ts: int
    """Capture timestamp from the worker, epoch ms."""
    owner: str
    """Whose screen/camera this is, e.g. `Sara's shared screen` - never empty."""
    caption: str
    """One short sentence the agent can read as context."""


class VisionBudget:
    """Per-call vision spend cap: a sliding 60-second window per call.

    Pure apart from the injected `now_ms`, so it is unit-testable without fake timers.
    `max_per_minute <= 0` means DISABLED (see AmbientVisionConfig.max_per_minute).
    """

    def __init__(self, max_per_minute: float) -> None:
        self._max_per_minute = max_per_minute
        self._hits_by_call: dict[str, list[float]] = {}

    def try_consume(self, call_id: str, now_ms: float) -> bool:
        """True (recording a hit) while under budget; False when the caller must skip this frame."""
        if self._max_per_minute <= 0:
            return False
        recent = [t for t in self._hits_by_call.get(call_id, []) if now_ms - t < 60_000]
        if len(recent) >= self._max_per_minute:
            self._hits_by_call[call_id] = recent  # keep the trimmed window
            return False
        recent.append(now_ms)
        self._hits_by_call[call_id] = recent
        return True

    def refund(self, call_id: str) -> None:
        """Give back the most recent hit: the delivery it paid for never happened. Without this a
        failed push burns a budget slot forever. A refund for a call with no window is a no-op."""
        hits = self._hits_by_call.get(call_id)
        if hits:
            hits.pop()

    def release(self, call_id: str) -> None:
        """Drop a call's window when the call ends, or it leaks for the process lifetime."""
        self._hits_by_call.pop(call_id, None)


def describe_frame_owner(source: Any, participant_name: Any) -> str | None:
    """Whose screen/camera a frame shows, or None when the worker did not name the participant.
    Returning None (rather than a guess) lets the caller degrade the label to the source kind
    instead of dropping attribution entirely - a meeting screenshot with no owner is close to
    unusable."""
    name = participant_name.strip() if isinstance(participant_name, str) else ""
    if not name:
        return None
    return f"{name}'s shared screen" if source == "screenshare" else f"{name}'s camera"


def fallback_owner(source: VisionSource) -> str:
    """The degraded label used when the worker did not name the participant."""
    return "a shared screen" if source == "screenshare" else "a camera"


def caption_for(owner: str) -> str:
    return f"Live frame of {owner}."


def _as_int(value: Any) -> int:
    """Wire messages are plain dicts here (protocol.py hands back JSON, not a typed message), so
    the geometry fields are coerced rather than trusted; a missing or junk value becomes 0 instead
    of poisoning the stream attributes."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        return int(value)
    except (ValueError, OverflowError):
        return 0


@dataclass(frozen=True)
class _StoredFrame:
    source: VisionSource
    mime: str
    data_base64: str
    width: int
    height: int
    ts: int
    owner: str


class AmbientVision:
    """Per-call ambient vision: the frame store, the per-source change latch, the budget, the
    fallback queue and the backstop timer. One instance per CallSession."""

    def __init__(
        self,
        *,
        call_id: str,
        config: AmbientVisionConfig,
        log: Logger,
        media_permitted: Callable[[], bool],
        sink_ready: Callable[[], bool],
        deliver: Callable[[VisionImage], Awaitable[None]],
        now: Callable[[], float],
    ) -> None:
        """`media_permitted` is true while the bridge may process this call's media at all
        (recording gate + call still live) and is checked before a frame is even STORED, unlike the
        reference implementation, which stored everything and gated only delivery - so pre-recording
        frames sat in memory and stayed reachable afterwards. `sink_ready` is true when the agent
        side can accept an image right now (the room is connected). `deliver` MUST raise on failure,
        so the budget can be refunded. `now` is injected so the budget window is testable without
        waiting."""
        self._call_id = call_id
        self._config = config
        self._log = log
        self._media_permitted = media_permitted
        self._sink_ready = sink_ready
        self._deliver = deliver
        self._now = now
        self._budget = VisionBudget(config.max_per_minute)
        self._latest: dict[str, _StoredFrame] = {}
        # Bytes of the frame most recently DELIVERED per source: the change latch.
        self._last_pushed: dict[str, str] = {}
        self._queued: list[VisionImage] = []
        self._backstop: asyncio.Task[None] | None = None
        self._flushing = False
        self._dirty = False
        self._released = False
        self._announced_queue_route = False

    @property
    def queued_count(self) -> int:
        """Frames currently held for a room that is not up yet (tests read this)."""
        return len(self._queued)

    def offer(self, frame: Mapping[str, Any]) -> None:
        """Store an inbound worker frame and try to deliver. A no-op when the feature is off."""
        if not self._config.enabled or self._released:
            return
        source = vision_source_of(frame.get("source"))
        data = frame.get("dataBase64")
        if source is None or not isinstance(data, str) or not data:
            return
        if not self._media_permitted():
            return
        mime = frame.get("mime")
        self._latest[source] = _StoredFrame(
            source=source,
            mime=mime if isinstance(mime, str) and mime else "image/jpeg",
            data_base64=data,
            width=_as_int(frame.get("width")),
            height=_as_int(frame.get("height")),
            ts=_as_int(frame.get("ts")),
            owner=describe_frame_owner(source, frame.get("participantName")) or fallback_owner(source),
        )
        self._start_backstop()
        self.flush()

    def flush(self) -> None:
        """Deliver whatever is newest and unseen. Safe to call from anywhere that may have
        unblocked delivery (a frame arrived, the room came up, recording went active, the backstop
        ticked)."""
        if not self._config.enabled or self._released:
            return
        if self._flushing:
            # Delivery is async; a second pass over an unlatched frame would send it twice and pay
            # twice.
            self._dirty = True
            return
        self._flushing = True
        asyncio.ensure_future(self._flush_once())

    async def _flush_once(self) -> None:
        try:
            await self._run()
        except Exception as err:
            # Ambient vision is best-effort context: nothing about it may become an unhandled
            # exception that takes the call (or the process) down.
            self._log.debug(f"ambient vision: flush failed: {err}")
        finally:
            self._flushing = False
            if self._dirty:
                self._dirty = False
                self.flush()

    def release(self) -> None:
        """Drop the call's frames, queue and budget window, and stop the backstop."""
        self._released = True
        if self._backstop is not None:
            self._backstop.cancel()
            self._backstop = None
        self._latest.clear()
        self._last_pushed.clear()
        self._queued.clear()
        self._budget.release(self._call_id)

    def _start_backstop(self) -> None:
        if self._backstop is not None or self._released:
            return
        self._backstop = asyncio.ensure_future(self._backstop_loop())

    async def _backstop_loop(self) -> None:
        while not self._released:
            await asyncio.sleep(AMBIENT_VISION_BACKSTOP_MS / 1000)
            if self._released:
                return
            self.flush()

    def _enqueue(self, image: VisionImage) -> None:
        if not self._announced_queue_route:
            self._announced_queue_route = True
            # Announced ONCE per call, not once per frame: the delivery route is a real behavioural
            # difference (the agent sees these frames later than it otherwise would) and silence
            # about it is how such differences stay invisible for weeks.
            self._log.info(
                f"ambient vision: the agent room is not ready yet; "
                f"holding the newest {MAX_QUEUED_AMBIENT_IMAGES} frame(s) until it is"
            )
        self._queued.append(image)
        while len(self._queued) > MAX_QUEUED_AMBIENT_IMAGES:
            self._queued.pop(0)  # newest wins: ambient context is about NOW

    async def _run(self) -> None:
        if not self._media_permitted():
            return
        if self._sink_ready() and self._queued:
            # Already paid for at collection time; a drain failure drops them rather than refunding,
            # because by now they are stale and the live frames below are the ones worth spending on.
            pending = list(self._queued)
            self._queued.clear()
            for image in pending:
                try:
                    await self._deliver(image)
                except Exception as err:
                    self._log.debug(f"ambient vision: queued frame dropped: {err}")

        for source in VISION_SOURCE_ORDER:
            frame = self._latest.get(source)
            if frame is None:
                continue
            if frame.data_base64 == self._last_pushed.get(source):
                continue  # per-source change latch: a frozen screen costs nothing
            if not self._budget.try_consume(self._call_id, self._now()):
                break  # break, not continue: the budget is per call, not per source
            image = VisionImage(
                source=frame.source,
                mime=frame.mime,
                data_base64=frame.data_base64,
                width=frame.width,
                height=frame.height,
                ts=frame.ts,
                owner=frame.owner,
                caption=caption_for(frame.owner),
            )
            if not self._sink_ready():
                self._enqueue(image)
                self._last_pushed[source] = frame.data_base64
                continue
            try:
                await self._deliver(image)
            except Exception as err:
                # The spend never happened, and the frame must stay retryable: latching a failed
                # delivery loses the frame forever AND burns the budget slot.
                self._budget.refund(self._call_id)
                self._log.debug(f"ambient vision: delivery failed: {err}")
                continue
            # Latch only AFTER a successful delivery.
            self._last_pushed[source] = frame.data_base64
            self._log.debug(f"ambient vision: delivered {source} frame ({image.owner})")
