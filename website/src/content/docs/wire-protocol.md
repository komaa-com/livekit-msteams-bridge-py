---
title: "Wire Protocol"
description: "The exact contract on the worker socket: the HMAC upgrade, connection guards, every message the bridge relays, and the room-side mapping."
---

The bridge terminates the StandIn media bridge's worker protocol on one side and maps it onto a LiveKit room on the other. This page documents both. The contract is identical to the Node.js sibling - the two implementations are interchangeable.

## The upgrade (StandIn side)

The StandIn media bridge opens one WebSocket per call to `{path}/{callId}` - the **call id is the last path segment** of the URL. The upgrade carries two headers:

| Header | Value |
|---|---|
| `X-OpenClawTeamsBridge-Timestamp` | Unix epoch milliseconds |
| `X-OpenClawTeamsBridge-Signature` | `HMAC-SHA256(secret, "{timestampMs}.{callId}")`, lowercase hex |

Verification (`401` on failure): the timestamp must be within the two-sided freshness window (`HMAC_FRESHNESS_MS`, default 60 s past or future), the signature must match (constant-time compare), and the `(callId, ts, sig)` tuple must be **single-use** (a captured handshake cannot be replayed within the window). The bridge fails closed if the shared secret is unset. The call id is also cross-checked against the `session.start` body.

## Connection guards

| Guard | Value |
|---|---|
| Max concurrent connections | 64 (`MAX_CONNECTIONS`) |
| Per-IP cap | = total cap (`MAX_CONNECTIONS_PER_IP`) |
| Max inbound frame | 2 MB |
| Outbound backpressure cap | 1 MB (drops realtime frames above it; control frames always pass) |
| Pre-start timeout | 10 s (`PRE_START_TIMEOUT_MS`) - drops a socket that never sends `session.start` (only a real `session.start` clears it) |
| Worker idle timeout | 90 s (`WORKER_IDLE_TIMEOUT_MS`) - dead-peer detection, plus a 30 s WS-level heartbeat that catches dead peers earlier |
| Duplicate call id | rejected with `409` - no second billed agent job for one call |

Audio on the wire is base64 **PCM 16 kHz, 16-bit, mono**.

## Worker to bridge

| Message | Fields | Bridge action |
|---|---|---|
| `session.start` | `callId`, `threadId`, `caller{aadId?, displayName?, tenantId?}`, `recordingStatus?`, `direction?` | Create the room, dispatch the agent with caller metadata, publish the caller-audio track. All caller fields are nullable and are defaulted, never sent as null. |
| `audio.frame` | `seq`, `timestampMs`, `payloadBase64`, `speakerName?` | Publish the PCM into the room via `AudioSource.capture_frame`. Buffered (bounded, ~5 s) while the room is still connecting. |
| `video.frame` | `source`, `ts`, `width`, `height`, `mime`, `dataBase64`, ... | Ignored in v1 (the Teams tile is rendered by the worker's own avatar; publishing caller video into the room is on the roadmap). |
| `participants` | `count` | `teams.context` data message ("1:1 call" / "N humans, stay quiet unless addressed"). A count of 0 says nothing. |
| `dtmf` | `digit` | `teams.context` data message ("the caller pressed {digit}"). |
| `ping` | `ts` | Reply `pong` with the same `ts`. |
| `recording.status` | `status` | On state change, a `teams.context` message ("recording is now ACTIVE" / "not active") so the agent can disclose. |
| `assistant.say` | `text` | Governor goodbye: forwarded to the agent on `teams.goodbye` (empty text falls back to `GOODBYE_TEXT`), then StandIn tears the call down. |
| `session.end` | `reason` | Leave + delete the room, tear down. |

## Bridge to worker

| Message | Fields | Meaning |
|---|---|---|
| `audio.frame` | `seq`, `timestampMs`, `payloadBase64` | Agent audio for the Teams side (pumped from the agent's room track, resampled to 16 kHz by the SDK). |
| `assistant.cancel` | `turnId` | Sent when a goodbye begins, so buffered Teams-side playback is flushed. `turnId` is always `0` - the bridge does not track worker turn ids and the worker's flush ignores the value. |
| `pong` | `ts` | Reply to a worker `ping`. |
| `session.end` | `reason` | Ask StandIn to tear the call down (governor, agent left, or fatal error). |

## Room-side mapping

| Room event / API | Bridge behavior |
|---|---|
| room create + `create_dispatch` | One fresh room per call; the named agent is dispatched with the per-call metadata JSON. |
| `AudioSource.capture_frame` | Caller audio into the room (the published "teams-caller" microphone track). |
| `track_subscribed` (audio) | Bind the agent by participant kind (`PARTICIPANT_KIND_AGENT`), start the audio pump at 16 kHz mono. Non-agent publishers are ignored. |
| `track_unsubscribed` | Re-arm the pump so a re-published agent track (avatar swaps) keeps playing. |
| `participant_disconnected` | Only the bound agent leaving ends the call. |
| `disconnected` | Final (the SDK retries transient drops internally first): end the call. |
| `publish_data` (`teams.context` / `teams.goodbye`) | Reliable data messages carrying `{"text": "..."}`. |
| `delete_room` at teardown | Ends the agent job immediately (`LIVEKIT_DELETE_ROOM_ON_END=true`). |
