---
title: "Governors and Privacy"
description: "The two call governors, why there is no bridge-side TTS on the room transport, and what data the bridge does and does not handle."
---

## Two governors

A call can be cut off from two places:

1. **StandIn-side** - StandIn enforces the caller's tier limits and, at cutoff, asks the bridge to wind the call down. The bridge forwards the goodbye request to the agent on `msteams.goodbye`.
2. **Bridge-side (`MAX_CALL_MINUTES`)** - an independent hard cap you set on the bridge. LiveKit doesn't know about your billing, so this is your own backstop against a call that runs forever.

Both funnel through the same goodbye path; the first to fire wins, and a duplicate goodbye is ignored. Governor fires and goodbye requests are counted at `GET /metrics` (`bridge_governor_time_limit_total`, `bridge_goodbyes_requested_total`).

## How the goodbye works

There is **no bridge-side TTS** on the room transport - the bridge cannot synthesize speech into the room. So the governor's goodbye is a `msteams.goodbye` **data message** carrying the text; **your agent speaks it**. The sequence on `MAX_CALL_MINUTES`:

1. The bridge flushes Teams-side buffered playback (`assistant.cancel`) so stale audio cannot eat the grace window.
2. It sends `msteams.goodbye` to the agent (an empty goodbye falls back to `GOODBYE_TEXT`).
3. It waits `GOODBYE_GRACE_MS` (plus a fixed 500 ms scheduling buffer), then ends the call with reason `time-limit`.

Because the bridge can't know your agent's real speech duration, `GOODBYE_GRACE_MS` (default 8 s) is the budget. If the agent's current turn outlasts the grace, the goodbye can be cut - so have your `msteams.goodbye` handler **interrupt the current turn** and speak the line with interruptions disabled (see [Agents and Dispatch](/livekit-msteams-bridge-py/agents-and-dispatch/)).

## Ambient vision, and its spend cap

`AMBIENT_VISION` is the third governed thing on this bridge, and the only one that costs money per frame - so it is **off by default**. When it is on:

- **Nothing is stored before Teams reports the recording as active** (`REQUIRE_RECORDING_STATUS`, default `true`). The gate blocks *capture*, not just delivery, so a frame taken before the gate opened cannot surface afterwards - it was never kept.
- **Only CHANGED frames are sent.** A per-source latch means a frozen screen costs nothing, and it is set only after a delivery actually succeeded.
- **`MAX_VISION_PER_MINUTE` (default 30) is a sliding 60 s cap per call.** `0` disables. Screen-share is tried before camera, and exhausting the budget stops the pass, so a tight budget spends its last slot on the screen rather than a talking head. A failed publish refunds the slot and leaves the frame retryable.
- Frames are relayed **verbatim**: the bridge never transcodes, and it never asks the agent to speak about them. Delivered images are counted at `GET /metrics` as `bridge_vision_frames_sent_total`.

## Privacy and data handling

- **No recording, no persistence.** This bridge stores nothing. Audio is relayed frame-by-frame between the worker socket and the room and never written to disk. Ambient-vision frames are held only as the newest image per source for the life of the call and dropped at teardown. `recording.status` is logged and surfaced to the agent as context only.
- **Caller identity is minimal and defaulted.** The agent receives `caller_name`, `tenant_id`, `call_direction`, and `user_id` (AAD id) - and `user_id` is included *only* when Teams provides one, so it is never a shared placeholder. Nullable fields default to safe strings, never null.
- **Metrics carry no call content.** `GET /metrics` exposes counters (calls, durations, rejects, relay/drop counts) only - never audio, text, or identities.
- **The room is deleted at teardown** (`LIVEKIT_DELETE_ROOM_ON_END=true`), so the agent job ends immediately and no room lingers with call context.
- Whatever your **agent and LiveKit** retain (recordings, transcripts, logs) follows their own settings - review those separately.

## Hardening summary

The transport carries the same protections as the sibling bridges: replay-proof single-use HMAC upgrade, connection caps checked before crypto, payload caps, a pre-start timeout, WS heartbeat + dead-peer detection, duplicate-call `409`, a pre-auth crash guard, and a graceful SIGTERM drain. See [Architecture](/livekit-msteams-bridge-py/architecture/) for how each fits into the call flow.
