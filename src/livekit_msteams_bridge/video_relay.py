"""EXPERIMENTAL: relay the agent avatar's published video track onto the Teams
tile as a continuous display.frame stream. Off by default (LIVEKIT_TILE_VIDEO).

Selection: the participant that publishes the audio we already relay also
publishes the avatar video (LiveKit's avatar framework runs both on one
participant and tags it lk.publish_on_behalf). We subscribe THAT participant's
camera track.

Delivery: livekit-rtc's VideoStream supports capacity=1 (a ring that drops the
OLDEST frame on overflow) and FFI-side RGB conversion, so latest-wins comes
from the SDK - iterate the stream and rate-limit the send. Frames are dropped,
never queued, under worker backpressure. seq is monotonic; ts is the sender
media-timeline ms (shared with outbound audio.frame) so the worker/consumer
can measure A/V skew.
"""

from __future__ import annotations

import asyncio
import base64
import io
import time
from typing import Any, Callable, Protocol

from livekit import rtc

from .log import Logger
from .metrics import metric_inc

TILE_W = 640
TILE_H = 360
JPEG_QUALITY = 58
# Separate, tighter than the 1 MB audio cap: video must fall back to the
# rendered avatar promptly rather than build up seconds of skew.
VIDEO_BACKPRESSURE_BYTES = 320 * 1024
PUBLISH_ON_BEHALF = "lk.publish_on_behalf"


class TileSink(Protocol):
    """What the relay needs to push a frame + read backpressure."""

    def is_open(self) -> bool: ...

    def buffered_bytes(self) -> int: ...

    def now_media_ms(self) -> int:
        """The sender's AUDIO media timeline, in ms (the same clock outbound
        audio.frame.timestampMs rides). Video ts MUST come from this clock - a
        wall clock keeps ticking through listening silence while the audio
        clock does not, which would confound the A/V skew measurement with
        clock drift."""
        ...

    def send_frame(self, seq: int, ts: int, data_base64: str, width: int, height: int) -> None: ...


