#!/usr/bin/env python3
"""test_api.py — Multi-turn API tester for the Google ADK marketing_campaign_agent.

Uses nuguard.common.llm_client to generate the second turn dynamically from
the agent's first response, so the conversation adapts to what the agent says.

Usage:
    uv run python tests/apps/ai-agents-google-adk/test_api.py

Environment variables:
    PORT          ADK server port (default: 8090)
    APP           Agent app name (default: marketing_campaign_agent)
    USER_ID       Session user ID (default: user1)
    TURN_DELAY    Seconds to wait between turns (default: 3) — reduces 429s
    RETRY_MAX     Max retries on 429/5xx (default: 4)
    GEMINI_API_KEY / GOOGLE_API_KEY   API key for LLM turn generation
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Load .env from the script's directory before reading os.environ
# ---------------------------------------------------------------------------

def _load_dotenv(path: str) -> None:
    """Parse a .env file and inject variables into os.environ (no overwrite)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    # Minimal fallback: parse KEY=VALUE lines without python-dotenv
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


_load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

PORT = int(os.environ.get("PORT", "8090"))
APP = os.environ.get("APP", "marketing_campaign_agent")
USER_ID = os.environ.get("USER_ID", "user1")
BASE_URL = f"http://127.0.0.1:{PORT}"
TURN_DELAY = float(os.environ.get("TURN_DELAY", "3"))
RETRY_MAX = int(os.environ.get("RETRY_MAX", "4"))

# ---------------------------------------------------------------------------
# HTTP helpers with retry + exponential backoff
# ---------------------------------------------------------------------------

def _read_sse_events(raw: str) -> list[dict[str, Any]]:
    """Parse a Server-Sent Events (SSE) body into a list of JSON event objects.

    ADK's /run_sse endpoint emits lines like:
        data: {"author":"MarketResearcher","content":{...}}\n\n
    Each non-empty "data:" line is parsed as a JSON object and collected.
    Lines that are not valid JSON (e.g. "event: ping") are silently skipped.
    """
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            obj = json.loads(payload)
            if isinstance(obj, dict):
                events.append(obj)
        except json.JSONDecodeError:
            pass
    return events


def _http_post(path: str, body: dict[str, Any], retry_max: int = RETRY_MAX) -> list[dict[str, Any]]:
    """POST JSON to the ADK server, retrying on 429 / 5xx with exponential backoff.

    Supports both response modes:
    - application/json  — ADK /run endpoint returns a JSON array of events.
    - text/event-stream — ADK /run_sse endpoint streams SSE; each "data:" line
                          is one event object. Collected and returned as a list.

    Raises RuntimeError on unrecoverable errors.
    """
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode()
    delay = 5.0  # initial backoff in seconds

    for attempt in range(1, retry_max + 2):
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read().decode()
                if not raw.strip():
                    return []
                if "text/event-stream" in content_type:
                    return _read_sse_events(raw)
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else [parsed]
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt <= retry_max:
                print(
                    f"  [retry {attempt}/{retry_max}] HTTP {exc.code} — "
                    f"waiting {delay:.0f}s before retry..."
                )
                time.sleep(delay)
                delay = min(delay * 2, 120)  # cap at 2 minutes
                continue
            body_text = exc.read().decode()[:300]
            raise RuntimeError(f"HTTP {exc.code} from {path}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Connection error to {url}: {exc.reason}") from exc

    raise RuntimeError(f"Exceeded retry limit ({retry_max}) for {path}")


def _http_get(path: str) -> bool:
    """Return True when GET succeeds (used for server health check)."""
    url = f"{BASE_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status < 400
    except Exception:
        return False


# ---------------------------------------------------------------------------
# ADK API helpers
# ---------------------------------------------------------------------------

def wait_for_server(timeout: int = 30) -> None:
    """Block until the ADK server is reachable, or raise RuntimeError."""
    print(f"==> Waiting for ADK server at {BASE_URL} ...")
    for i in range(1, timeout + 1):
        if _http_get("/docs"):
            print("    Server ready.\n")
            return
        if i == timeout:
            raise RuntimeError(
                f"ADK server not reachable at {BASE_URL} after {timeout}s.\n"
                "  Start it first:  ./serve.sh"
            )
        time.sleep(1)


