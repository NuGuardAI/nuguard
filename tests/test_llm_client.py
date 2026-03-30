"""Manual smoke-test for LLMClient across all supported provider combos.

Run from the repo root:
    uv run python tmp/test_llm_client.py

Or from a test directory that has a .env file:
    cd tests/agentic-healthcare-ai && uv run python ../../tmp/test_llm_client.py

Each case prints: PASS / FAIL / SKIP, the effective model string, elapsed time,
and the first 120 chars of the response.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env from CWD or nearest parent (non-fatal if absent)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    for _candidate in [Path.cwd() / ".env", Path(__file__).parent.parent / "tests/agentic-healthcare-ai/.env"]:
        if _candidate.exists():
            load_dotenv(_candidate, override=False)
            print(f"[env] Loaded {_candidate}\n")
            break
except ImportError:
    pass  # python-dotenv not installed — rely on shell environment


# ---------------------------------------------------------------------------
# Test-case definition
# ---------------------------------------------------------------------------

@dataclass
class Case:
    name: str
    model: str
    api_key_env: str = ""          # env var name to use as api_key (empty → None)
    api_base_env: str = ""         # env var name for api_base (empty → None)
    extra_env: dict[str, str] = field(default_factory=dict)  # env vars to inject
    expect_canned: bool = False    # True if we expect a canned response (no real call)
    skip_if_missing: list[str] = field(default_factory=list)  # skip when these env vars are unset
    clear_env: list[str] = field(default_factory=list)        # temporarily unset these env vars during the test


CASES: list[Case] = [
    # ── No API key → canned response ─────────────────────────────────────────
    Case(
        name="no-key → canned response",
        model="gemini/gemini-2.0-flash",
        api_key_env="",          # deliberately empty
        expect_canned=True,
        # Clear all provider keys so _resolve_api_key returns None
        clear_env=["GEMINI_API_KEY", "GOOGLE_API_KEY", "LITELLM_API_KEY", "OPENAI_API_KEY"],
    ),

    # ── Gemini via REST (plain API key, gemini/ prefix) ───────────────────────
    Case(
        name="gemini/gemini-2.0-flash (Gemini REST)",
        model="gemini/gemini-2.0-flash",
        api_key_env="GEMINI_API_KEY",
        skip_if_missing=["GEMINI_API_KEY"],
    ),

    # ── vertex_ai/ with plain API key → auto-rewrite to gemini/ ──────────────
    Case(
        name="vertex_ai/gemini-2.0-flash → auto-rewrite (plain key)",
        model="vertex_ai/gemini-2.0-flash",
        api_key_env="GEMINI_API_KEY",
        skip_if_missing=["GEMINI_API_KEY"],
        # No GOOGLE_APPLICATION_CREDENTIALS set → triggers auto-rewrite
    ),

    # ── OpenAI ───────────────────────────────────────────────────────────────
    Case(
        name="openai/gpt-4.1-mini (OpenAI)",
        model="openai/gpt-4.1-mini",
        api_key_env="OPENAI_API_KEY",
        skip_if_missing=["OPENAI_API_KEY"],
    ),

    # ── Azure OpenAI (azure/<deployment-name>) ────────────────────────────────
    Case(
        name="azure/gpt-4.1 (Azure OpenAI)",
        model="azure/gpt-4.1",
        api_key_env="AZURE_OPENAI_KEY",
        api_base_env="AZURE_OPENAI_ENDPOINT",
        skip_if_missing=["AZURE_OPENAI_KEY", "AZURE_OPENAI_ENDPOINT"],
    ),

    # ── Azure AI Foundry — Kimi K2 (OpenAI-compatible endpoint) ──────────────
    Case(
        name="openai/Kimi-K2-Thinking (Azure AI Foundry)",
        model="openai/Kimi-K2-Thinking",
        api_key_env="AZURE_KIMI_K2_KEY",
        api_base_env="AZURE_KIMI_K2_ENDPOINT",
        skip_if_missing=["AZURE_KIMI_K2_KEY", "AZURE_KIMI_K2_ENDPOINT"],
    ),
]

PROMPT = "Reply with exactly one word: hello."
SYSTEM = "You are a concise assistant. Never use more than one word."

_CANNED_PREFIX = "[NUGUARD_CANNED_RESPONSE]"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_case(case: Case) -> tuple[str, str, float, str]:
    """Returns (status, effective_model, elapsed_s, response_excerpt)."""
    # Check skip conditions
    missing = [v for v in case.skip_if_missing if not os.environ.get(v)]
    if missing:
        return "SKIP", case.model, 0.0, f"missing env: {', '.join(missing)}"

    # Resolve values
    api_key: str | None = os.environ.get(case.api_key_env) if case.api_key_env else None
    api_base: str | None = os.environ.get(case.api_base_env) if case.api_base_env else None

    # Temporarily unset GOOGLE_APPLICATION_CREDENTIALS for the vertex auto-rewrite test
    # so the rewrite logic fires (it checks for that env var).
    # Also clear any env vars the case specifies (e.g. for the no-key test).
    saved_env: dict[str, str] = {}
    to_clear = list(case.clear_env)
    if "vertex_ai/" in case.model:
        to_clear.append("GOOGLE_APPLICATION_CREDENTIALS")
    for var in to_clear:
        val = os.environ.pop(var, None)
        if val is not None:
            saved_env[var] = val

    try:
        from nuguard.common.llm_client import LLMClient

        client = LLMClient(model=case.model, api_key=api_key, api_base=api_base)

        t0 = time.monotonic()
        response = await client.complete(PROMPT, system=SYSTEM, label=case.name)
        elapsed = time.monotonic() - t0
    finally:
        os.environ.update(saved_env)

    effective_model = client.model
    is_canned = response.startswith(_CANNED_PREFIX)

    if case.expect_canned:
        status = "PASS" if is_canned else "FAIL (expected canned, got real response)"
    else:
        status = "FAIL (got canned response)" if is_canned else "PASS"

    excerpt = response[:120].replace("\n", " ")
    return status, effective_model, elapsed, excerpt


async def main() -> None:
    print("=" * 70)
    print("LLMClient smoke-test")
    print("=" * 70)

    results: list[tuple[str, Case, str, str, float, str]] = []

    for case in CASES:
        print(f"\n▶  {case.name}")
        print(f"   model config : {case.model}")
        try:
            status, effective_model, elapsed, excerpt = await run_case(case)
        except Exception as exc:
            status, effective_model, elapsed, excerpt = f"ERROR: {type(exc).__name__}: {exc}", case.model, 0.0, ""

        if effective_model != case.model:
            print(f"   effective    : {effective_model}  ← rewritten")
        print(f"   status       : {status}")
        if elapsed:
            print(f"   elapsed      : {elapsed:.2f}s")
        if excerpt:
            print(f"   response     : {excerpt!r}")

        results.append((status, case, status, effective_model, elapsed, excerpt))

    # Summary
    print("\n" + "=" * 70)
    print("Summary")
    print("=" * 70)
    passes = sum(1 for r in results if r[0].startswith("PASS"))
    skips  = sum(1 for r in results if r[0] == "SKIP")
    fails  = sum(1 for r in results if r[0].startswith("FAIL") or r[0].startswith("ERROR"))
    print(f"  PASS: {passes}   SKIP: {skips}   FAIL: {fails}   total: {len(results)}")
    if fails:
        print("\nFailed cases:")
        for r in results:
            if r[0].startswith("FAIL") or r[0].startswith("ERROR"):
                print(f"  · {r[1].name}: {r[0]}")


if __name__ == "__main__":
    asyncio.run(main())
