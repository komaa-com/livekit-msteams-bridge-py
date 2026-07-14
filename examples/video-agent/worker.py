"""LiveKit avatar agent (bitHuman) answering Microsoft Teams calls via
livekit-msteams-bridge (the Python bridge).

A minimal voice pipeline (OpenAI STT/LLM/TTS + silero VAD) plus a bitHuman
AvatarSession, following LiveKit's avatar example
(https://github.com/livekit/agents/tree/main/examples/avatar_agents/bithuman).
The Teams caller HEARS the avatar's synchronized voice; the avatar's video
stays in the LiveKit room in v1 (the Teams video tile is rendered by the
StandIn media bridge's own animated avatar).

Run:  python worker.py dev
Env:  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, OPENAI_API_KEY,
      BITHUMAN_API_SECRET, BITHUMAN_MODEL_PATH (an .imx avatar model)
"""

import json
import os

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
from bithuman import AsyncBithuman
from livekit.plugins import bithuman, openai, silero

load_dotenv()

AGENT_NAME = "standin-avatar-agent"  # must equal the bridge's LIVEKIT_AGENT_NAME


def prewarm(proc: JobProcess):
    # Load BOTH the VAD and the bitHuman avatar model here, in the prewarmed
    # process, so the first call doesn't pay a cold model load (an .imx converts
    # in ~2 min the first time). The entrypoint reuses this runtime. Combined with
    # num_idle_processes below, a dispatch never waits on a model load.
    proc.userdata["vad"] = silero.VAD.load()
    proc.userdata["bithuman"] = AsyncBithuman(
        model_path=os.environ["BITHUMAN_MODEL_PATH"],
        api_secret=os.environ["BITHUMAN_API_SECRET"],
        load_model=True,
    )


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    meta = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    caller_name = meta.get("caller_name", "caller")

    session = AgentSession(
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(voice="alloy"),
        vad=ctx.proc.userdata["vad"],
    )

    # the avatar runtime lip-syncs the session's TTS and publishes
    # synchronized audio+video into the room; the bridge relays the audio to Teams.
    # Reuse the runtime prewarmed above so this call starts instantly.
    avatar = bithuman.AvatarSession(
        model_path=os.environ["BITHUMAN_MODEL_PATH"],
        api_secret=os.environ["BITHUMAN_API_SECRET"],
        runtime=ctx.proc.userdata["bithuman"],
    )
    await avatar.start(session, room=ctx.room)

    @ctx.room.on("data_received")
    def on_data(packet: rtc.DataPacket):
        try:
            payload = json.loads(packet.data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if packet.topic == "teams.goodbye":
            session.say(payload.get("text", "Goodbye!"), allow_interruptions=False)

    await session.start(
        agent=Agent(
            instructions=(
                f"You are a friendly avatar assistant on a Microsoft Teams call with {caller_name}. "
                "Keep answers short and natural."
            ),
        ),
        room=ctx.room,
    )

    await session.generate_reply(
        instructions=f"Greet {caller_name} briefly. Under 25 words.",
        allow_interruptions=False,
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            worker_type=WorkerType.ROOM,
            agent_name=AGENT_NAME,
            # The one-time .imx conversion can take minutes; the default 60s
            # process-init deadline would kill the worker mid-load.
            initialize_process_timeout=300,
            # Keep one process warm so avatar dispatch is instant, not a cold load.
            num_idle_processes=1,
        ),
    )
