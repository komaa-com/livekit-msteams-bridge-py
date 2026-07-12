---
title: "Agents and Dispatch"
description: "How the bridge dispatches your LiveKit agent, the per-call metadata it passes, the teams.context and teams.goodbye data topics, and avatar agents."
---

The bridge is agnostic about what your agent does - any LiveKit agent (Python or Node, any STT/LLM/TTS/realtime stack) works unchanged. There are only three integration points: how it is **dispatched**, the **metadata** it receives, and two **data topics** it can listen on.

## Explicit dispatch

When `LIVEKIT_AGENT_NAME` is set, the bridge creates the per-call room and then creates an **explicit agent dispatch** for it via LiveKit's AgentDispatch service (`create_dispatch` - the [documented pattern](https://docs.livekit.io/agents/server/agent-dispatch)). Because the bridge creates a fresh room per call, your named agent is dispatched into that one room and no other.

Register the name on your worker:

```python
cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, agent_name="my-teams-agent"))
```

```bash
LIVEKIT_AGENT_NAME=my-teams-agent
```

Automatic dispatch (no name; an agent joins every room) still works for a quick prototype, but LiveKit recommends explicit dispatch for anything real - otherwise every room in your project pulls in the agent.

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

The bridge publishes two reliable data topics into the room. Subscribe to them if your agent should react to call context or the governor.

### `teams.context`

Non-interrupting context about the call, as `{ "text": "..." }`:

- Participant count changes - `"This is a 1:1 call with a single human caller."` or `"There are N human participants on this call. Stay quiet unless directly addressed."`
- DTMF - `"The caller pressed the \"5\" key on their keypad."`
- Recording state changes - `"The Microsoft Teams call recording is now ACTIVE."` (and the inverse), so the agent can disclose or adjust.

Feed these into your agent as system/context messages so it can adapt (for example, stay quiet in a group call until addressed).

### `teams.goodbye`

The governor's goodbye line, as `{ "text": "..." }`. When a call hits its time limit, the bridge asks the agent to speak this text, waits `GOODBYE_GRACE_MS`, then ends the call. There is **no bridge-side TTS** on the room transport - the agent speaks the goodbye. Have your handler interrupt the current turn so the goodbye actually plays:

```python
@ctx.room.on("data_received")
def on_data(packet):
    if packet.topic == "teams.goodbye":
        text = json.loads(packet.data)["text"]
        session.interrupt()               # stop the current turn
        session.say(text, allow_interruptions=False)
```

See [Governors and Privacy](/livekit-msteams-bridge-py/governors-and-privacy/) for the full governor behavior.

## How the bridge finds your agent's audio

The bridge binds "the agent" by participant **kind** (`PARTICIPANT_KIND_AGENT`): a monitor, recorder or debugging participant that happens to publish audio first can neither be mistaken for the agent nor block the agent's track. Only when the participant kind is unavailable (automatic-dispatch prototypes) does it fall back to first-audio-wins. Only the bound agent leaving ends the call.

## Avatar agents

Avatar agents ([bitHuman](https://github.com/livekit/agents/tree/main/examples/avatar_agents/bithuman), Tavus, and others) publish synchronized audio and video. The caller **hears the avatar's audio** - the bridge relays whichever remote track carries the agent's voice, including an avatar's republished audio (the audio pump re-arms when a track is unpublished and re-published).

Two things to know for v1:

- The avatar's **video** stays in the room. The Teams tile is rendered by StandIn's own animated avatar (RMS lip-sync), not the room video. Bridging room video to the Teams tile is on the roadmap.
- Avatar setups often run the avatar as a **separate participant** alongside the agent session. The bridge tracks the agent identity and only ends the call when *that* participant leaves, so a flapping avatar participant will not cut a healthy call short.

Ready-made examples (a minimal voice agent and a bitHuman avatar variant) live in [`examples/agents/`](https://github.com/komaa-com/livekit-msteams-bridge/tree/main/examples/agents) - they work with either the Node or the Python bridge.
