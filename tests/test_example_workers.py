"""The example workers are the only place the agent-side contract lives, and CI cannot import them:
the dev extras deliberately contain pytest/ruff/Pillow only, not livekit-agents, livekit-plugins-*
or bithuman. So these assertions read the workers' AST instead - which also means comments and
docstrings can never satisfy a check, because ast.parse does not keep comments and the docstring
strip below removes the rest.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"
WORKERS = {
    "voice-agent": EXAMPLES / "voice-agent" / "worker.py",
    "avatar-agent": EXAMPLES / "avatar-agent" / "worker.py",
}


def tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text())


def code(path: pathlib.Path) -> str:
    """Executable source only: no comments (ast drops them), no docstrings (stripped here)."""
    parsed = tree(path)
    for node in ast.walk(parsed):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    node.body = body[1:] or [ast.Pass()]
    return ast.unparse(parsed)


def string_constants(path: pathlib.Path) -> set[str]:
    return {n.value for n in ast.walk(tree(path)) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


@pytest.mark.parametrize("name", sorted(WORKERS))
def test_workers_parse(name):
    tree(WORKERS[name])


@pytest.mark.parametrize("name", sorted(WORKERS))
def test_both_workers_register_the_same_agent_name(name):
    """All examples register `standin-agent`: only one runs at a time, since explicit dispatch
    resolves a single name."""
    assert "AGENT_NAME = 'standin-agent'" in code(WORKERS[name])


@pytest.mark.parametrize("name", sorted(WORKERS))
def test_no_pre_rename_strings_survive(name):
    stale = {"teams.context", "teams.goodbye", "teams.vision", "standin-voice-agent", "standin-avatar-agent"}
    assert not (string_constants(WORKERS[name]) & stale)


@pytest.mark.parametrize("name", sorted(WORKERS))
def test_three_tier_pipeline_selection(name):
    """Azure realtime when a realtime deployment exists, else Azure STT/LLM/TTS, else plain OpenAI.
    The operator's Azure resource has two dozen chat deployments but neither whisper nor tts-1, and
    the plain OpenAI key has no credits - so a worker without the realtime tier cannot speak."""
    source = code(WORKERS[name])
    for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_REALTIME_DEPLOYMENT"):
        assert f"os.environ.get('{key}')" in source
    assert "openai.realtime.RealtimeModel.with_azure(" in source
    assert "openai.STT.with_azure(" in source and "openai.LLM.with_azure(" in source
    assert "openai.TTS.with_azure(" in source
    assert "openai.STT()" in source  # the plain-OpenAI fallback tier


@pytest.mark.parametrize("name", sorted(WORKERS))
def test_realtime_endpoint_and_api_version_fall_back_with_or_not_a_dict_default(name):
    """`os.environ.get(K, az_ep)` returns "" for an exported-but-empty K and builds a base_url of
    "/openai"; `or` falls back properly. The endpoint override exists because realtime is served
    from <res>.cognitiveservices.azure.com, not <res>.openai.azure.com."""
    source = code(WORKERS[name])
    assert "os.environ.get('AZURE_OPENAI_REALTIME_ENDPOINT') or az_ep" in source
    assert "os.environ.get('AZURE_OPENAI_REALTIME_API_VERSION') or os.environ.get('AZURE_OPENAI_API_VERSION')" in source


def test_realtime_voice_defaults_differ_per_example():
    """marin is the SDK default (a statement of intent in the voice worker); the avatar example
    ships a MALE .imx, so a female default there is a defect."""
    assert "os.environ.get('AZURE_OPENAI_REALTIME_VOICE') or 'marin'" in code(WORKERS["voice-agent"])
    assert "os.environ.get('AZURE_OPENAI_REALTIME_VOICE') or 'cedar'" in code(WORKERS["avatar-agent"])


@pytest.mark.parametrize("name", sorted(WORKERS))
def test_vision_handler_is_registered_before_session_start(name):
    """A byte stream whose topic has no handler is logged at info and dropped, permanently - so
    registering after start loses every frame that arrives during startup."""
    body = next(
        node.body
        for node in ast.walk(tree(WORKERS[name]))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "entrypoint"
    )
    register_line = next(
        stmt.lineno
        for stmt in ast.walk(ast.Module(body=body, type_ignores=[]))
        if isinstance(stmt, ast.Call)
        and isinstance(stmt.func, ast.Attribute)
        and stmt.func.attr == "register_byte_stream_handler"
        and stmt.args
        and isinstance(stmt.args[0], ast.Constant)
        and stmt.args[0].value == "msteams.vision"
    )
    start_line = next(
        stmt.lineno
        for stmt in ast.walk(ast.Module(body=body, type_ignores=[]))
        if isinstance(stmt, ast.Call)
        and isinstance(stmt.func, ast.Attribute)
        and stmt.func.attr == "start"
        and isinstance(stmt.func.value, ast.Name)
        and stmt.func.value.id == "session"  # not avatar.start(), which runs earlier by design
    )
    assert register_line < start_line


@pytest.mark.parametrize("name", sorted(WORKERS))
def test_vision_frames_become_context_and_never_speech(name):
    source = code(WORKERS[name])
    assert "chat_ctx.add_message(role='user'" in source
    assert "await agent.update_chat_ctx(chat_ctx)" in source
    assert "ImageContent(" in source
    # the image is context for the NEXT turn: nothing on this path may make the agent talk
    consume = next(
        node
        for node in ast.walk(tree(WORKERS[name]))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "consume"
    )
    called = {n.func.attr for n in ast.walk(consume) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "say" not in called and "generate_reply" not in called


def test_bithuman_runtime_is_built_by_the_async_factory_not_the_constructor():
    """In bithuman 2.6 AsyncBithuman IS AsyncAvatar, whose __init__ takes neither model_path nor
    load_model - the old prewarm call raised TypeError and every job process died at init."""
    source = code(WORKERS["avatar-agent"])
    assert "await AsyncBithuman.create(" in source
    assert "runtime=ctx.proc.userdata['bithuman']" in source
    bare_constructor = [
        n
        for n in ast.walk(tree(WORKERS["avatar-agent"]))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "AsyncBithuman"
    ]
    assert not bare_constructor, "AsyncBithuman(...) as a constructor raises TypeError on bithuman 2.6"


def test_prewarm_does_not_claim_to_load_the_avatar_model():
    prewarm = next(
        node
        for node in ast.walk(tree(WORKERS["avatar-agent"]))
        if isinstance(node, ast.FunctionDef) and node.name == "prewarm"
    )
    calls = {n.func.attr for n in ast.walk(prewarm) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "create" not in calls  # a coroutine cannot be awaited from a sync prewarm
    # the two env vars are still validated here: cheap, sync, and it keeps the failure at startup
    assert "BITHUMAN_MODEL_PATH" in {
        n.value for n in ast.walk(prewarm) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