def create_session() -> str:
    """Create a new ADK session and return the session_id."""
    path = f"/apps/{APP}/users/{USER_ID}/sessions"
    events = _http_post(path, {})
    result = events[0] if events else {}
    session_id = result.get("id") or result.get("session_id")
    if not session_id:
        raise RuntimeError(f"No session id in response: {result}")
    return str(session_id)


def run_turn(session_id: str, message: str) -> tuple[str, list[str], list[str]]:
    """Send one message to the agent and return (full_text, tool_calls_used, agents_invoked).

    full_text is the concatenated text from all response parts.
    tool_calls_used is a list of tool names observed in the event stream.
    agents_invoked is a list of unique agent/author names seen in the event stream.
    """
    body = {
        "app_name": APP,
        "user_id": USER_ID,
        "session_id": session_id,
        "new_message": {
            "role": "user",
            "parts": [{"text": message}],
        },
    }
    event_list = _http_post("/run", body)

    text_parts: list[str] = []
    tool_calls: list[str] = []
    agents_seen: list[str] = []
    seen_agents_set: set[str] = set()
    for event in event_list:
        # Track which agent/sub-agent produced this event
        author = event.get("author") or ""
        if author and author not in seen_agents_set:
            seen_agents_set.add(author)
            agents_seen.append(author)
        # Extract text output from content.parts
        content = event.get("content") or {}
        for part in content.get("parts") or []:
            text = (part.get("text") or "").strip()
            if text:
                text_parts.append(text)
            # Track tool calls (function_call parts)
            fn = part.get("function_call")
            if fn and fn.get("name"):
                tool_calls.append(fn["name"])

    return "\n\n".join(text_parts), tool_calls, agents_seen


# ---------------------------------------------------------------------------
# LLM-generated follow-up turn
# ---------------------------------------------------------------------------

async def _generate_followup(agent_response: str) -> str:
    """Use nuguard LLMClient to generate a follow-up message from the agent's reply.

    The follow-up is a specific, actionable request that exercises one of the
    capabilities the agent described.  Falls back to a hard-coded message when
    no LLM key is configured.
    """
    # Import here so the script works even if nuguard is not on sys.path
    try:
        from nuguard.common.llm_client import LLMClient
    except ImportError:
        # Add project root to path if running from the test-app directory
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from nuguard.common.llm_client import LLMClient  # type: ignore[import]

    system = (
        "You are a QA tester writing a follow-up message to send to an AI marketing campaign agent. "
        "Your goal is to give the agent a complete, self-contained task it can immediately act on.\n"
        "Rules:\n"
        "- Include ALL necessary details inline: product name, target audience, campaign goal.\n"
        "- NEVER use placeholders like [X], [paste here], or [insert Y] — write real content.\n"
        "- The message must read like something a real user would send.\n"
        "Reply with ONLY the message text — no quotes, no explanation, no prefix."
    )
    prompt = (
        f"The agent described its capabilities as follows:\n\n"
        f"---\n{agent_response[:1500]}\n---\n\n"
        "Write a follow-up message (2-4 sentences) giving the agent a specific marketing campaign task. "
        "Include a concrete product, a clearly described target audience, and a campaign goal — "
        "all written out in full, with no placeholders. "
        "The task should exercise the full pipeline the agent described (research, messaging, ad copy, visuals)."
    )

    client = LLMClient()
    followup = await client.complete(prompt, system=system, label="test_api:turn2")
    followup = followup.strip()
    if not followup:
        followup = (
            "Please create a complete marketing campaign for an eco-friendly "
            "reusable water bottle with a built-in filter targeting outdoor enthusiasts."
        )
    return followup


# ---------------------------------------------------------------------------
# LLM response judge
# ---------------------------------------------------------------------------

