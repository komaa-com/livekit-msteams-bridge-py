---
title: "Agents and Dispatch"
description: "How the bridge dispatches your LiveKit agent, the per-call metadata it passes, the msteams.context, msteams.goodbye and msteams.vision data topics, and avatar agents."
---

The bridge is agnostic about what your agent does - any LiveKit agent (Python or Node, any STT/LLM/TTS/realtime stack) works unchanged. There are only three integration points: how it is **dispatched**, the **metadata** it receives, and two **data topics** it can listen on.

## Explicit dispatch

When `LIVEKIT_AGENT_NAME` is set, the bridge creates the per-call room and then creates an **explicit agent dispatch** for it via LiveKit's AgentDispatch service (`create_dispatch` - the [documented pattern](https://docs.livekit.io/agents/server/agent-dispatch)). Because the bridge creates a fresh room per call, your named agent is dispatched into that one room and no other.

Register the name on your worker:

```python
cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="standin-agent"))
```

```bash
LIVEKIT_AGENT_NAME=standin-agent
```

**The names must match, or the agent never joins.** A worker registered with `agent_name` is reachable *only* by explicit dispatch. If you set a name on the worker but leave `LIVEKIT_AGENT_NAME` unset, the bridge falls back to automatic dispatch, the named worker ignores it, and the call sits silent with no agent - the single most common setup mistake. Set `LIVEKIT_AGENT_NAME` to the worker's exact `agent_name`.

Automatic dispatch (no name on either side; the agent joins every room) still works for a quick prototype, but LiveKit recommends explicit dispatch for anything real - otherwise every room in your project pulls in the agent.

## Per-call metadata

The dispatch carries JSON metadata, available in the agent's job context (`ctx.job.metadata` in Python):

```json
{
  "source": "msteams",
  "caller_name": "Jane Caller",
  "tenant_id": "<tenant guid>",
  "call_direction": "inbound",
  "user_id": "<AAD object id, only when Teams provides one>"
}
```

Nullable Teams fields are defaulted, never null: `caller_name` falls back to `"caller"`, `tenant_id` to `"unknown-tenant"`. `user_id` is included **only** when Teams supplies an AAD id, so it is per-person and never a shared placeholder - safe to use as a personalization or lookup key.

```python
async def entrypoint(ctx: JobContext):
    meta = json.loads(ctx.job.metadata or "{}")
    greeting = f"Hello {meta.get('caller_name', 'there')}, you're calling from Teams."
    # ... build your AgentSession as usual
```

## Data topics

The bridge publishes two reliable data topics into the room, plus one opt-in byte stream. Subscribe to them if your agent should react to call context, the governor, or what the caller is showing.

### `msteams.context`

Non-interrupting context about the call, as `{ "text": "..." }`:

- Participant count changes - `"This is a 1:1 call with a single human caller."` or `"There are N human participants on this call. Stay quiet unless directly addressed."`
- DTMF - `"The caller pressed the \"5\" key on their keypad."`
- Recording state changes - `"The Microsoft Teams call recording is now ACTIVE."` (and the inverse), so the agent can disclose or adjust.

Feed these into your agent as system/context messages so it can adapt (for example, stay quiet in a group call until addressed).

### `msteams.goodbye`

The governor's goodbye line, as `{ "text": "..." }`. When a call hits its time limit, the bridge asks the agent to speak this text, waits `GOODBYE_GRACE_MS`, then ends the call. There is **no bridge-side TTS** on the room transport - the agent speaks the goodbye. Have your handler interrupt the current turn so the goodbye actually plays:

```python
@ctx.room.on("data_received")
def on_data(packet):
    if packet.topic == "msteams.goodbye":
        text = json.loads(packet.data)["text"]
        session.interrupt()               # stop the current turn
        session.say(text, allow_interruptions=False)
```

### `msteams.vision`

A **byte stream**, not a data packet: a screen-share JPEG is far larger than a LiveKit data packet may be. Published only when the bridge runs with `AMBIENT_VISION=true`, one stream per image, named `{source}-{ts}`. The attributes carry the attribution, so a handler never has to parse the image to know whose screen it is: `source` (`screenshare` or `camera`), `owner` (`"Sara's shared screen"`, degrading to `"a shared screen"` when Teams did not name the participant), `caption`, `width`, `height`, `ts`.

```python
def on_vision(reader: rtc.ByteStreamReader, participant_identity: str):
    async def consume():
        attrs = reader.info.attributes or {}
        data = b"".join([chunk async for chunk in reader])
        chat_ctx = session.current_agent.chat_ctx.copy()
        chat_ctx.add_message(role="user", content=[
            attrs.get("caption", "Live frame of the call."),
            ImageContent(image=f"data:{reader.info.mime_type};base64,{base64.b64encode(data).decode()}"),
        ])
        await session.current_agent.update_chat_ctx(chat_ctx)

    asyncio.create_task(consume())

# BEFORE session.start: a stream whose topic has no handler is dropped, permanently.
ctx.room.register_byte_stream_handler("msteams.vision", on_vision)
```

The frame is context for the agent's **next** turn - nothing here should make it speak. The bridge sends only CHANGED frames and caps the rate per call, so this stays cheap; see [Governors and Privacy](/livekit-msteams-bridge-py/governors-and-privacy/) for the recording gate and the spend cap.

See [Governors and Privacy](/livekit-msteams-bridge-py/governors-and-privacy/) for the full governor behavior.

## How the bridge finds your agent's audio

The bridge binds "the agent" by participant **kind** (`PARTICIPANT_KIND_AGENT`): a monitor, recorder or debugging participant that happens to publish audio first can neither be mistaken for the agent nor block the agent's track. Only when the participant kind is unavailable (automatic-dispatch prototypes) does it fall back to first-audio-wins. Only the bound agent leaving ends the call.

## Avatar agents

Avatar agents ([bitHuman](https://github.com/livekit/agents/tree/main/examples/avatar_agents/bithuman), Tavus, and others) publish synchronized audio and video. The caller **hears the avatar's audio** - the bridge relays whichever remote track carries the agent's voice, including an avatar's republished audio (the audio pump re-arms when a track is unpublished and re-published).

Two things to know for v1:

- The avatar's **video** stays in the room. The Teams tile is rendered by StandIn's own animated avatar (RMS lip-sync), not the room video. Bridging room video to the Teams tile is on the roadmap.
- Avatar setups often run the avatar as a **separate participant** alongside the agent session. The bridge tracks the agent identity and only ends the call when *that* participant leaves, so a flapping avatar participant will not cut a healthy call short.

Ready-made examples live in this repository: [`examples/voice-agent`](https://github.com/komaa-com/livekit-msteams-bridge-py/tree/main/examples/voice-agent) and [`examples/avatar-agent`](https://github.com/komaa-com/livekit-msteams-bridge-py/tree/main/examples/avatar-agent). Both register as `standin-agent` and work with either the Node or the Python bridge.