def load_jpeg_encoder(log: Logger) -> Callable[[bytes, int, int], bytes] | None:
    """Load Pillow lazily as an OPTIONAL dependency: the package keeps its core
    dependencies lean for the non-avatar majority; avatar users install the
    [avatar] extra. Returns None (with one warn) when it is not installed."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        log.warn(
            "LIVEKIT_TILE_VIDEO is on but Pillow is not installed; avatar video relay disabled "
            "(pip install 'livekit-msteams-bridge[avatar]' to enable). "
            "Audio and everything else are unaffected."
        )
        return None

    def encode(rgb: bytes, width: int, height: int) -> bytes:
        # Downscale to the tile before encoding: the worker scales to its tile
        # regardless, so shipping native avatar resolution only wastes bandwidth.
        img = Image.frombuffer("RGB", (width, height), rgb, "raw", "RGB", 0, 1)
        if (width, height) != (TILE_W, TILE_H):
            img = img.resize((TILE_W, TILE_H))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=JPEG_QUALITY)
        return out.getvalue()

    return encode


def select_participant(room: rtc.Room, mode: str, agent_identity: str | None) -> Any:
    """Pick the participant whose avatar video to relay, honoring the config:
    off        -> never called; the caller gates on this
    auto       -> the agent identity (the one we relay audio from), verified by
                  lk.publish_on_behalf when present
    <identity> -> that exact identity
    """
    remotes = list(room.remote_participants.values())
    if mode != "auto":
        return next((p for p in remotes if p.identity == mode), None)
    # auto: prefer a participant that declares it publishes on behalf of the
    # agent (the avatar), else the agent identity itself.
    if agent_identity:
        by_behalf = next(
            (p for p in remotes if (p.attributes or {}).get(PUBLISH_ON_BEHALF) == agent_identity), None
        )
        if by_behalf is not None:
            return by_behalf
        return next((p for p in remotes if p.identity == agent_identity), None)
    return None


def start_video_relay(
    tile_video: str,
    tile_video_fps: float,
    log: Logger,
    room: rtc.Room,
    get_agent_identity: Callable[[], str | None],
    sink: TileSink,
) -> Callable[[], None]:
    """Wire the video relay onto a room. Returns a stop() to unwire on teardown.
    The caller supplies the agent-identity resolver (bound by the room port's
    kind-based/first-audio logic) and the TileSink (its session's send path)."""
    encode = load_jpeg_encoder(log)
    if encode is None:
        return lambda: None  # Pillow missing: no-op, already warned

    period_s = 1.0 / max(1.0, min(tile_video_fps, 20.0))
    state: dict[str, Any] = {"stopped": False, "seq": 0, "task": None, "identity": None}
    loop = asyncio.get_running_loop()

    def drain_participant(participant: Any) -> None:
        """One drain task per selected participant; a re-selection replaces it."""
        cancel_active()
        state["identity"] = participant.identity
        log.info(f'avatar video relay: draining camera track from "{participant.identity}"')

        async def drain() -> None:
            # capacity=1: the SDK ring drops the oldest frame, so this loop can
            # rate-limit + encode + send inline and latest-wins still holds.
            stream = rtc.VideoStream.from_participant(
                participant=participant,
                track_source=rtc.TrackSource.SOURCE_CAMERA,
                format=rtc.VideoBufferType.RGB24,
                capacity=1,
            )
            last_send = 0.0
            try:
                async for ev in stream:
                    if state["stopped"]:
                        break
                    now = time.monotonic()
                    if now - last_send < period_s:
                        continue  # self-cap at the configured fps
                    if not sink.is_open():
                        continue
                    if sink.buffered_bytes() > VIDEO_BACKPRESSURE_BYTES:
                        metric_inc("bridge_video_frames_dropped_total")
                        continue
                    frame = ev.frame
                    rgb = bytes(frame.data)
                    # JPEG encode off the event loop: the audio pump must not
                    # stall behind a video encode.
                    jpeg = await loop.run_in_executor(None, encode, rgb, frame.width, frame.height)
                    if state["stopped"] or not sink.is_open():
                        break
                    last_send = now
                    # ts rides the sender's AUDIO media timeline (not wall
                    # clock): skew is only measurable if both streams share one
                    # clock (design doc §6).
                    seq = state["seq"]
                    state["seq"] = seq + 1
                    sink.send_frame(
                        seq, sink.now_media_ms(), base64.b64encode(jpeg).decode("ascii"), TILE_W, TILE_H
                    )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                if not state["stopped"]:
                    log.warn(f"avatar video stream ended: {err}")
            finally:
                await stream.aclose()

        state["task"] = asyncio.ensure_future(drain())

    def cancel_active() -> None:
        task = state["task"]
        state["task"] = None
        state["identity"] = None
        if task is not None and not task.done():
            task.cancel()

    def try_start_from_existing() -> None:
        """Start draining the selected participant, if identified. Subscribe
        ordering is unspecified: the avatar's video can subscribe BEFORE its
        audio binds the agent identity, so this rescans whenever the identity
        may have just bound (from_participant picks up an already-subscribed
        camera track itself)."""
        if state["stopped"] or state["identity"] is not None:
            return
        chosen = select_participant(room, tile_video, get_agent_identity())
        if chosen is None:
            return
        has_video = any(
            int(pub.kind) == int(rtc.TrackKind.KIND_VIDEO) for pub in chosen.track_publications.values()
        )
        if has_video:
            drain_participant(chosen)

    def on_track_subscribed(track: rtc.Track, publication: Any, participant: Any) -> None:
        if state["stopped"] or state["identity"] is not None:
            return
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            # The audio subscribe is what binds the agent identity (the room
            # port's handler registered before this one, so it has run). The
            # selected participant's video may already be up - pick it up now.
            try_start_from_existing()
            return
        if track.kind != rtc.TrackKind.KIND_VIDEO:
            return
        chosen = select_participant(room, tile_video, get_agent_identity())
        if chosen is not None and chosen.identity == participant.identity:
            drain_participant(chosen)

    def on_track_unsubscribed(track: rtc.Track, publication: Any, participant: Any) -> None:
        if participant.identity == state["identity"]:
            # from_participant keeps waiting for a re-publish; a swap restarts
            # cleanly via the next subscribe event instead.
            cancel_active()

    room.on("track_subscribed", on_track_subscribed)
    room.on("track_unsubscribed", on_track_unsubscribed)
    # The relay may be armed after tracks already subscribed: scan once at start.
    try_start_from_existing()

    log.info(f"avatar video relay armed (mode={tile_video}, {tile_video_fps:g} fps, tile {TILE_W}x{TILE_H})")

    def stop() -> None:
        state["stopped"] = True
        try:
            room.off("track_subscribed", on_track_subscribed)
            room.off("track_unsubscribed", on_track_unsubscribed)
        except Exception:
            pass  # room may already be closed
        cancel_active()

    return stop
