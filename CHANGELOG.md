# Changelog

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
