"""livekit-msteams-bridge - public API.

Typical embedding:

    from livekit_msteams_bridge import load_config, start_server

    server = await start_server(load_config())

Or run the CLI: `livekit-msteams-bridge` (env-configured, see .env.example).
"""

from .cli import load_dotenv
from .config import BridgeConfig, load_config
from .hmac_auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, is_fresh, sign, verify
from .log import Logger, logger
from .metrics import render_metrics, reset_metrics
from .protocol import parse_worker_message, pcm16k_bytes_to_ms
from .server import BridgeServer, ReplayGuard, authorize_upgrade, call_id_from_path, start_server
from .session import AgentRoomPort, CallSession, RoomConnector, RoomHandlers, WorkerPort

__version__ = "0.1.0"

__all__ = [
    "AgentRoomPort",
    "BridgeConfig",
    "BridgeServer",
    "CallSession",
    "Logger",
    "ReplayGuard",
    "RoomConnector",
    "RoomHandlers",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "WorkerPort",
    "__version__",
    "authorize_upgrade",
    "call_id_from_path",
    "is_fresh",
    "load_config",
    "load_dotenv",
    "logger",
    "parse_worker_message",
    "pcm16k_bytes_to_ms",
    "render_metrics",
    "reset_metrics",
    "sign",
    "start_server",
    "verify",
]

from .livekit_room import (  # noqa: E402  (after __all__ on purpose)
    TOPIC_CONTEXT,
    TOPIC_GOODBYE,
    LiveKitRoomPort,
    connect_livekit_room,
)

__all__ += ["TOPIC_CONTEXT", "TOPIC_GOODBYE", "LiveKitRoomPort", "connect_livekit_room"]
