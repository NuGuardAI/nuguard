"""LLM gap-fill discovery pass for AI SBOM extraction.

This module implements Step 0 of the LLM enrichment pipeline: a targeted
discovery pass that looks for component types that are absent or weakly
represented in the deterministic (AST + regex) results.

Algorithm
---------
1. Identify *absent* ComponentTypes (no nodes in the document) and
   *low-confidence* categories (all nodes < 0.65 confidence).
2. For each priority category, rank source files by the number of evidence
   hits that mention keywords associated with that category.
3. Build a focused prompt: existing node summary + snippets from the ranked
   files (≤ 300 lines per file, total ≤ 12 000 characters per call).
4. A single LLM call asks for JSON:
   ``[{"name", "confidence", "evidence_files", "detail", "canonical_name"}]``
5. Discovered nodes are capped at ``confidence=0.75`` (no AST backing) and
   tagged ``evidence_kind="llm_discovery"`` / ``source_tier="llm"``.
6. Category processing order: MODEL → DATASTORE → TOOL → PROMPT →
   AUTH → DEPLOYMENT.

Integration
-----------
Called from ``AiSbomExtractor._llm_enrich()`` **before** ``verify_uncertain_nodes``
so that newly discovered nodes enter the standard verification queue.

Example usage::

    gap_client = LLMClient(model=config.llm_model, api_key=config.llm_api_key,
                           budget_tokens=min(config.llm_budget_tokens // 3, 15_000))
    new_nodes = await discover_missing_nodes(doc, file_contents, gap_client)
    doc = apply_discovery_results(doc, new_nodes)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from xelo.models import AiSbomDocument, Evidence, Node, NodeMetadata
from xelo.models import SourceLocation
from xelo.types import ComponentType

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category configuration
# ---------------------------------------------------------------------------

# Categories checked in priority order (higher risk of being missed first)
_CATEGORY_ORDER: list[ComponentType] = [
    ComponentType.MODEL,
    ComponentType.DATASTORE,
    ComponentType.TOOL,
    ComponentType.PROMPT,
    ComponentType.AUTH,
    ComponentType.DEPLOYMENT,
    ComponentType.FRAMEWORK,
    ComponentType.PRIVILEGE,
]

# Per-category keyword sets used to rank files for inclusion in the prompt
_CATEGORY_KEYWORDS: dict[ComponentType, list[str]] = {
    ComponentType.MODEL: [
        "model",
        "llm",
        "gpt",
        "claude",
        "gemini",
        "llama",
        "mistral",
        "deepseek",
        "base_url",
        "api_key",
        "openai",
        "groq",
        "anthropic",
        "ollama",
    ],
    ComponentType.DATASTORE: [
        "database",
        "sqlite",
        "postgres",
        "mysql",
        "redis",
        "mongo",
        "aiosqlite",
        "sqlalchemy",
        "supabase",
        "dynamodb",
        "firestore",
        "collection",
        "connect",
        "cursor",
        "session",
        "table",
        "schema",
    ],
    ComponentType.TOOL: [
        "tool",
        "function_call",
        "playwright",
        "praw",
        "twikit",
        "telethon",
        "apscheduler",
        "celery",
        "requests",
        "httpx",
        "scrape",
        "browser",
        "api_call",
        "scheduler",
        "job",
        "task",
        # MCP tool decorators
        "@mcp.tool",
        "@server.tool",
        "fastmcp",
        "mcp.server",
        "mcp.tool",
    ],
    ComponentType.PROMPT: [
        "prompt",
        "system_message",
        "user_message",
        "template",
        "instruction",
        "few_shot",
        "persona",
        "context_window",
        "message_template",
    ],
    ComponentType.AUTH: [
        "auth",
        "jwt",
        "oauth",
        "api_key",
        "token",
        "password",
        "bcrypt",
        "passlib",
        "session",
        "cookie",
        "verify_password",
        "hash_password",
        # MCP auth providers
        "BearerAuthProvider",
        "OAuthProvider",
        "ClientCredentialsProvider",
        "OAuth2Bearer",
        "APIKeyAuth",
        "TokenAuth",
        "JWTAuth",
        "mcp_auth",
        "bearer_token",
    ],
    ComponentType.DEPLOYMENT: [
        "docker",
        "nginx",
        "gunicorn",
        "uvicorn",
        "deploy",
        "kubernetes",
        "helm",
        "terraform",
        "aws",
        "gcp",
        "azure",
        "server",
        "port",
        "host",
        # MCP HTTP transports
        "transport",
        "streamable-http",
        "mcp.run",
        "mcp.serve",
    ],
    ComponentType.FRAMEWORK: [
        # MCP / FastMCP
        "FastMCP",
        "fastmcp",
        "mcp.server",
        "mcp.server.fastmcp",
        "Server",
        "MCPServer",
        "model_context_protocol",
        "mcp",
        # Other AI orchestration frameworks
        "langgraph",
        "crewai",
        "autogen",
        "llamaindex",
        "langchain",
        "semantic_kernel",
        "openai",
        "anthropic",
        "haystack",
    ],
    ComponentType.PRIVILEGE: [
        # RBAC / access control
        "rbac",
        "has_permission",
        "require_permission",
        "assign_role",
        "access_control",
        "least_privilege",
        # Admin / superuser
        "sudo",
        "superuser",
        "is_superuser",
        "is_admin",
        "setuid",
        "elevate",
        # Filesystem write
        "FileWriteTool",
        "os.remove",
        "shutil.move",
        "write_text",
        # DB write
        "session.add",
        "INSERT INTO",
        "UPDATE.*SET",
        "DELETE FROM",
        "bulk_create",
        # Email out
        "smtplib",
        "sendgrid",
        "ses.send_email",
        "send_email",
        # Social media out
        "tweepy",
        "praw",
        "discord",
        "telegram",
        "slack_sdk",
        # Code execution / shell
        "subprocess",
        "BashTool",
        "ShellTool",
        "E2BSandbox",
        "shell=True",
        "os.system",
        # Network out
        "requests.post",
        "httpx.post",
        "webhook",
    ],
}

# Maximum snippet characters sent to LLM per gap-fill category
_MAX_SNIPPET_CHARS = 12_000
# Maximum lines read per file when building snippets
_MAX_LINES_PER_FILE = 300
# Confidence cap for LLM-discovered nodes (no structural backing)
_DISCOVERY_CONFIDENCE_CAP = 0.75
# Minimum confidence threshold below which a discovery result is ignored.
# Raised from 0.40 → 0.60 to reduce speculative false positives (P3).
_MIN_ACCEPTED_CONFIDENCE = 0.60

# ---------------------------------------------------------------------------
# Gap-fill source-file filter (P1)
# ---------------------------------------------------------------------------
# Documentation, test, and deploy-guide files consistently produce false
# positives because the LLM reads *descriptive* mentions of tools/services
# as actual component detections.  Only code files are sent as context.

_GAP_FILL_SKIP_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".rst",
        ".txt",
        ".adoc",
        ".html",
        ".htm",
    }
)

_GAP_FILL_SKIP_STEMS: frozenset[str] = frozenset(
    {
        "readme",
        "changelog",
        "license",
        "contributing",
        "roadmap",
        "deployment-guide",
        "deployment_guide",
        "install",
        "setup",
        "getting-started",
        "getting_started",
        "deploy",
        # Prompt/template source files — contain string variables whose names
        # look like tool/capability names but are not registered AI tools (P6)
        "prompts",
        "system_prompts",
        "prompt_templates",
        "instructions",
        "instruction_templates",
        "templates",
        "message_templates",
    }
)

_GAP_FILL_SKIP_PATH_PARTS: frozenset[str] = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "tests_integ",
        "test_toolbox",
        "docs",
        "doc",
        "documentation",
        "notebooks",
        "examples",
        "samples",
        "benchmark",
        "benchmarks",
        "evals",
    }
)


def _is_gap_fill_source_file(path: str) -> bool:
    """Return True only if *path* is a code file suitable for gap-fill context.

    Excludes documentation (\.md, \*.rst …), test directories, example
    directories, and deployment-guide files whose descriptive prose consistently
    causes the LLM to hallucinate components that are only *mentioned*, not
    *used*, in the codebase.
    """
    p = Path(path)
    stem = p.stem.lower()
    suffix = p.suffix.lower()
    parts = {part.lower() for part in p.parts}

    if suffix in _GAP_FILL_SKIP_EXTENSIONS:
        return False
    # Match README-like stems and anything with deploy/install in the name
    if stem in _GAP_FILL_SKIP_STEMS:
        return False
    if "readme" in stem or "deploy" in stem or "install" in stem:
        return False
    if parts & _GAP_FILL_SKIP_PATH_PARTS:
        return False
    return True


# ---------------------------------------------------------------------------
# Per-type gap-fill gating (P2)
# ---------------------------------------------------------------------------
# Categories that are NEVER gap-filled:
#   AGENT     — 97% recall without LLM; gap-fill adds 0 TPs, 0 FPs historically
#   GUARDRAIL — 100% recall without LLM; gap-fill has no benefit
#   PRIVILEGE — 16% precision without LLM already; gap-fill adds ~10 FPs per run
_GAP_FILL_NEVER: frozenset[ComponentType] = frozenset(
    {
        ComponentType.AGENT,
        ComponentType.GUARDRAIL,
        ComponentType.PRIVILEGE,
    }
)

# Categories gap-filled only when truly absent (zero nodes of that type).
# If the regex/AST pass already found ≥1 node, skip gap-fill — these types
# have ≥94% recall from deterministic extraction alone and gap-fill only
# introduces false positives from description files.
_GAP_FILL_ONLY_IF_ABSENT: frozenset[ComponentType] = frozenset(
    {
        ComponentType.AUTH,  # 100% recall without LLM
        ComponentType.TOOL,  # 94% recall without LLM; LLM was adding +20 FPs
        ComponentType.PROMPT,  # 96% recall without LLM; LLM was adding +5 FPs
    }
)

# Dev / build tools that are NOT AI SBOM components — excluded from TOOL gap-fill
_TOOL_BLOCKLIST: frozenset[str] = frozenset(
    {
        "vite",
        "eslint",
        "prettier",
        "webpack",
        "babel",
        "jest",
        "tsc",
        "mypy",
        "ruff",
        "npm",
        "yarn",
        "pip",
        "docker",
        "git",
        "make",
        "rollup",
        "parcel",
        "turbo",
        "vitest",
        "mocha",
        "chai",
        "pytest",
        "black",
        "isort",
        "flake8",
        "pylint",
        "husky",
        "lint-staged",
        "typescript",
        "node",
        "bun",
        "pnpm",
        "sass",
        "tailwind",
        "postcss",
        "nodemon",
        "ts-node",
        "pm2",
    }
)

# Short category description injected into the LLM prompt
_CATEGORY_DESCRIPTIONS: dict[ComponentType, str] = {
    ComponentType.MODEL: "AI/ML models (LLM, embedding, speech, vision, etc.)",
    ComponentType.DATASTORE: "Databases, caches, vector stores, file stores, memory backends",
    ComponentType.TOOL: (
        "AI/agent tools ONLY — functions or capabilities registered with an LLM or used by an AI agent: "
        "external API calls made BY agent code, browser/scraping automation (playwright, selenium), "
        "social-media clients (praw, twikit, telethon), function-calling tools, "
        "scheduled tasks driven by agent logic (celery, apscheduler). "
        "EXCLUDE build/dev tooling (vite, eslint, prettier, webpack, babel, jest, tsc, "
        "mypy, ruff, npm, yarn, docker, git, etc.) — those are not AI components."
    ),
    ComponentType.PROMPT: "Prompt templates, system messages, instruction files",
    ComponentType.AUTH: "Authentication, authorisation, credentials, session management",
    ComponentType.DEPLOYMENT: "Deployment targets, reverse proxies, container orchestration",
    ComponentType.FRAMEWORK: (
        "AI orchestration frameworks or MCP server instances — FastMCP / mcp.server.fastmcp "
        "instantiations, LangChain, LangGraph, CrewAI, AutoGen, LlamaIndex, Semantic Kernel, "
        "or any other AI framework that orchestrates models, tools, or agents."
    ),
    ComponentType.PRIVILEGE: (
        "Privileged capabilities exercised by the AI agent or application — one or more of: "
        "RBAC / role-based access control and permission checks (rbac, has_permission, assign_role); "
        "admin/superuser escalation (sudo, is_superuser, setuid, elevate); "
        "filesystem write/delete operations (open w/a mode, os.remove, shutil.move, FileWriteTool); "
        "database write operations (INSERT/UPDATE/DELETE, session.add, bulk_create); "
        "outbound email (smtplib, sendgrid, ses.send_email); "
        "outbound social-media messaging (tweepy, praw, discord, telegram, slack_sdk); "
        "shell / code execution (subprocess, os.system, BashTool, ShellTool, E2BSandbox, shell=True); "
        "outbound HTTP write calls (requests.post, httpx.post, webhook dispatch)."
    ),
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def discover_missing_nodes(
    doc: AiSbomDocument,
    file_contents: dict[str, str],
    llm_client: Any,  # LLMClient — typed as Any to avoid circular import
    *,
    budget_tokens: int | None = None,
) -> list[Node]:
    """Run a gap-fill LLM pass and return newly discovered nodes.

    Parameters
    ----------
    doc:
        The current (deterministic) document.  No mutation occurs here.
    file_contents:
        Mapping of relative file path → file text (as assembled by the extractor).
    llm_client:
        Instantiated ``LLMClient``.
    budget_tokens:
        If provided, the client's budget is virtually bounded to this value.
        Pass ``min(config.llm_budget_tokens // 3, 15_000)`` from the caller.

    Returns
    -------
    list[Node]
        New nodes not yet present in *doc*.  Caller should pass them to
        :func:`apply_discovery_results`.
    """
    absent_categories = _identify_absent_categories(doc)
    if not absent_categories:
        _log.debug("gap-fill: all priority categories present — skipping")
        return []

    _log.info("gap-fill: absent/weak categories: %s", [c.value for c in absent_categories])

    existing_summary = _build_existing_node_summary(doc)
    new_nodes: list[Node] = []
    existing_canonical: set[str] = {
        str(n.metadata.extras.get("canonical_name", n.name)).lower() for n in doc.nodes
    }

    for category in _CATEGORY_ORDER:
        if category not in absent_categories:
            continue

        if budget_tokens is not None and llm_client.tokens_used >= budget_tokens:
            _log.info("gap-fill: token budget exhausted — stopping early")
            break

        snippets = _build_file_snippets(category, file_contents)
        if not snippets:
            _log.debug("gap-fill: no relevant files for category=%s", category.value)
            continue

        try:
            raw_results = await _call_gap_fill_llm(category, existing_summary, snippets, llm_client)
        except Exception as exc:  # pragma: no cover
            _log.warning("gap-fill: LLM call failed for %s: %s", category.value, exc)
            continue

        for item in raw_results:
            # Block dev/build tools before creating a node
            if category == ComponentType.TOOL:
                candidate_name = str(item.get("canonical_name") or item.get("name") or "").lower()
                if candidate_name in _TOOL_BLOCKLIST or any(
                    blocked in candidate_name for blocked in _TOOL_BLOCKLIST
                ):
                    _log.debug("gap-fill: blocking dev-tool %r", candidate_name)
                    continue

            node = _result_to_node(item, category)
            if node is None:
                continue
            canon = str(node.metadata.extras.get("canonical_name", node.name)).lower()
            if canon in existing_canonical:
                _log.debug("gap-fill: skipping duplicate %r", canon)
                continue
            existing_canonical.add(canon)
            new_nodes.append(node)
            _log.info(
                "gap-fill: discovered new %s node %r (confidence=%.2f)",
                category.value,
                node.name,
                node.confidence,
            )

    _log.info(
        "gap-fill: %d new node(s) discovered across %d categories",
        len(new_nodes),
        len(absent_categories),
    )
    return new_nodes


def apply_discovery_results(
    doc: AiSbomDocument,
    new_nodes: list[Node],
) -> AiSbomDocument:
    """Merge *new_nodes* into *doc* and return the updated document.

    Existing nodes are never overwritten.  The function updates
    ``doc.summary.node_counts`` to include the new nodes.
    """
    if not new_nodes:
        return doc

    doc.nodes.extend(new_nodes)

    # Refresh node_counts in summary
    if doc.summary:
        counts: dict[str, int] = {}
        for node in doc.nodes:
            key = node.component_type.value
            counts[key] = counts.get(key, 0) + 1
        doc.summary.node_counts = counts

    return doc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _identify_absent_categories(doc: AiSbomDocument) -> list[ComponentType]:
    """Return priority categories where gap-fill should run.

    Applies three gating rules (see P2 in accuracy improvement plan):

    1. *Never* gap-fill AGENT, GUARDRAIL, PRIVILEGE — these have either
       ≥97% deterministic recall or pre-existing precision problems.
    2. *Skip* AUTH, TOOL, PROMPT if the deterministic pass found ≥1 node —
       those types have ≥94% recall; gap-fill only adds FPs.
    3. For all other categories: gap-fill when no node exceeds 0.65 confidence
       (original behaviour).
    """
    present_types: dict[ComponentType, float] = {}
    type_counts: dict[ComponentType, int] = {}
    for node in doc.nodes:
        ct = node.component_type
        type_counts[ct] = type_counts.get(ct, 0) + 1
        if ct not in present_types or node.confidence > present_types[ct]:
            present_types[ct] = node.confidence

    absent: list[ComponentType] = []
    for category in _CATEGORY_ORDER:
        # Rule 1: never gap-fill these types
        if category in _GAP_FILL_NEVER:
            continue
        # Rule 2: skip high-recall types if already found
        if category in _GAP_FILL_ONLY_IF_ABSENT and type_counts.get(category, 0) > 0:
            continue
        # Rule 3: original threshold
        max_conf = present_types.get(category, 0.0)
        if max_conf < 0.65:
            absent.append(category)
    return absent


def _build_existing_node_summary(doc: AiSbomDocument) -> str:
    """Build a compact text summary of already-detected nodes for the prompt."""
    if not doc.nodes:
        return "(no nodes detected yet)"
    lines: list[str] = []
    for node in doc.nodes[:50]:  # Cap at 50 to keep prompt short
        lines.append(
            f"- [{node.component_type.value}] {node.name} (confidence={node.confidence:.2f})"
        )
    if len(doc.nodes) > 50:
        lines.append(f"  ... and {len(doc.nodes) - 50} more")
    return "\n".join(lines)


def _score_file_for_category(content: str, keywords: list[str], rel_path: str) -> int:
    """Return a keyword-hit score for *content* (used to rank files)."""
    text_lower = content.lower()
    score = sum(text_lower.count(kw) for kw in keywords)
    # Slight boost for keywords appearing in the file path itself
    path_lower = rel_path.lower()
    score += sum(3 for kw in keywords if kw in path_lower)
    return score


def _build_file_snippets(
    category: ComponentType,
    file_contents: dict[str, str],
) -> str:
    """Return a single concatenated snippet string for the LLM prompt."""
    keywords = _CATEGORY_KEYWORDS.get(category, [])
    if not keywords:
        return ""

    # Score and rank files — only consider code files (P1: exclude docs/tests)
    scored: list[tuple[int, str, str]] = []
    for path, content in file_contents.items():
        if not _is_gap_fill_source_file(path):
            continue
        score = _score_file_for_category(content, keywords, path)
        if score > 0:
            scored.append((score, path, content))

    scored.sort(key=lambda t: t[0], reverse=True)

    # Build snippet string within character budget
    parts: list[str] = []
    total_chars = 0
    for _, path, content in scored:
        if total_chars >= _MAX_SNIPPET_CHARS:
            break
        lines = content.splitlines()[:_MAX_LINES_PER_FILE]
        snippet_text = "\n".join(lines)
        # Trim to remaining budget
        remaining = _MAX_SNIPPET_CHARS - total_chars
        if len(snippet_text) > remaining:
            snippet_text = snippet_text[:remaining] + "\n...(truncated)"
        parts.append(f"### {path}\n{snippet_text}")
        total_chars += len(snippet_text)

    return "\n\n".join(parts)


_SYSTEM_PROMPT = """\
You are an AI component detection assistant.
You will be given:
1. A summary of AI components already detected in a codebase.
2. Relevant source file snippets.
3. A target component category to look for.

Your job is to identify ONLY components of the target category that are NOT
already in the existing summary.

Return a JSON array of objects.  Each object must have:
  "name"            — display name (string)
  "canonical_name"  — lowercase slug form, e.g. "gpt-4o-mini" or "redis"
  "confidence"      — float 0.0–1.0 (be conservative; cap at 0.75 for uncertain finds)
  "detail"          — one-sentence justification referencing the file and a code snippet
  "evidence_files"  — list of relative file paths supporting this detection

Return an empty array [] if you find nothing new.
Return ONLY the JSON array — no prose, no markdown, no code fences.
"""


async def _call_gap_fill_llm(
    category: ComponentType,
    existing_summary: str,
    snippets: str,
    client: Any,
) -> list[dict[str, Any]]:
    """Make one focused LLM call and return parsed discovery results."""
    category_desc = _CATEGORY_DESCRIPTIONS.get(category, category.value)
    extra_guidance = ""
    if category == ComponentType.FRAMEWORK:
        extra_guidance = (
            "\n\nFor MCP server instances (FastMCP / mcp.server.fastmcp):\n"
            "- Set \"name\" to the server display name passed to FastMCP() or 'mcp-server' if unknown.\n"
            '- In "detail", write a SHORT description: e.g. '
            '\'MCP server "my-server" exposing tools: <tool1>, <tool2>; '
            "transport: streamable-http; auth: BearerAuthProvider'.\n"
            '- Set canonical_name to the snake_case server name prefixed with "mcp:", '
            'e.g. "mcp:my-server".\n'
            'If it is a different framework (LangGraph, CrewAI, etc.) describe it in "detail" likewise.'
        )
    elif category == ComponentType.PRIVILEGE:
        extra_guidance = (
            "\n\nFor PRIVILEGE nodes use one of these canonical_name values exactly:\n"
            '  "privilege:rbac"              — RBAC / permission checks / role assignment\n'
            '  "privilege:admin"             — sudo / superuser / admin escalation\n'
            '  "privilege:filesystem_write"  — file write, delete, or move operations\n'
            '  "privilege:db_write"          — database INSERT / UPDATE / DELETE / ORM write calls\n'
            '  "privilege:email_out"         — outbound email (smtplib, SendGrid, SES, etc.)\n'
            '  "privilege:social_media_out"  — posts to Twitter/X, Reddit, Discord, Telegram, Slack\n'
            '  "privilege:code_execution"    — subprocess, os.system, BashTool, E2BSandbox, shell=True\n'
            '  "privilege:network_out"       — outbound HTTP POST/PUT/PATCH, webhooks\n'
            'Set "name" to the human-readable privilege class (e.g. "Filesystem Write").\n'
            'In "detail" reference the specific function/class/pattern you found.'
        )
    user_prompt = (
        f"## Already-detected components\n{existing_summary}\n\n"
        f"## Target category: {category.value}\n"
        f"Description: {category_desc}{extra_guidance}\n\n"
        f"## Source code snippets\n{snippets}\n\n"
        f"Find any {category.value} components NOT listed above and return JSON."
    )

    raw_text, tokens = await client.complete_text(_SYSTEM_PROMPT, user_prompt)
    _log.debug("gap-fill[%s]: %d tokens used", category.value, tokens)

    # Strip markdown code fences if present
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(ln for ln in lines if not ln.startswith("```"))

    # Find the JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        _log.debug("gap-fill[%s]: no JSON array in response: %r", category.value, raw_text[:200])
        return []

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        _log.warning("gap-fill[%s]: JSON parse error: %s", category.value, exc)
        return []

    if not isinstance(parsed, list):
        return []

    # Shallow validation
    valid: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        if not item.get("name"):
            continue
        conf = float(item.get("confidence", 0.0))
        if conf < _MIN_ACCEPTED_CONFIDENCE:
            continue
        valid.append(item)

    return valid


def _result_to_node(item: dict[str, Any], category: ComponentType) -> Node | None:
    """Convert a raw LLM discovery dict to a ``Node``."""
    try:
        name = str(item["name"]).strip()
        canonical = str(item.get("canonical_name") or name).lower().strip()
        confidence = min(
            _DISCOVERY_CONFIDENCE_CAP,
            float(item.get("confidence", 0.5)),
        )
        detail = str(item.get("detail") or f"llm_discovery: {name}")[:200]
        evidence_files: list[str] = [str(f) for f in (item.get("evidence_files") or [])]

        primary_file = evidence_files[0] if evidence_files else "unknown"
        evidence = Evidence(
            kind="llm_discovery",
            confidence=confidence,
            detail=detail,
            location=SourceLocation(path=primary_file, line=None),
        )

        node = Node(
            name=name,
            component_type=category,
            confidence=confidence,
            metadata=NodeMetadata(),
            evidence=[evidence],
        )
        node.metadata.extras["canonical_name"] = canonical
        node.metadata.extras["adapter"] = "gap_fill"
        node.metadata.extras["evidence_files"] = evidence_files
        node.metadata.extras["source_tier"] = "llm"
        # Persist the LLM-generated one-sentence description for later use
        # (e.g. asset summary, use-case refinement, serialization).
        if detail and detail != f"llm_discovery: {name}":
            node.metadata.extras["description"] = detail
        if category == ComponentType.FRAMEWORK and "framework" not in node.metadata.extras:
            node.metadata.framework = canonical

        return node
    except (KeyError, ValueError, TypeError) as exc:
        _log.debug("gap-fill: invalid result item %r: %s", item, exc)
        return None