async def _judge_response(
    user_request: str,
    agent_response: str,
    tools_used: list[str],
    agents_invoked: list[str],
) -> str:
    """Ask an LLM to evaluate whether the agent correctly fulfilled the user request.

    Returns a short verdict string with a pass/fail rating and reasoning.
    Falls back to a rule-based heuristic if no LLM key is available.
    """
    try:
        from nuguard.common.llm_client import LLMClient
    except ImportError:
        project_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from nuguard.common.llm_client import LLMClient  # type: ignore[import]

    tools_str = ", ".join(tools_used) if tools_used else "none"
    agents_str = ", ".join(agents_invoked) if agents_invoked else "none"

    system = (
        "You are a strict QA evaluator reviewing an AI agent's response. "
        "Reply with ONLY a JSON object — no markdown, no explanation."
    )
    prompt = f"""
## User request
{user_request}

## Agent final response (last 3000 chars — most recent output)
{agent_response[-3000:]}

## Observed execution
- Tools called: {tools_str}
- Sub-agents / authors: {agents_str}

## Evaluation task
Return a JSON object with these fields:
- "verdict": "PASS" or "FAIL"
- "score": integer 1-5 (5 = fully met the request)
- "reasoning": one sentence explaining the verdict
- "gaps": list of strings describing what was missing or incorrect (empty list if PASS)
""".strip()

    client = LLMClient()
    raw = await client.complete(prompt, system=system, label="test_api:judge")
    raw = raw.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    try:
        verdict = json.loads(raw)
        v = verdict.get("verdict", "?")
        score = verdict.get("score", "?")
        reasoning = verdict.get("reasoning", "")
        gaps = verdict.get("gaps") or []
        lines_out = [f"  Verdict : {v}  (score {score}/5)", f"  Reasoning: {reasoning}"]
        if gaps:
            lines_out.append("  Gaps    :")
            for g in gaps:
                lines_out.append(f"    • {g}")
        return "\n".join(lines_out)
    except (json.JSONDecodeError, KeyError):
        # Return raw text if JSON parsing fails
        return f"  Raw judge output:\n{raw[:500]}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print(f"Agent : {APP}")
    print(f"Server: {BASE_URL}")
    print()

    wait_for_server()

    # ── Create session ────────────────────────────────────────────────────────
    print("==> Creating session...")
    session_id = create_session()
    print(f"    session_id: {session_id}\n")

    # ── Turn 1: capability discovery ──────────────────────────────────────────
    turn1_message = "Hi! What can you help me with today? Please describe your capabilities."
    print(f"==> Turn 1 (sent): {turn1_message}")
    print()

    t0 = time.monotonic()
    turn1_text, turn1_tools, turn1_agents = run_turn(session_id, turn1_message)
    elapsed = time.monotonic() - t0

    print(f"--- Agent response (Turn 1, {elapsed:.1f}s) ---")
    print(turn1_text or "[no text response]")
    if turn1_tools:
        print(f"\n    Tools used: {', '.join(turn1_tools)}")
    if turn1_agents:
        print(f"    Agents/authors: {', '.join(turn1_agents)}")
    print()

    if not turn1_text:
        print("WARNING: Empty turn-1 response — skipping turn 2.")
        return

    # ── Generate Turn 2 from the agent's response ─────────────────────────────
    print("==> Generating Turn 2 message from agent response (via LLM)...")
    turn2_message = await _generate_followup(turn1_text)
    print(f"    Generated: {turn2_message}\n")

    # Pause between turns to reduce rate-limit pressure on the ADK server.
    if TURN_DELAY > 0:
        print(f"    (waiting {TURN_DELAY:.0f}s before next turn to avoid rate limits...)\n")
        time.sleep(TURN_DELAY)

    # ── Turn 2: generated task ────────────────────────────────────────────────
    print(f"==> Turn 2 (sent): {turn2_message}")
    print()

    t0 = time.monotonic()
    turn2_text, turn2_tools, turn2_agents = run_turn(session_id, turn2_message)
    elapsed = time.monotonic() - t0

    print(f"--- Agent response (Turn 2, {elapsed:.1f}s) ---")
    print(turn2_text or "[no text response]")
    if turn2_tools:
        print(f"\n    Tools used: {', '.join(turn2_tools)}")
    if turn2_agents:
        print(f"    Agents/authors: {', '.join(turn2_agents)}")
    print()

    # ── Validation: judge whether the agent fulfilled the request ─────────────
    all_tools = list(dict.fromkeys(turn1_tools + turn2_tools))  # deduplicated, ordered
    all_agents = list(dict.fromkeys(turn1_agents + turn2_agents))

    print("==> Validation")
    print(f"    Tools called   : {', '.join(all_tools) if all_tools else 'none'}")
    print(f"    Agents invoked : {', '.join(all_agents) if all_agents else 'none'}")
    print("    Judging response quality via LLM...")

    verdict_text = await _judge_response(
        user_request=turn2_message,
        agent_response=turn2_text or turn1_text,
        tools_used=all_tools,
        agents_invoked=all_agents,
    )
    print(verdict_text)
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
