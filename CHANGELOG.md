# Changelog

## 0.4.0

- **`LIVEKIT_TILE_VIDEO` now defaults to `auto`** (was `off`). Avatar agents show
  their face on the Teams tile by default; set `off` to opt out. Voice-only
  agents are unaffected (no avatar video to relay). Reverses the earlier opt-in
  default per project decision. README + `.env.example` updated.
- Document the deliberate Node/Python pacing-mechanism parity in `video_relay.py`
  (monotonic self-cap vs fixed ticker; behaviourally equivalent).

## 0.3.0

- **Avatar video relay graduated to a documented opt-in.** The
  `LIVEKIT_TILE_VIDEO` relay (avatar agent's video onto the Teams tile) is now
  verified end to end and documented in the README/`.env.example`; the config
  docstring drops "EXPERIMENTAL". Default stays `off`.
- `LIVEKIT_TILE_VIDEO_FPS` default raised 10 -> 15 to match the Teams tile rate
  (10 starved the tile and stuttered). Only affects users who enable the relay.

## 0.2.1

- fix(avatar-relay): subscribe to the avatar worker's video track directly
  instead of filtering by `SOURCE_CAMERA`. Virtual-avatar workers (bitHuman,
  etc.) publish their video untagged — it arrives as `SOURCE_UNKNOWN`, not
  `SOURCE_CAMERA` — so the source filter matched no track and relayed zero
  frames. Now takes the participant's video publication and uses
  `rtc.VideoStream(track)`, matching LiveKit's docs.

## 0.1.0 (unreleased)

Initial release: Python port of `@komaa/livekit-msteams-bridge` (Node.js).

- Same wire contract and environment variables as the Node package - the two
  are drop-in interchangeable behind one `.env` file.
- Per-call LiveKit rooms, explicit agent dispatch with per-call metadata,
  16 kHz PCM relay via the SDK's resampling AudioSource/AudioStream,
  `teams.context` / `teams.goodbye` data topics, call governor, HMAC-signed
  upgrades with replay guard, connection caps, dead-peer detection,
  Prometheus `/metrics`, graceful drain.

### Stability notes

- The worker wire protocol (message types, HMAC scheme) tracks the StandIn
  media bridge contract and is stable.
- The data-topic payload shape is `{"text": "..."}` on both `teams.context`
  and `teams.goodbye`; treat additions as backwards-compatible.
- Tested against `livekit` 1.1.x / `livekit-api` 1.2.x (pinned `<2`); the SDK
  surface used is asserted by `tests/test_livekit_sdk_surface.py`.
