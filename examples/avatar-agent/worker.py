"""LiveKit avatar agent (bitHuman) answering Microsoft Teams calls via
livekit-msteams-bridge (the Python bridge).

A voice pipeline plus a bitHuman AvatarSession, following LiveKit's avatar example
(https://github.com/livekit/agents/tree/main/examples/avatar_agents/bithuman).
The Teams caller HEARS the avatar's synchronized voice; the avatar's video
stays in the LiveKit room in v1 (the Teams video tile is rendered by the
StandIn media bridge's own animated avatar).

Run:  python worker.py dev
Env:  LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET,
      BITHUMAN_API_SECRET, BITHUMAN_MODEL_PATH (an .imx avatar model),
      and one model stack (see .env.example)
"""

import asyncio
import base64
import json
import os

from bithuman import AsyncBithuman
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
from livekit.agents.llm import ImageContent
from livekit.plugins import bithuman, openai, silero

load_dotenv()

AGENT_NAME = "standin-agent"  # must equal the bridge's LIVEKIT_AGENT_NAME


def prewarm(proc: JobProcess):
    # VAD loads here, in the prewarmed process, so a dispatch never waits on it.
    proc.userdata["vad"] = silero.VAD.load()
    # The bitHuman runtime is NOT built here: it loads through an async factory
    # (AsyncBithuman.create) and prewarm is synchronous. The entrypoint builds it and caches it on
    # the process, so the ~2 min first .imx conversion is paid once per worker process rather than
    # once per call.
    proc.userdata["bithuman"] = None
    # Fail here rather than after connect: moving the load into the entrypoint also moved a missing
    # BITHUMAN_* from "the process dies at init" to "this call answers and then goes silent".
    for key in ("BITHUMAN_MODEL_PATH", "BITHUMAN_API_SECRET"):
        if not os.environ.get(key):
            raise RuntimeError(f"{key} is required by this avatar worker")


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)

    meta = json.loads(ctx.job.metadata) if ctx.job.metadata else {}
    caller_name = meta.get("caller_name", "caller")

    # Same three-tier selection as ../voice-agent: the avatar lip-syncs whatever audio the session
    # produces, so the pipeline tier is orthogonal to the avatar. Azure realtime first where a
    # deployment exists - it is speech-to-speech, so it needs no separate whisper/tts deployments,
    # and an Azure OpenAI resource commonly has neither.
    az_ep = os.environ.get("AZURE_OPENAI_ENDPOINT")
    az_key = os.environ.get("AZURE_OPENAI_API_KEY")
    rt_deployment = os.environ.get("AZURE_OPENAI_REALTIME_DEPLOYMENT")

    if az_ep and az_key and rt_deployment:
        session = AgentSession(
            llm=openai.realtime.RealtimeModel.with_azure(
                azure_deployment=rt_deployment,
                # Realtime is served from wss://<res>.cognitiveservices.azure.com, NOT
                # <res>.openai.azure.com, which 404s the websocket handshake. `or` rather than a
                # dict default, so an exported-but-empty override falls back instead of building a
                # base_url of "/openai".
                azure_endpoint=os.environ.get("AZURE_OPENAI_REALTIME_ENDPOINT") or az_ep,
                api_key=az_key,
                api_version=os.environ.get("AZURE_OPENAI_REALTIME_API_VERSION")
                or os.environ.get("AZURE_OPENAI_API_VERSION"),
                # cedar, not the SDK's default marin: the .imx avatar this example ships is MALE,
                # so a female default is a defect in the example rather than a preference.
                voice=os.environ.get("AZURE_OPENAI_REALTIME_VOICE") or "cedar",
            ),
        )
    elif az_ep and az_key:
        az = dict(
            azure_endpoint=az_ep,
            api_key=az_key,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION"),
        )
        session = AgentSession(
            stt=openai.STT.with_azure(model=os.environ.get("AZURE_OPENAI_STT_DEPLOYMENT", "whisper"), **az),
            llm=openai.LLM.with_azure(model=os.environ.get("AZURE_OPENAI_MODEL_NAME", "gpt-4o-mini"), **az),
            tts=openai.TTS.with_azure(
                model=os.environ.get("AZURE_OPENAI_TTS_DEPLOYMENT", "tts-1"),
                voice=os.environ.get("AZURE_OPENAI_TTS_VOICE", "alloy"),
                **az,
            ),
            vad=ctx.proc.userdata["vad"],
        )
    else:
        session = AgentSession(
            stt=openai.STT(),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=openai.TTS(voice="alloy"),
            vad=ctx.proc.userdata["vad"],
        )

    # The avatar runtime lip-syncs the session's audio and publishes synchronized audio+video into
    # the room; the bridge relays the audio to Teams.
    #
    # AsyncBithuman is an ALIAS for AsyncAvatar, whose __init__ takes neither model_path nor
    # load_model - the model only loads through this async factory. Cached on the process, but note
    # the honest limit: AvatarSession.aclose() calls runtime.cleanup(), which nulls the fixture, and
    # a warmed process serves exactly one job - so this cache is a re-entrancy guard, not a
    # cross-call amortiser. Do not turn it into a reuse loop without handling the dead runtime.
    if ctx.proc.userdata.get("bithuman") is None:
        ctx.proc.userdata["bithuman"] = await AsyncBithuman.create(
            model_path=os.environ["BITHUMAN_MODEL_PATH"],
            api_secret=os.environ["BITHUMAN_API_SECRET"],
        )

    avatar = bithuman.AvatarSession(
        model_path=os.environ["BITHUMAN_MODEL_PATH"],
        api_secret=os.environ["BITHUMAN_API_SECRET"],
        # Passing runtime= is what makes the cache reachable; without it the plugin calls
        # AsyncBithuman.create itself, once per session.
        runtime=ctx.proc.userdata["bithuman"],
    )
    await avatar.start(session, room=ctx.room)

    @ctx.room.on("data_received")
    def on_data(packet: rtc.DataPacket):
        try:
            payload = json.loads(packet.data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if packet.topic == "msteams.goodbye":
            session.say(payload.get("text", "Goodbye!"), allow_interruptions=False)

    # Ambient vision (only when the bridge runs with AMBIENT_VISION=true). One image per stream,
    # already attributed: attributes carry source / owner / caption / width / height / ts. The bridge
    # sends only CHANGED frames and caps the rate, so this stays cheap - but every image kept is
    # tokens on the next turn, so each one is dropped into the context and left to age out rather
    # than pinning a gallery.
    #
    # Without this the agent has no picture at all, and a model asked "what is on my screen?"
    # answers confidently from nothing.
    session_running = asyncio.Event()
    vision_tasks: set[asyncio.Task] = set()
    # Serialises the read-modify-write below. Each frame arrives on its own task, and the update is
    # copy -> append -> write-the-whole-context-back. Two overlapping frames both copy the SAME base
    # context, each appends its own message, and each writes back, so update_chat_ctx re-sends items
    # the Realtime session already holds. That is not theoretical: an unlocked version produced 868
    # "Error adding item: an item with id ... already exists" plus 54 "previous_item_id ... not
    # found" in a single call, and the session stopped accepting anything - including the very
    # vision frames this handler exists to deliver. Camera and screen-share frames arrive
    # back-to-back, so the tasks overlap constantly rather than occasionally.
    vision_lock = asyncio.Lock()
    # Cap on how many vision images stay in the context. Every frame appends an image and
    # update_chat_ctx re-diffs the whole history, so an uncapped call grows the payload without
    # bound and re-sends a longer list each time. The agent only needs to see what is on screen
    # NOW; older frames are what the transcript is for.
    MAX_VISION_IMAGES = 4

    def on_vision(reader: rtc.ByteStreamReader, participant_identity: str):
        async def consume():
            attrs = reader.info.attributes or {}
            # Drain the stream immediately - the sender is waiting on this side to read it.
            data = b"".join([chunk async for chunk in reader])
            mime = reader.info.mime_type or "image/jpeg"
            caption = attrs.get("caption", "Live frame of the call.")
            # flush=True: print() is block-buffered on a pipe, so without it these receipts sit in
            # an 8 KB buffer while the JSON logging lines (which flush) stream past. That made a
            # working vision path look dead for a whole call during debugging.
            print(
                f"[msteams.vision] {caption} ({len(data)} bytes, {attrs.get('source')})",
                flush=True,
            )
            # session.current_agent raises until session.start() has run, so a frame that lands
            # during startup would be lost inside this task. Hold it instead of dropping it.
            await session_running.wait()
            # Nothing here asks the agent to speak: the image is context for its NEXT turn.
            # The whole read-modify-write must be atomic - see vision_lock above.
            async with vision_lock:
                agent = session.current_agent
                chat_ctx = agent.chat_ctx.copy()
                chat_ctx.add_message(
                    role="user",
                    content=[
                        caption,
                        ImageContent(image=f"data:{mime};base64,{base64.b64encode(data).decode()}"),
                    ],
                )
                # Drop the oldest images once past the cap, keeping every non-image turn so the
                # conversation itself is never truncated - only the stale pictures are.
                images = [
                    m for m in chat_ctx.items
                    if getattr(m, "role", None) == "user"
                    and any(isinstance(c, ImageContent) for c in (getattr(m, "content", None) or []))
                ]
                if len(images) > MAX_VISION_IMAGES:
                    stale = {id(m) for m in images[: len(images) - MAX_VISION_IMAGES]}
                    chat_ctx.items[:] = [m for m in chat_ctx.items if id(m) not in stale]
                await agent.update_chat_ctx(chat_ctx)

        # Keep a strong reference: a bare create_task can be garbage-collected mid-stream.
        task = asyncio.create_task(consume())
        vision_tasks.add(task)
        task.add_done_callback(vision_tasks.discard)

    # BEFORE session.start: a byte stream whose topic has no handler is logged at info and dropped,
    # permanently - so registering after start would lose every frame that arrives during startup.
    ctx.room.register_byte_stream_handler("msteams.vision", on_vision)

    await session.start(
        agent=Agent(
            instructions=(
                f"You are a friendly avatar assistant on a Microsoft Teams call with {caller_name}. "
                "Keep answers short and natural."
            ),
        ),
        room=ctx.room,
    )
    session_running.set()

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
            # The one-time .imx conversion can take minutes; the 10s default process-init deadline
            # would kill the worker mid-load on the first run after a model change.
            initialize_process_timeout=300,
            # Keep one process warm so avatar dispatch is instant, not a cold load.
            num_idle_processes=1,
        ),
    )
