#!/usr/bin/env python3
"""test_api.py — Multi-turn tester for the Golden Bank CES agent.

Calls the Google Customer Engagement Suite (CES) runSession API directly,
using gcloud for auth (no local proxy needed).

Usage:
    python3 test_api.py

Environment variables:
    GEMINI_API_KEY / GOOGLE_API_KEY   API key for LLM turn-generation and judging
    TURN_DELAY                        Seconds between turns (default: 3)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# CES API constants — values taken from the working curl command
# ---------------------------------------------------------------------------

_PROJECT    = "platform-dev-2025"
_APP_ID     = "2f519af5-a66e-40a4-ad77-b52cb9a96394"
_VERSION_ID = "9c99ba4b-fcec-4466-96c7-950e111bd239"
_DEPLOY_ID  = "4c8a5e8b-4bfd-46b4-9001-4933ab2b3b2f"

_APP_RESOURCE = f"projects/{_PROJECT}/locations/us/apps/{_APP_ID}"
_CES_BASE     = f"https://ces.googleapis.com/v1beta/{_APP_RESOURCE}"

TURN_DELAY = float(os.environ.get("TURN_DELAY", "3"))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _access_token() -> str:
    """Return a short-lived gcloud access token."""
    for cmd in (
        ["gcloud", "auth", "print-access-token"],
        ["gcloud", "auth", "application-default", "print-access-token"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            t = r.stdout.strip()
            if t and not r.returncode:
                return t
        except Exception:
            pass
    raise RuntimeError("No gcloud credentials. Run: gcloud auth login")


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def _new_session_id(length: int = 16) -> str:
    """Generate a short random alphanumeric session ID, matching CES format."""
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


def _ces_post(session_id: str, message: str) -> str:
    """Send one turn via CES runSession and return the reply text."""
    token = _access_token()
    url   = f"{_CES_BASE}/sessions/{session_id}:runSession"
    body  = json.dumps({
        "config": {
            "session":      f"{_APP_RESOURCE}/sessions/{session_id}",
            "app_version":  f"{_APP_RESOURCE}/versions/{_VERSION_ID}",
            "deployment":   f"{_APP_RESOURCE}/deployments/{_DEPLOY_ID}",
        },
        "inputs": [{"text": message}],
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        err = exc.read().decode()[:500]
        raise RuntimeError(f"CES API error {exc.code}: {err}") from exc

    for output in data.get("outputs", []):
        text = output.get("text", "").strip()
        if text:
            return text
    return "(no response)"


# ---------------------------------------------------------------------------
# Multi-turn runner
# ---------------------------------------------------------------------------

def run_turn(session_id: str, message: str) -> tuple[str, list[str]]:
    """Send one message and return (reply_text, tools_seen).

    CES runSession doesn't expose sub-agent event streams like ADK /run does,
    so tools_seen is always empty — kept for API compatibility with the judge.
    """
    reply = _ces_post(session_id, message)
    return reply, []


# ---------------------------------------------------------------------------
# LLM helpers (follow-up generation + judging)
# ---------------------------------------------------------------------------

def _load_dotenv(path: str) -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        pass


def _nuguard_llm_client():
    """Import and return a nuguard LLMClient, adding project root to sys.path if needed."""
    try:
        from nuguard.common.llm_client import LLMClient
        return LLMClient()
    except ImportError:
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from nuguard.common.llm_client import LLMClient  # type: ignore[import]
        return LLMClient()


async def _generate_followup(agent_response: str) -> str:
    """Generate a specific follow-up task from the agent's first reply."""
    system = (
        "You are a QA tester writing a follow-up message to send to a banking AI agent. "
        "Your goal is to give the agent a complete, self-contained banking task it can act on.\n"
        "Rules:\n"
        "- Include ALL necessary details inline (account type, amounts, etc.).\n"
        "- The message must read like something a real bank customer would send.\n"
        "- No placeholders. No generic requests.\n"
        "Reply with ONLY the message text — no quotes, no explanation."
    )
    prompt = (
        f"The agent described its capabilities:\n\n---\n{agent_response[:1500]}\n---\n\n"
        "Write a follow-up message (2-4 sentences) giving the agent a specific banking task."
    )
    client = _nuguard_llm_client()
    followup = await client.complete(prompt, system=system, label="test_api:turn2")
    followup = (followup or "").strip()
    return followup or "Can you help me check my recent transactions and explain any fees?"


