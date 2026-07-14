"""Minimal LiveKit voice agent that answers Microsoft Teams calls via
livekit-msteams-bridge (the Python bridge).

Nothing here is Teams-specific except three small things the bridge gives you:
  1. agent_name        - the bridge dispatches you by this name (explicit dispatch)
  2. ctx.job.metadata  - per-call JSON: caller_name, tenant_id, call_direction,
                         user_id (caller AAD id, present only when Teams knows it)
  3. data topics       - "teams.context" (participants/DTMF hints) and
                         "teams.goodbye" (speak this line, the call is ending)

Swap the STT/LLM/TTS plugins for any stack you like (Azure, Google, Deepgram,
OpenAI realtime, a LangChain graph via livekit-plugins-langchain, ...) - the
bridge does not care, it only relays room audio.

Run:  python worker.py dev
Env:  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, OPENAI_API_KEY
"""

import json

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    WorkerOptions,
    WorkerType,
    cli,
)
from livekit.plugins import openai, silero

load_dotenv()

AGENT_NAME = "standin-voice-agent"  # must equal the bridge's LIVEKIT_AGENT_NAME


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Per-call context from the bridge (all fields are strings; user_id may be absent)
    meta = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    caller_name = meta.get("caller_name", "caller")

    session = AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(voice="alloy"),
        vad=ctx.proc.userdata["vad"],
    )

    # Bridge data topics: group-call context and the governor goodbye
    @ctx.room.on("data_received")
    def on_data(packet: rtc.DataPacket):
        try:
            payload = json.loads(packet.data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if packet.topic == "teams.context":
            # non-interrupting hints ("3 humans on the call", "caller pressed 5");
            # use them however fits your agent (steer behavior, log, drive an IVR)
            print(f"[teams.context] {payload.get('text', '')}")
        elif packet.topic == "teams.goodbye":
            # the call is being cut (time limit): say this line now
            session.say(payload.get("text", "Goodbye!"), allow_interruptions=False)

    await session.start(
        agent=Agent(
            instructions=(
                f"You are a helpful voice assistant on a Microsoft Teams call with {caller_name}. "
                "Keep answers short and conversational; the caller hears you, they do not read you."
            ),
        ),
        room=ctx.room,
    )

    await session.generate_reply(
        instructions=f"Greet {caller_name} briefly and ask how you can help. Under 25 words.",
        allow_interruptions=False,
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            worker_type=WorkerType.ROOM,
            agent_name=AGENT_NAME,
        ),
    )
