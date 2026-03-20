"""
Multi-Pass Verification for Uncertain AIBOM Node Detections

Implements second-pass verification for nodes detected with confidence
in the uncertain zone (0.60–0.85). A focused LLM call verifies each detection.

Key Features:
- Cost-aware: stops when budget exceeded
- Configurable: can be disabled via environment variable
- Caches results for identical code patterns
- Works with xelo.models.Node (standalone, no backend dependency)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from uuid import UUID

from xelo.models import Evidence, Node

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

ENABLE_VERIFICATION = os.environ.get("AISBOM_ENABLE_VERIFICATION", "true").lower() == "true"
VERIFICATION_CONFIDENCE_MIN = float(os.environ.get("AISBOM_VERIFICATION_CONFIDENCE_MIN", "0.60"))
VERIFICATION_CONFIDENCE_MAX = float(os.environ.get("AISBOM_VERIFICATION_CONFIDENCE_MAX", "0.85"))
VERIFICATION_COST_BUDGET = float(os.environ.get("AISBOM_VERIFICATION_COST_BUDGET", "0.05"))
MAX_VERIFICATIONS_PER_SCAN = int(os.environ.get("AISBOM_MAX_VERIFICATIONS", "20"))

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class VerificationResult:
    """Result of verifying a single AIBOM node detection."""

    node_id: UUID
    original_name: str
    verified: bool
    original_confidence: float
    new_confidence: float
    reason: str
    refined_metadata: dict[str, Any] = field(default_factory=dict)
    verification_cost: float = 0.0


@dataclass
class VerificationStats:
    """Statistics from a verification pass."""

    total_candidates: int = 0
    verified_count: int = 0
    rejected_count: int = 0
    skipped_count: int = 0
    total_cost: float = 0.0
    budget_exceeded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_candidates": self.total_candidates,
            "verified_count": self.verified_count,
            "rejected_count": self.rejected_count,
            "skipped_count": self.skipped_count,
            "total_cost": self.total_cost,
            "budget_exceeded": self.budget_exceeded,
        }


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an expert code reviewer verifying AI asset detections for AI Bill of Materials (AIBOM).

Your job is to confirm or reject potential AI-related nodes found in source code.
Be precise and conservative — only verify assets that are clearly production code.

AIBOM Node Types you may encounter:
- AGENT: AI agent that can take actions autonomously
- MODEL: LLM or ML model (e.g., gpt-4, claude-3, text-embedding-3-large)
- TOOL: Function or capability available to an agent (including MCP @server.tool() decorated functions)
- PROMPT: Prompt template or system instruction
- DATASTORE: Vector database, knowledge base, or memory system
- GUARDRAIL: Input/output filter or safety mechanism
- AUTH: Authentication or authorization configuration (including MCP BearerAuthProvider, OAuthProvider, etc.)
- PRIVILEGE: Privileged capability exercised by the agent — rbac/permission check, admin/superuser escalation, filesystem write/delete, DB write (INSERT/UPDATE/DELETE/ORM .save()/.add()), outbound email (smtplib, sendgrid, SES), social-media post (tweepy, praw, discord, telegram, slack_sdk), code/shell execution (subprocess, BashTool, ShellTool, E2BSandbox, shell=True), or outbound HTTP write (requests.post, httpx.post, webhook)
- FRAMEWORK: AI orchestration framework or MCP server (e.g., FastMCP, LangChain, CrewAI, AutoGen)
- API_ENDPOINT: Network endpoint exposed by the application (e.g., MCP .run(transport="sse"), HTTP server)"""