async def _judge_response(
    user_request: str,
    agent_response: str,
) -> str:
    """LLM-judge whether the agent's response fulfilled the user request."""
    system = (
        "You are a strict QA evaluator reviewing a banking AI agent's response. "
        "Reply with ONLY a JSON object — no markdown, no explanation."
    )
    prompt = f"""
## User request
{user_request}

## Agent response
{agent_response[-3000:]}

## Evaluation task
Return a JSON object with:
- "verdict": "PASS" or "FAIL"
- "score": integer 1-5 (5 = fully met the request)
- "reasoning": one sentence explaining the verdict
- "gaps": list of strings describing what was missing (empty list if PASS)
""".strip()

    client = _nuguard_llm_client()
    raw = await client.complete(prompt, system=system, label="test_api:judge")
    raw = (raw or "").strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = "\n".join(
            ln for ln in raw.splitlines() if not ln.startswith("```")
        ).strip()

    try:
        v = json.loads(raw)
        verdict   = v.get("verdict", "?")
        score     = v.get("score", "?")
        reasoning = v.get("reasoning", "")
        gaps      = v.get("gaps") or []
        lines = [f"  Verdict  : {verdict}  (score {score}/5)", f"  Reasoning: {reasoning}"]
        if gaps:
            lines.append("  Gaps     :")
            for g in gaps:
                lines.append(f"    • {g}")
        return "\n".join(lines)
    except (json.JSONDecodeError, KeyError):
        return f"  Raw judge output:\n{raw[:500]}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    _load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

    session_id = _new_session_id()
    print(f"CES endpoint : {_CES_BASE}")
    print(f"Session ID   : {session_id}")
    print()

    # ── Turn 1: capability / greeting ────────────────────────────────────────
    turn1_msg = "Hi! I'm a new customer. What banking services can you help me with today?"
    print(f"==> Turn 1: {turn1_msg}")
    t0 = time.monotonic()
    turn1_reply, _ = run_turn(session_id, turn1_msg)
    print(f"    [{time.monotonic() - t0:.1f}s]\n{turn1_reply}\n")

    if not turn1_reply or turn1_reply == "(no response)":
        print("ERROR: No response from CES agent on turn 1.")
        sys.exit(1)

    # ── Turn 2: LLM-generated follow-up ──────────────────────────────────────
    print("==> Generating Turn 2 from agent response...")
    turn2_msg = await _generate_followup(turn1_reply)
    print(f"    Generated: {turn2_msg}\n")

    if TURN_DELAY > 0:
        print(f"    (waiting {TURN_DELAY:.0f}s...)\n")
        time.sleep(TURN_DELAY)

    print(f"==> Turn 2: {turn2_msg}")
    t0 = time.monotonic()
    turn2_reply, _ = run_turn(session_id, turn2_msg)
    print(f"    [{time.monotonic() - t0:.1f}s]\n{turn2_reply}\n")

    # ── Turn 3: clarification or edge-case follow-up ─────────────────────────
    turn3_candidates = [
        "What happens if I need to dispute a charge? What information do I need to provide?",
        "Are there any fees I should know about for these services?",
        "Can you summarize what you can and cannot help me with?",
    ]
    turn3_msg = random.choice(turn3_candidates)

    if TURN_DELAY > 0:
        time.sleep(TURN_DELAY)

    print(f"==> Turn 3: {turn3_msg}")
    t0 = time.monotonic()
    turn3_reply, _ = run_turn(session_id, turn3_msg)
    print(f"    [{time.monotonic() - t0:.1f}s]\n{turn3_reply}\n")

    # ── Validation ────────────────────────────────────────────────────────────
    print("==> Judging Turn 2 response...")
    verdict = await _judge_response(
        user_request=turn2_msg,
        agent_response=turn2_reply or turn1_reply,
    )
    print(verdict)
    print()
    print("==> Test complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
