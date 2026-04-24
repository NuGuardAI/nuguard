from __future__ import annotations

import json
from pathlib import Path

from nuguard.redteam.llm_engine.prompt_cache import PromptCache


def test_path_for_includes_model_slug() -> None:
    cache = PromptCache(Path("."), llm_model="gemini/gemini-2.0-flash")
    p = cache.path_for("abc123")
    assert p.name == "redteam-prompts-gemini-gemini-2-0-flash-abc123.json"


def test_save_writes_model_aware_filename(tmp_path: Path) -> None:
    cache = PromptCache(tmp_path, llm_model="openai/gpt-4.1-mini")
    out = cache.save("deadbeefdeadbeef", {"scenario": {"turn_sequences": [["a", "b"]]}})
    assert out.name == "redteam-prompts-openai-gpt-4-1-mini-deadbeefdeadbeef.json"
    assert out.exists()


def test_load_falls_back_to_legacy_filename(tmp_path: Path) -> None:
    key = "0011223344556677"
    legacy = tmp_path / f"redteam-prompts-{key}.json"
    legacy_payload = {
        "cache_key": key,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "scenarios": {"legacy-scenario": {"turn_sequences": [["x", "y"]]}},
    }
    legacy.write_text(json.dumps(legacy_payload), encoding="utf-8")

    cache = PromptCache(tmp_path, llm_model="gemini/gemini-2.0-flash")
    loaded = cache.load(key)
    assert loaded is not None
    assert loaded.get("cache_key") == key
    assert "legacy-scenario" in loaded.get("scenarios", {})
