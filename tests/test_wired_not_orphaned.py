"""A capability nothing calls is a bug, not a feature.

A behavioural test cannot catch "nobody calls this": when the call site vanishes, the behaviour
vanishes with it and there is nothing left to assert against. So these assertions read the SOURCE
and require the literal call, with comments and docstrings stripped first - via `ast`, so prose that
merely names a symbol can never satisfy the check.
"""

from __future__ import annotations

import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "livekit_msteams_bridge"


def code(module: str) -> str:
    """The module's source with every comment and docstring removed.

    ast.unparse() drops comments outright and `ast.get_docstring` strips the rest, so what is left
    is executable code only."""
    tree = ast.parse((SRC / module).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def require_call(module: str, *needles: str) -> None:
    source = code(module)
    for needle in needles:
        assert needle in source, f"{module} must actually call {needle} - the capability is unreachable without it"


def test_ambient_vision_is_wired_into_the_real_call_path():
    # Store-and-deliver on inbound worker video, re-try when the gate or the room unblocks, and
    # free the per-call frames at teardown.
    require_call("session.py", "self._vision.offer(msg)", "self._vision.flush()", "self._vision.release()")
    # ...and the room really publishes the image (a byte stream, because an image does not fit a
    # data packet). Dropping this leaves a feature that collects frames and delivers nothing.
    require_call("livekit_room.py", "async def send_vision(", "local_participant.stream_bytes(")
    # the delivery route the session hands to AmbientVision, and the counter that proves it landed
    require_call("session.py", "await room.send_vision(image)", "bridge_vision_frames_sent_total")


def test_path_anchoring_is_wired_with_the_configured_base():
    # A one-argument call site here is exactly the regression that makes the bridge answer on every
    # route again, and no behavioural unit test of call_id_from_path can see it.
    require_call("server.py", "call_id_from_path(path, cfg.ws_path)")
    # raw_path, not path: aiohttp decodes `path`, and decoding twice breaks a %2F callId.
    require_call("server.py", "request.raw_path")
    # WS_PATH is normalized once, loudly, at startup.
    require_call("config.py", "normalize_ws_path(")


def test_every_feature_resolves_its_defaults_through_its_single_resolver():
    # One defaults table per feature. A second copy in the env layer is how two backends end up
    # disagreeing about what "unset" means.
    require_call("config.py", "resolve_ambient_vision_config(")
    assert "AmbientVisionConfig(" not in code("config.py"), (
        "config.py must not build an AmbientVisionConfig itself - the defaults live in vision.py"
    )


def test_bridge_owned_topics_are_namespaced_but_livekits_is_untouched():
    room = code("livekit_room.py")
    assert "'msteams.context'" in room and "'msteams.goodbye'" in room and "'msteams.vision'" in room
    for stale in ("'teams.context'", "'teams.goodbye'", "'teams.vision'"):
        assert stale not in room, f"{stale} is not a topic this bridge publishes"


def test_the_docstring_stripper_cannot_be_satisfied_by_prose():
    """Guards the guard: if the stripper stopped stripping, every assertion above would pass on a
    file that only TALKS about the call."""
    session = code("session.py")
    assert "Barge-in note" not in session, "module docstrings must be stripped"
    assert "hot path: caller audio" not in session, "comments must be stripped"
    assert "class CallSession" in session, "stripping must not eat the code"
