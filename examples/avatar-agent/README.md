# Video (avatar) agent for Teams calls

A ready-made agent the bridge can dispatch onto a Microsoft Teams call:
`worker.py` builds a speech pipeline from whatever the environment offers (see
[Model stack](#model-stack)) plus a
[bitHuman](https://github.com/livekit/agents/tree/main/examples/avatar_agents/bithuman)
avatar - the caller hears the avatar's synchronized voice. The avatar lip-syncs
whatever audio the session produces, so the pipeline tier is orthogonal to it.
For a voice-only agent with no avatar, see [`../voice-agent`](../voice-agent).

Any existing LiveKit agent works with the bridge unchanged except for three integration points, all shown in the example:

1. **`agent_name`** in `WorkerOptions` must equal the bridge's `LIVEKIT_AGENT_NAME` (explicit dispatch).
2. **`ctx.job.metadata`** carries per-call JSON from the bridge: `caller_name`, `tenant_id`, `call_direction`, and `user_id` (the caller's AAD id, present only when Teams provides it - use it for per-person memory).
3. **Data topics** (optional): `msteams.context` delivers group-call hints (participant counts, DTMF presses, recording state) and `msteams.goodbye` asks the agent to speak a final line because the call is being cut by a time governor. `msteams.vision` carries the caller's screen-share/camera frames when the bridge runs with `AMBIENT_VISION=true`; this worker handles it and folds each frame into the model's context for its next turn.

## Model stack

The worker picks one of three pipelines from what the environment actually offers, in this order:

| Tier | Trigger | Pipeline |
| --- | --- | --- |
| 1 | `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_REALTIME_DEPLOYMENT` | Azure speech-to-speech realtime (no VAD: Azure does turn detection server-side) |
| 2 | `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY` | Azure STT + LLM + TTS deployments + silero VAD |
| 3 | neither | `OPENAI_API_KEY` with `gpt-4o-mini` + silero VAD |

Realtime is preferred where it exists because it is speech-to-speech: it needs **no separate
`whisper` and `tts-1` deployments**, which an Azure OpenAI resource commonly does not have. Without
it the STT and TTS legs fail with `404 DeploymentNotFound` while the LLM leg works, which reads as
"the agent answers but never speaks".

| Env var | Default | Notes |
| --- | --- | --- |
| `AZURE_OPENAI_ENDPOINT` | - | General resource host, typically `https://<res>.openai.azure.com`. |
| `AZURE_OPENAI_API_KEY` | - | Also read automatically by every `with_azure()` call. |
| `AZURE_OPENAI_REALTIME_DEPLOYMENT` | - | Opt-in to tier 1. No default is guessable, and realtime bills differently from chat. |
| `AZURE_OPENAI_REALTIME_ENDPOINT` | `AZURE_OPENAI_ENDPOINT` | Realtime is served from `<res>.cognitiveservices.azure.com`, **not** `<res>.openai.azure.com`, which 404s the websocket handshake. |
| `AZURE_OPENAI_REALTIME_API_VERSION` | `AZURE_OPENAI_API_VERSION` | Selects the ROUTE, not just a header: with a version the SDK dials `/openai/realtime?api-version=..&deployment=..`, without one `/openai/v1/realtime`. |
| `AZURE_OPENAI_REALTIME_VOICE` | `cedar` | Not the SDK's `marin`: the `.imx` avatar this example ships is male, so a female default would be a defect. |
| `AZURE_OPENAI_API_VERSION` | - | Passed to the tier-2 STT/LLM/TTS constructors. |
| `AZURE_OPENAI_STT_DEPLOYMENT` | `whisper` | Tier-2 deployment names. The defaults mirror the plain-OpenAI model ids. |
| `AZURE_OPENAI_MODEL_NAME` | `gpt-4o-mini` | |
| `AZURE_OPENAI_TTS_DEPLOYMENT` | `tts-1` | |
| `AZURE_OPENAI_TTS_VOICE` | `alloy` | |
| `OPENAI_API_KEY` | - | Tier 3 only. |

## Run (uv, recommended)

```bash
cp .env.example .env                 # LIVEKIT_URL/KEY/SECRET, BITHUMAN_API_SECRET, BITHUMAN_MODEL_PATH + one model stack
uv lock --upgrade                    # refresh uv.lock (optional; a lock ships in the repo)
uv sync                              # install the environment
uv run python -m livekit.agents download-files      # prefetch model weights (silero VAD etc.)
uv run worker.py dev                 # hot-reloading dev mode; `start` for production
```

Prefer plain pip? `pip install -r requirements.txt && python worker.py dev` works too.

## Run (Docker)

`download-files` is baked at build time so cold starts are fast, and secrets are passed at RUNTIME (never into the image). The `.imx` avatar model is mounted at runtime, not baked in:

```bash
docker build -f Dockerfile -t standin-agent .
docker run --env-file .env \
  -v ./avatar.imx:/models/avatar.imx \
  -e BITHUMAN_MODEL_PATH=/models/avatar.imx \
  standin-agent
```

## The bitHuman runtime

The runtime is built in the entrypoint with `await AsyncBithuman.create(...)`, not in `prewarm`.
`AsyncBithuman` is an alias for `AsyncAvatar`, whose `__init__` accepts neither `model_path` nor
`load_model`, and the model only loads through that async factory - a synchronous `prewarm` cannot
await it, so constructing it there raised `TypeError` and every job process died at init.

The runtime is cached on `proc.userdata`, so the one-time `.imx` conversion (minutes, the first time
a model is seen) is paid once per worker process rather than once per call. That cache is a
re-entrancy guard rather than a cross-call amortiser: `AvatarSession.aclose()` calls
`runtime.cleanup()`, which nulls the fixture, and a warmed process serves exactly one job.
`prewarm` still validates `BITHUMAN_MODEL_PATH` and `BITHUMAN_API_SECRET`, so a missing value fails
at startup instead of after a caller is already connected.

## Connect to Teams

Run the bridge (see [`../basic-bridge`](../basic-bridge) , or `pip install livekit-msteams-bridge` and run the `livekit-msteams-bridge` command) with `LIVEKIT_AGENT_NAME=standin-agent`, point a StandIn identity at it, and call your Teams bot.

Swap the plugins freely - Azure/Google STT+TTS, a LangChain graph through `livekit-plugins-langchain`, an OpenAI Realtime session: the bridge only relays room audio and never sees your model stack.