_USER_PROMPT_TEMPLATE = """## AIBOM Node to Verify

- **Type**: {node_type}
- **Name**: {node_name}
- **File**: {file_path}
- **Line**: {line_number}
- **Initial Confidence**: {initial_confidence:.2f}
- **Detection Method**: {detection_method}
- **Framework**: {framework}

## Code Context

```{language}
{context}
```

## Verification Criteria

Determine if this is a REAL AI-related node that would exist in production:

### REJECT if:
1. This is in a test file or test function (test_*, *_test.py, conftest.py)
2. This is a mock, stub, or fixture for testing
3. This is in a comment, docstring, or documentation
4. This is an abstract base class without concrete instantiation
5. This is example code in a README or docs folder
6. This is an import statement without actual usage
7. This is a false positive (e.g., "agent" meaning insurance agent, not AI agent)

### VERIFY if:
1. This is a concrete instantiation that will run in production
2. This is defined in application code (not tests)
3. This has actual AI/ML functionality
4. This matches the claimed node type

## Your Response

Respond ONLY with valid JSON (no markdown, no explanation):
{{
  "verified": true or false,
  "confidence": 0.0-1.0,
  "reason": "Brief explanation",
  "refined_name": "Corrected name if wrong (or null)",
  "refined_type": "Corrected node type if misclassified (or null)",
  "description": "What this node does (if verified)"
}}"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Type alias for LLM call function — matches LLMClient.complete_text signature
LLMCallFn = Callable[[str, str], Awaitable[tuple[str, int]]]


def _detect_language(file_path: str) -> str:
    if not file_path:
        return "python"
    lower = file_path.lower()
    if lower.endswith((".ts", ".tsx")):
        return "typescript"
    if lower.endswith((".js", ".jsx", ".mjs")):
        return "javascript"
    if lower.endswith((".yaml", ".yml")):
        return "yaml"
    if lower.endswith(".tf"):
        return "hcl"
    if lower.endswith(".json"):
        return "json"
    return "python"


def _extract_context(file_content: str, line_number: int, context_lines: int = 20) -> str:
    if not file_content or line_number <= 0:
        return "(context not available)"
    lines = file_content.splitlines()
    start = max(0, line_number - context_lines // 2 - 1)
    end = min(len(lines), line_number + context_lines // 2)
    return "\n".join(lines[start:end])


@lru_cache(maxsize=1000)
def _context_hash(context: str) -> str:
    return hashlib.md5(context.encode()).hexdigest()[:16]  # noqa: S324


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def should_verify_node(
    confidence: float,
    min_threshold: float = VERIFICATION_CONFIDENCE_MIN,
    max_threshold: float = VERIFICATION_CONFIDENCE_MAX,
) -> bool:
    """Return True if the node falls in the uncertain confidence zone."""
    return min_threshold <= confidence <= max_threshold


def filter_verification_candidates(
    nodes: list[Node],
    max_candidates: int = MAX_VERIFICATIONS_PER_SCAN,
) -> tuple[list[Node], int]:
    """Filter nodes to find verification candidates.

    Returns ``(candidates, skipped_count)``.
    """
    candidates = [n for n in nodes if should_verify_node(n.confidence)]
    skipped = len(nodes) - len(candidates)
    candidates.sort(key=lambda n: n.confidence)
    if len(candidates) > max_candidates:
        skipped += len(candidates) - max_candidates
        candidates = candidates[:max_candidates]
    return candidates, skipped


def build_verification_prompt(
    node: Node,
    evidence_list: list[Evidence],
    file_content: str | None = None,
    context_lines: int = 20,
) -> tuple[str, str]:
    """Build verification prompt for a single node.

    Returns ``(system_prompt, user_prompt)``.
    """
    file_path = ""
    line_number = 0
    snippet = ""

    if evidence_list:
        first = evidence_list[0]
        file_path = first.location.path if first.location else ""
        line_number = first.location.line or 0 if first.location else 0
        snippet = first.detail or ""

    context = snippet
    if file_content and line_number > 0:
        context = _extract_context(file_content, line_number, context_lines)
    if not context:
        context = "(context not available)"

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        node_type=node.component_type.value,
        node_name=node.name,
        file_path=file_path,
        line_number=line_number,
        initial_confidence=node.confidence,
        detection_method=node.metadata.extras.get("evidence_kind", "pattern matching"),
        framework=node.metadata.framework or node.metadata.extras.get("framework") or "unknown",
        language=_detect_language(file_path),
        context=context,
    )
    return _SYSTEM_PROMPT, user_prompt


def parse_verification_response(
    response: str,
    original_node: Node,
) -> VerificationResult:
    """Parse LLM verification response into a VerificationResult."""
    raw = response.strip()
    if raw.startswith("```"):
        raw = "\n".join(ln for ln in raw.splitlines() if not ln.startswith("```"))
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
        return VerificationResult(
            node_id=original_node.id,
            original_name=original_node.name,
            verified=bool(data.get("verified", False)),
            original_confidence=original_node.confidence,
            new_confidence=float(data.get("confidence", 0.5)),
            reason=str(data.get("reason", "")),
            refined_metadata={
                "description": data.get("description", ""),
                "refined_type": data.get("refined_type"),
                "verified_by": "llm_verification",
            },
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return VerificationResult(
            node_id=original_node.id,
            original_name=original_node.name,
            verified=False,
            original_confidence=original_node.confidence,
            new_confidence=0.4,
            reason=f"Failed to parse verification response: {exc}",
            refined_metadata={"parse_error": True},
        )


def apply_verification_results(
    nodes: list[Node],
    results: list[VerificationResult],
) -> list[Node]:
    """Apply verification results: update confidence, soft-reject or drop nodes.

    Rejection policy
    ----------------
    - **LLM-discovered** nodes (``source_tier="llm"`` / ``adapter="gap_fill"``)
      are *fully dropped* when rejected — they have no structural backing.
    - **Deterministic** nodes (AST / regex) are *soft-rejected*: their confidence
      is reduced to 0.55 (below the verification floor) so they remain in the SBOM
      but are flagged.  Dropping a deterministic node on an uncertain LLM verdict
      trades away recall for no precision gain.
    """
    by_id = {r.node_id: r for r in results}
    by_name = {r.original_name: r for r in results}
    updated: list[Node] = []
    for node in nodes:
        result = by_id.get(node.id) or by_name.get(node.name)
        if result:
            if result.verified:
                node.confidence = result.new_confidence
                if result.refined_metadata.get("description"):
                    node.metadata.extras["description"] = result.refined_metadata["description"]
                node.metadata.extras["llm_verified"] = True
                node.metadata.extras["llm_verification_reason"] = result.reason
                node.metadata.extras["llm_confidence"] = result.new_confidence
                updated.append(node)
            else:
                # Only fully drop nodes that were LLM-discovered (no structural backing)
                is_llm_discovered = (
                    node.metadata.extras.get("source_tier") == "llm"
                    or node.metadata.extras.get("adapter") == "gap_fill"
                )
                if is_llm_discovered:
                    _log.debug(
                        "apply_verification: dropping llm_discovery node %r (rejected)",
                        node.name,
                    )
                    # Dropped — not appended
                else:
                    # Soft-reject: keep but push confidence below verification floor
                    node.confidence = min(node.confidence, 0.55)
                    node.metadata.extras["llm_soft_rejected"] = True
                    node.metadata.extras["llm_verification_reason"] = result.reason
                    _log.debug(
                        "apply_verification: soft-reject deterministic node %r → conf=0.55",
                        node.name,
                    )
                    updated.append(node)
        else:
            updated.append(node)
    return updated


async def verify_uncertain_nodes(
    nodes: list[Node],
    evidence_map: dict[UUID, list[Evidence]],
    llm_call_fn: LLMCallFn,
    file_contents: dict[str, str] | None = None,
    cost_budget: float = VERIFICATION_COST_BUDGET,
    enabled: bool = ENABLE_VERIFICATION,
) -> tuple[list[VerificationResult], VerificationStats]:
    """Verify AIBOM nodes in the uncertain confidence zone.

    Parameters
    ----------
    nodes:
        All extracted nodes.
    evidence_map:
        Mapping from ``node.id`` to the list of ``Evidence`` objects for that node.
    llm_call_fn:
        Async callable ``(system_prompt, user_prompt) -> (response_text, tokens)``.
        Matches the signature of ``LLMClient.complete_text``.
    file_contents:
        Optional mapping of relative file path → file content for richer context.
    cost_budget:
        Maximum cost (USD) to spend; stops early if exceeded.
    enabled:
        Master switch — returns empty results immediately when False.
    """
    stats = VerificationStats()
    if not enabled:
        stats.skipped_count = len(nodes)
        return [], stats

    candidates, skipped = filter_verification_candidates(nodes)
    stats.total_candidates = len(candidates)
    stats.skipped_count = skipped

    if not candidates:
        return [], stats

    results: list[VerificationResult] = []
    cost_used = 0.0
    cost_per_call = 0.001  # estimated cost per call

    for node in candidates:
        if cost_used + cost_per_call > cost_budget:
            stats.budget_exceeded = True
            stats.skipped_count += len(candidates) - len(results)
            break

        evidence_list = evidence_map.get(node.id, [])
        file_path = (
            evidence_list[0].location.path if evidence_list and evidence_list[0].location else ""
        )
        file_content = (file_contents or {}).get(file_path)

        system_prompt, user_prompt = build_verification_prompt(node, evidence_list, file_content)

        try:
            response, tokens = await llm_call_fn(system_prompt, user_prompt)
            actual_cost = tokens * 0.00001
            cost_used += actual_cost
            result = parse_verification_response(response, node)
            result.verification_cost = actual_cost
            results.append(result)
            if result.verified:
                stats.verified_count += 1
            else:
                stats.rejected_count += 1
        except Exception as exc:  # noqa: BLE001
            # On API/network failure, skip verification entirely — node keeps
            # its original confidence rather than being incorrectly rejected.
            _log.warning("Verification skipped for node %r: %s", node.name, exc)
            stats.skipped_count += 1

    stats.total_cost = cost_used
    return results, stats


def get_verification_config() -> dict[str, Any]:
    """Return current verification configuration."""
    return {
        "enabled": ENABLE_VERIFICATION,
        "confidence_min": VERIFICATION_CONFIDENCE_MIN,
        "confidence_max": VERIFICATION_CONFIDENCE_MAX,
        "cost_budget": VERIFICATION_COST_BUDGET,
        "max_verifications": MAX_VERIFICATIONS_PER_SCAN,
    }
