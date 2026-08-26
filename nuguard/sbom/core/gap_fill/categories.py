"""Category configuration for the LLM gap-fill discovery pass.

Pure data: which component categories gap-fill considers, the keywords used
to rank source files for each, human-readable descriptions injected into
LLM prompts, and the gating sets that control when each category runs.
"""

from __future__ import annotations

from ...types import ComponentType

# Categories checked in priority order (higher risk of being missed first).
# PRIVILEGE and GUARDRAIL are included here (unlike the pre-refactor module)
# but are vetoed by default via config — see gating.py / rounds.py.
_CATEGORY_ORDER: list[ComponentType] = [
    ComponentType.MODEL,
    ComponentType.DATASTORE,
    ComponentType.TOOL,
    ComponentType.PROMPT,
    ComponentType.AUTH,
    ComponentType.DEPLOYMENT,
    ComponentType.FRAMEWORK,
    ComponentType.GUARDRAIL,
    ComponentType.PRIVILEGE,
    ComponentType.API_ENDPOINT,
    ComponentType.AGENT,
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
    ComponentType.API_ENDPOINT: [
        # NestJS / Express / Fastify / Koa route decorators and calls —
        # deliberately framework-name-agnostic since this category exists
        # specifically to cover web frameworks with no dedicated AST adapter
        # yet (e.g. NestJS; see studyield-sbom-fix.md item #1).
        "route",
        "controller",
        "@get(",
        "@post(",
        "@put(",
        "@delete(",
        "@patch(",
        "app.get(",
        "app.post(",
        "app.put(",
        "app.delete(",
        "router.get(",
        "router.post(",
        "router.put(",
        "router.delete(",
        "endpoint",
        "webhook",
        "@controller",
        "useguards",
        "middleware",
    ],
    ComponentType.AGENT: [
        # Hand-rolled (non-framework) multi-agent orchestration — a base/
        # abstract class whose subclasses are invoked in sequence by an
        # orchestrating service. Only ever gap-filled when zero AGENT nodes
        # AND zero AI-framework nodes exist (see _identify_absent_categories),
        # so this never runs for LangGraph/CrewAI/AutoGen-style apps where
        # deterministic detection already has ~97% recall.
        "agent",
        "orchestrator",
        "pipeline",
        "abstract class",
        "basemodel",
        "base_agent",
        "baseagent",
        "sequential",
        "workflow",
        "step",
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
    ComponentType.GUARDRAIL: [
        # Guardrail / content-safety frameworks and heuristics
        "guardrails_ai",
        "guardrails.Guard",
        "Guard(",
        "@register_validator",
        "guard.validate",
        "nemo-guardrails",
        "nemoguardrails",
        "llm-guard",
        "llm_guard",
        "rebuff",
        "presidio",
        "moderation",
        "openai.moderations",
        "content_filter",
        "toxicity",
        "profanity",
        "jailbreak",
        "prompt_injection",
        "sanitize_input",
        "sanitize_output",
        "output_filter",
        "safety_check",
        "pii_redact",
    ],
}

# Maximum snippet characters sent to LLM per gap-fill category
_MAX_SNIPPET_CHARS = 12_000
# Maximum lines read per file when building snippets
_MAX_LINES_PER_FILE = 300
# Confidence cap for LLM-discovered nodes (no structural backing)
_DISCOVERY_CONFIDENCE_CAP = 0.75
# Minimum confidence threshold below which a discovery result is ignored.
_MIN_ACCEPTED_CONFIDENCE = 0.60
# Per-category override of the acceptance floor — used for higher-risk
# categories (PRIVILEGE/GUARDRAIL) where a candidate must already look like
# the discovery-confidence ceiling just to survive Round 1.
_MIN_ACCEPTED_CONFIDENCE_BY_CATEGORY: dict[ComponentType, float] = {
    ComponentType.PRIVILEGE: 0.75,
    ComponentType.GUARDRAIL: 0.75,
}

# ---------------------------------------------------------------------------
# Gap-fill source-file filter (P1)
# ---------------------------------------------------------------------------
# Documentation, test, and deploy-guide files consistently produce false
# positives because the LLM reads *descriptive* mentions of tools/services
# as actual component detections. Only code files are sent as context.

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

# ---------------------------------------------------------------------------
# Per-type gap-fill gating (P2)
# ---------------------------------------------------------------------------
# Categories that are NEVER gap-filled unless explicitly opted into via
# config (AiSbomConfig.gap_fill_enable_privilege / gap_fill_enable_guardrail):
#   GUARDRAIL — deterministic recall claimed ~100% in prior tuning; this
#     claim is not independently reproducible from git history (see
#     gating.py docstring), so reinclusion is treated as an experiment.
#   PRIVILEGE — deterministic precision claimed ~16% without LLM gap-fill in
#     prior tuning (same caveat) — reinclusion adds a raised confidence floor
#     and mandatory self-critique round as a safeguard.
# AGENT is deliberately NOT in this set (see _AGENT_FRAMEWORK_MARKERS below) —
# framework-based agents still skip gap-fill via a dedicated rule in
# gating.py, but hand-rolled/non-framework multi-agent orchestration (zero
# AGENT nodes AND zero recognized AI-framework nodes) is a real, confirmed
# gap (studyield-sbom-fix.md item #2) that a blanket exclusion would make
# permanently invisible.
_GAP_FILL_OPT_IN: frozenset[ComponentType] = frozenset(
    {
        ComponentType.GUARDRAIL,
        ComponentType.PRIVILEGE,
    }
)

# Categories gap-filled only when truly absent (zero nodes of that type) —
# never probed even when some nodes already exist. These types have high
# deterministic recall from prior tuning; gap-fill only earns its keep as a
# safety net for total blanks.
#
# API_ENDPOINT: acts strictly as a fallback net for web frameworks with no
# dedicated AST adapter yet (e.g. NestJS — studyield-sbom-fix.md item #1).
# Once a real adapter exists for a given framework, deterministic detection
# finds >=1 node — but API_ENDPOINT is also eligible for PROBE gating (see
# gating.py) once a dynamic-route-registration signal is present, since a
# single static route existing doesn't mean all routes were found.
_GAP_FILL_ONLY_IF_ABSENT: frozenset[ComponentType] = frozenset(
    {
        ComponentType.AUTH,  # high recall without LLM
    }
)

# Categories eligible for PROBE gating — i.e. gap-fill may run even when the
# category already has >=1 deterministic node, if a "likely more exist"
# signal fires (see gating.py's probe-signal functions).
_GAP_FILL_PROBE_ELIGIBLE: frozenset[ComponentType] = frozenset(
    {
        ComponentType.API_ENDPOINT,
        ComponentType.TOOL,
        ComponentType.PROMPT,
    }
)

# AI orchestration framework markers used to gate AGENT gap-fill: only run
# it when the document has neither an AGENT node nor a FRAMEWORK node whose
# name/canonical_name suggests a recognized agent framework — i.e. genuinely
# hand-rolled orchestration with nothing else that could have caught it
# deterministically. Framework-based agents keep their high recall without
# ever invoking this (expensive, narrower) heuristic.
_AGENT_FRAMEWORK_MARKERS: frozenset[str] = frozenset(
    {
        "langgraph",
        "langchain",
        "crewai",
        "autogen",
        "llamaindex",
        "semantic_kernel",
        "semantic-kernel",
        "agno",
        "google_adk",
        "google-adk",
        "openai_agents",
        "openai-agents",
        "bedrock_agents",
        "bedrock-agents",
        "azure_ai_agents",
        "azure-ai-agents",
        "claude_agent_sdk",
        "claude-agent-sdk",
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
    ComponentType.API_ENDPOINT: (
        "HTTP or WebSocket API routes/endpoints exposed by the application — e.g. NestJS "
        "@Controller/@Get/@Post decorators, Express app.get()/router.post() calls, Fastify/Koa "
        "route registrations, or routes registered dynamically from a route table/config loop. "
        "Only report ROUTES ACTUALLY DEFINED in this code (a decorated handler, a route-registration "
        "call with a path, or a loop that iterates a route table calling a registration function), "
        "not routes merely mentioned in documentation or comments. "
        "Set \"name\" to \"METHOD /path\" (e.g. \"POST /api/users\")."
    ),
    ComponentType.AGENT: (
        "A hand-rolled (non-framework) multi-agent or multi-step orchestrator — application "
        "code where one class/service sequentially invokes several other classes that each "
        "build a prompt and call an LLM client, WITHOUT using LangGraph/CrewAI/AutoGen/"
        "LlamaIndex or another recognized AI framework. Report the ORCHESTRATING class/service "
        "as the AGENT (not each individual step class). Do not report this if the orchestration "
        "is built on a recognized AI framework — that is handled by deterministic detection."
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
    ComponentType.GUARDRAIL: (
        "Input/output content-safety or validation layers protecting the AI application — "
        "guardrails-ai Guard()/@register_validator usage, NeMo Guardrails, llm-guard, Rebuff, "
        "Presidio PII redaction, moderation API calls (openai.moderations.create, Perspective API), "
        "custom sanitize/validate/safety-check functions applied to LLM input or output, "
        "prompt-injection or jailbreak detection logic, toxicity/profanity filters. "
        "Only report concrete instantiations/calls actually wired into the request or response "
        "path, not merely imported-but-unused validators."
    ),
}
