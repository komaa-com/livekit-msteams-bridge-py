---
title: "Troubleshooting"
description: "The errors you will actually see on the upgrade, on the call, and at startup, and what each one means."
---

## `401` on the upgrade

The HMAC handshake failed. Causes:

- **Secret mismatch** - `WORKER_SHARED_SECRET` does not equal the value StandIn holds from pairing. They must match exactly.
- **Clock skew** - the timestamp is outside the freshness window (`HMAC_FRESHNESS_MS`, default 60 s either side). Sync the clocks (NTP).
- **Replayed handshake** - the same `(callId, ts, sig)` tuple was already used. This is the single-use guard doing its job; a genuine retry uses a fresh timestamp.
- **Secret unset** - the bridge fails closed if `WORKER_SHARED_SECRET` is empty; every upgrade is rejected.

## `409` Conflict

A live session already owns that call id (a retry or rollout reconnect). The bridge rejects the duplicate so it does not create a second room + billed agent job for one call. It clears when the first session tears down (a dead peer is detected by the heartbeat/idle watchdog).

## `503` Service Unavailable

A connection cap was hit: `MAX_CONNECTIONS` (default 64) or `MAX_CONNECTIONS_PER_IP`. Raise them for a busier deployment, or check for a client that is not closing sockets.

## Call connects, then `agent-unavailable`

The bridge could not create/join the LiveKit room (it retries once before giving up). Check `LIVEKIT_URL`, that the API key/secret belong to that project, and the bridge host's network path to LiveKit.

## The room is created but the caller hears nothing

The classic dispatch mismatch: `LIVEKIT_AGENT_NAME` does not equal the `agent_name` your worker registered with, the worker is not running, or it is registered against a **different LiveKit project**. The bridge logs `agent "<name>" dispatched` - if your worker never logs a job for it, the name or project is wrong. Also check the worker machine's logs for crashed jobs (a missing `OPENAI_API_KEY` in the example agent, for instance).

## The worker won't start, or the first call takes minutes to answer

Large models - avatar runtimes (bitHuman, Tavus), local STT/TTS, turn detectors - take real time to load, and that trips two setup problems worth calling out because the symptom looks like the wire path is broken when it isn't:

- **The worker exits at startup with `TimeoutError` / "error initializing process".** The model load overran the process-init deadline. Raise it: `WorkerOptions(..., initialize_process_timeout=300)`. A bitHuman `.imx` model converting for the first time can take a couple of minutes.
- **The first call takes minutes to answer; later calls are instant.** A cold job process loads the model on demand. Two fixes, use both: load the model in your `prewarm` function and stash it in `proc.userdata` (so the entrypoint reuses it), and keep a process warm with `num_idle_processes >= 1` so a dispatch never waits on a cold load. Run `python your_agent.py download-files` once first to prefetch downloadable weights (silero VAD, the turn detector).

If a call connects and dispatches but the agent then sits silent for a long time before speaking, this - not the handshake or the room - is almost always the cause.

## The agent's goodbye gets cut off

`teams.goodbye` handlers must interrupt the current turn and speak with interruptions disabled; otherwise an in-flight answer can outlast `GOODBYE_GRACE_MS`. See the handler snippet in [Agents and Dispatch](/livekit-msteams-bridge-py/agents-and-dispatch/).

## Governor never fires

`MAX_CALL_MINUTES` must be a number. A non-numeric or negative value stops startup with a clear error (numeric env vars fail loud), so if the process started, the value parsed. Confirm it is greater than `0` (`0` disables the governor).

## A monitor participant joined and the call ended / went quiet

It should not: the bridge binds "the agent" by participant kind and ignores other publishers, and only the bound agent leaving ends the call. If you see otherwise on a self-hosted server, check that your LiveKit version reports agent participants with the agent kind.

## Port already in use

The CLI prints a friendly hint on the bind error. Set `PORT` to a free port.

## Where the logs are

The bridge logs one line per event to stdout/stderr, scoped by call id. Set `LOG_LEVEL=debug` for the verbose relay detail (an invalid value falls back to `info`).
