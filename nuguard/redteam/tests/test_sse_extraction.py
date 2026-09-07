"""Tests for _extract_sse_event_text (redteam signal Gap F).

Confirms common streaming-chat event shapes decode correctly, and that an
unrecognized event shape returns None (not raw JSON dumped as text) so
downstream capability-discovery parsing never mistakes JSON keys/fragments
for real assistant output or tool names.
"""
from nuguard.redteam.target.client import _extract_sse_event_text


def test_content_block_chunk_event_shape_extracted():
    event = {"type": "content_block", "chunk": "Hello, how can I help?"}
    assert _extract_sse_event_text(event) == "Hello, how can I help?"


def test_openai_delta_content_shape_still_extracted():
    event = {"choices": [{"delta": {"content": "partial text"}}]}
    assert _extract_sse_event_text(event) == "partial text"


def test_bare_string_delta_extracted():
    event = {"type": "delta", "delta": "streamed text"}
    assert _extract_sse_event_text(event) == "streamed text"


def test_nested_delta_dict_text_extracted():
    event = {"delta": {"type": "text_delta", "text": "nested text"}}
    assert _extract_sse_event_text(event) == "nested text"


def test_error_event_not_treated_as_text():
    assert _extract_sse_event_text({"error": "boom"}) == ""


def test_known_control_frame_with_no_text_is_empty_not_none():
    event = {"type": "message_start", "id": "msg_1", "model": "x"}
    assert _extract_sse_event_text(event) == ""


def test_unrecognized_event_shape_returns_none_not_raw_json():
    event = {"conversation_id": "abc", "answering": True, "foo_bar_field": 1}
    assert _extract_sse_event_text(event) is None
