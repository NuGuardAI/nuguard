"""Tests for OpenAI-style messages=[{role,content}] chat-field detection.

Covers the fix for endpoints like Phlox's ``/api/chat`` — a real conversational
endpoint whose request schema is ``{"messages": list[dict], ...}`` — being
completely invisible to chat-endpoint auto-discovery because the extractors'
prompt-field heuristics only recognized singular field names (``message``,
``prompt``, ``query``, ...), never plural/list-shaped ``messages`` history.
"""
from __future__ import annotations

import ast

from nuguard.sbom.adapters.csharp.aspnet_core import _MESSAGE_HISTORY_FIELDS
from nuguard.sbom.adapters.python.fastapi_adapter import (
    FastAPIAdapter,
    _infer_chat_payload_key,
)
from nuguard.sbom.adapters.python.flask_adapter import (
    _infer_chat_payload_key as _flask_infer_chat_payload_key,
)
from nuguard.sbom.ast_parser import parse
from nuguard.sbom.types import ComponentType

# ── FastAPI: unit tests on the schema-typed inference function ──────────────



def test_messages_list_dict_field_detected_as_chat_list() -> None:
    fields = {"messages": "list[dict]", "raw_transcription": "str | None"}
    key, is_list = _infer_chat_payload_key(fields)
    assert key == "messages"
    assert is_list is True


def test_messages_list_str_field_not_matched_as_role_content() -> None:
    """A plain list[str] named 'messages' is deliberately NOT matched here —
    it isn't role/content-shaped, and guessing wrong would make TargetAppClient
    send [{"role": ..., "content": ...}] dicts to an endpoint expecting bare
    strings. Same as pre-fix behaviour for this field (never detected)."""
    fields = {"messages": "list[str]"}
    assert _infer_chat_payload_key(fields) == (None, False)


def test_singular_prompt_field_still_takes_priority_over_messages() -> None:
    fields = {"prompt": "str", "messages": "list[dict]"}
    key, is_list = _infer_chat_payload_key(fields)
    assert key == "prompt"
    assert is_list is False


def test_history_and_conversation_field_names_also_detected() -> None:
    for name in ("history", "conversation", "chat_history"):
        key, is_list = _infer_chat_payload_key({name: "list[dict]"})
        assert key == name
        assert is_list is True


def test_no_chat_field_still_returns_none() -> None:
    fields = {"amount": "float", "count": "int"}
    assert _infer_chat_payload_key(fields) == (None, False)


# ── FastAPI: end-to-end adapter extraction (Phlox-shaped source) ────────────


def test_fastapi_endpoint_with_messages_schema_gets_chat_payload_key() -> None:
    code = """
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class ChatRequest(BaseModel):
    messages: list[dict]
    raw_transcription: str | None = None
    patient_context: dict | None = None

@app.post("/api/chat")
async def chat(request: ChatRequest):
    return {"response": "ok"}
"""
    pr = parse(code)
    detections = FastAPIAdapter().extract(code, "app.py", pr)
    endpoints = [d for d in detections if d.component_type == ComponentType.API_ENDPOINT]
    assert len(endpoints) == 1
    meta = endpoints[0].metadata
    assert meta.get("chat_payload_key") == "messages"
    assert meta.get("chat_payload_list") is True


# ── Flask: AST-pattern detection (no type info) ──────────────────────────────


def _first_func_def(code: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(code)
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_flask_messages_get_pattern_sets_payload_list_true() -> None:
    func_def = _first_func_def(
        "def handler():\n"
        "    data = request.json\n"
        "    messages = data.get('messages')\n"
        "    return {}\n"
    )
    key, is_history = _flask_infer_chat_payload_key(func_def)
    assert key == "messages"
    assert is_history is True


def test_flask_prompt_get_pattern_not_marked_as_history() -> None:
    func_def = _first_func_def(
        "def handler():\n"
        "    data = request.json\n"
        "    prompt = data.get('prompt')\n"
        "    return {}\n"
    )
    key, is_history = _flask_infer_chat_payload_key(func_def)
    assert key == "prompt"
    assert is_history is False


# ── ASP.NET Core: name-based heuristic (no type introspection) ──────────────


def test_aspnet_message_history_field_set_matches_fastapi_convention() -> None:
    assert _MESSAGE_HISTORY_FIELDS == {"messages", "history", "conversation", "chat_history"}
