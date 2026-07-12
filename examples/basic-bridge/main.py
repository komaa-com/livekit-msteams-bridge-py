"""Minimal embedding of livekit-msteams-bridge.

Run: `python main.py` (reads .env from this directory; see .env.example).
Pair it with a running LiveKit agent worker registered under the same
agent name as LIVEKIT_AGENT_NAME - see the docs for ready-to-run example
agents (voice pipeline and bitHuman avatar).
"""

from __future__ import annotations

import asyncio
import signal

from livekit_msteams_bridge import load_config, load_dotenv, start_server


async def main() -> None:
    cfg = load_config()
    server = await start_server(cfg)
    print(f"Point your StandIn identity's agent WebSocket URL at ws://<this-host>:{cfg.port}/voice/msteams/stream")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await server.close()


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())
