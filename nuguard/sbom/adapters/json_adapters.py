"""JSON-based adapters for AI SBOM extraction.

Parses structured JSON configuration files used by AI frameworks.

Supported patterns
------------------
``GoogleADKJSONAdapter``:
    Detects agents, tools, toolsets, models, and guardrails from Google ADK
    (Vertex AI Agent Builder / CX Agent Studio) JSON resource files.
    Driven entirely by content heuristics — no path-pattern restriction —
    so it fires on ``agents/*/agent.json``, ``tools/*/tool.json``,
    ``toolsets/*/toolset.json``, ``guardrails/*/guardrail.json``, and
    the top-level ``app.json``.

``OpenAIToolsJSONAdapter``:
    Detects tool/function definitions from OpenAI-style ``tools.json`` or
    ``functions.json`` files (and any JSON array of ``{"type": "function", ...}``
    objects).

``AgentJSONConfigAdapter``:
    Detects agents defined in JSON config files such as ``agents.json``,
    ``agent_config.json``, or arrays/objects with ``role``/``goal``/``backstory``
    fields (mirrors CrewAI YAML structure in JSON form).

``LLMJSONConfigAdapter``:
    Detects models and providers from JSON config files — the JSON equivalent
    of ``LLMYAMLConfigAdapter``.  Matches ``llm_config.json``,
    ``providers.json``, ``models.json``, etc.

``PromptJSONAdapter``:
    Detects prompt definitions from JSON files.  Handles OpenAI-style message
    arrays (``[{"role": "system", "content": "..."}]``) and flat objects with
    a ``content`` or ``prompt`` key.  Triggered by path pattern.

``MCPServerJSONAdapter``:
    Detects MCP server/tool registrations from Claude Desktop / VS Code MCP
    config files (``mcp.json``, ``mcp_config.json``,
    ``claude_desktop_config.json``, etc.).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..normalization import canonicalize_text
from ..types import ComponentType
from .base import ComponentDetection, RelationshipHint

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_load_json(content: str) -> Any:
    """Parse JSON content, returning None on failure."""
    try:
        return json.loads(content)
    except Exception as exc:  # noqa: BLE001
        _log.debug("JSON parse error: %s", exc)
        return None


def _find_key_line(content: str, key: str) -> int:
    """Return 1-based line number where ``"key"`` first appears, or 1."""
    pattern = re.compile(r'"' + re.escape(key) + r'"')
    for i, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            return i
    return 1


# ---------------------------------------------------------------------------
# OpenAI-style tool/function definitions
# ---------------------------------------------------------------------------

_TOOLS_JSON_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:tools|functions|tool_definitions?|function_definitions?)"
    r"(?:[_-].*)?\.json$",
    re.IGNORECASE,
)


class OpenAIToolsJSONAdapter:
    """Detect tool/function definitions from OpenAI-style JSON files.

    Handles two shapes:

    1. Array of tool objects::

        [
          {"type": "function", "function": {"name": "...", "description": "..."}}
        ]

    2. Object with a ``tools`` or ``functions`` key containing such an array::

        {"tools": [...]}

    Triggered by file names matching ``tools.json``, ``functions.json``,
    ``tool_definitions.json``, etc.
    """

    name = "openai_tools_json"
    priority = 36

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        if not _TOOLS_JSON_PATH_RE.search(rel_path):
            return []

        data = _try_load_json(content)
        if data is None:
            return []

        # Normalise to a flat list of potential tool objects
        if isinstance(data, dict):
            raw_list: list[Any] = []
            for key in ("tools", "functions"):
                val = data.get(key)
                if isinstance(val, list):
                    raw_list = val
                    break
            if not raw_list:
                return []
        elif isinstance(data, list):
            raw_list = data
        else:
            return []

        detections: list[ComponentDetection] = []
        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            # OpenAI tool shape: {"type": "function", "function": {...}}
            fn_block = entry.get("function") if entry.get("type") == "function" else entry
            if not isinstance(fn_block, dict):
                continue
            name = str(fn_block.get("name") or "").strip()
            if not name:
                continue
            description = str(fn_block.get("description") or "").strip()
            line = _find_key_line(content, name)
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canonicalize_text(name),
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "source": "json_config",
                        "description": description[:200] if description else None,
                        "parameters": fn_block.get("parameters"),
                    },
                    file_path=rel_path,
                    line=line,
                    snippet=f"{name}: {description[:60]}" if description else name,
                    evidence_kind="json",
                )
            )
            _log.debug("openai_tools_json: tool %r in %s (line %d)", name, rel_path, line)

        return detections


# ---------------------------------------------------------------------------
# Agent config JSON adapter
# ---------------------------------------------------------------------------

_AGENT_JSON_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:agents?|agent[_-]config|crewai[_-]agents?)(?:[_-].*)?\.json$",
    re.IGNORECASE,
)
_AGENT_FIELDS = {"role", "goal", "backstory", "description", "system_message"}


class AgentJSONConfigAdapter:
    """Detect agents defined in JSON configuration files.

    Handles two shapes:

    1. Top-level mapping where each key is an agent (CrewAI-style)::

        {
          "researcher": {"role": "Senior Analyst", "goal": "..."},
          "writer": {"role": "Content Writer", "goal": "..."}
        }

    2. Top-level ``agents`` array::

        {"agents": [{"name": "researcher", "role": "...", "goal": "..."}]}

    Triggered by file names matching ``agents.json``, ``agent_config.json``, etc.
    """

    name = "agent_json_config"
    priority = 36

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        if not _AGENT_JSON_PATH_RE.search(rel_path):
            return []

        data = _try_load_json(content)
        if not isinstance(data, dict):
            return []

        detections: list[ComponentDetection] = []

        # Shape 1: array under "agents" key
        agents_list = data.get("agents")
        if isinstance(agents_list, list):
            for entry in agents_list:
                if not isinstance(entry, dict):
                    continue
                if not any(k in entry for k in _AGENT_FIELDS):
                    continue
                name = str(entry.get("name") or entry.get("id") or "").strip()
                if not name:
                    continue
                detections.append(self._make_detection(name, entry, rel_path, content))
            return detections

        # Shape 2: top-level mapping (key → agent dict)
        for agent_key, agent_val in data.items():
            if not isinstance(agent_val, dict):
                continue
            if not any(k in agent_val for k in _AGENT_FIELDS):
                continue
            agent_name = str(agent_key).strip()
            if not agent_name:
                continue
            detections.append(self._make_detection(agent_name, agent_val, rel_path, content))

        return detections

    def _make_detection(
        self, name: str, val: dict[str, Any], rel_path: str, content: str
    ) -> ComponentDetection:
        role = str(val.get("role") or "").strip()
        goal = str(val.get("goal") or val.get("description") or "").strip()
        line = _find_key_line(content, name)
        _log.debug("agent_json_config: agent %r in %s (line %d)", name, rel_path, line)
        return ComponentDetection(
            component_type=ComponentType.AGENT,
            canonical_name=canonicalize_text(name),
            display_name=name,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.85,
            metadata={
                "source": "json_config",
                "role": role or None,
                "goal": goal[:200] if goal else None,
            },
            file_path=rel_path,
            line=line,
            snippet=f"{name}: role={role[:60]!r}" if role else name,
            evidence_kind="json",
        )


# ---------------------------------------------------------------------------
# LLM / model config JSON adapter
# ---------------------------------------------------------------------------

_LLM_JSON_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:llm[_-]?config.*|providers.*|models.*|config[\\/]llm)\.json$",
    re.IGNORECASE,
)

# Re-use the same base-URL → provider mapping as the YAML adapter
_LLM_JSON_BASE_URL_PROVIDERS: list[tuple[str, str]] = [
    ("api.groq.com", "groq"),
    ("generativelanguage.googleapis.com", "google"),
    ("aiplatform.googleapis.com", "google"),
    ("localhost:11434", "ollama"),
    ("127.0.0.1:11434", "ollama"),
    ("api.anthropic.com", "anthropic"),
    ("api.mistral.ai", "mistral"),
    ("api.together.xyz", "togetherai"),
    ("api.deepseek.com", "deepseek"),
    ("openrouter.ai", "openrouter"),
    ("api.openai.com", "openai"),
]


def _provider_from_base_url(base_url: str) -> str | None:
    url = (base_url or "").lower()
    for substring, provider in _LLM_JSON_BASE_URL_PROVIDERS:
        if substring in url:
            return provider
    return None


class LLMJSONConfigAdapter:
    """Detect LLM models and providers from generic JSON config files.

    JSON equivalent of ``LLMYAMLConfigAdapter``.  Matches files whose name
    matches ``llm_config.json``, ``providers.json``, ``models.json``, or
    ``config/llm.json``.

    Supports the same ``providers`` / ``llm_providers`` / ``models`` block
    structure as the YAML adapter::

        {
          "providers": {
            "groq": {
              "model": "llama-3.3-70b-versatile",
              "base_url": "https://api.groq.com/openai/v1"
            }
          }
        }

    Also handles a flat single-model object::

        {"model": "gpt-4o", "provider": "openai"}

    Emits MODEL and FRAMEWORK nodes.
    """

    name = "llm_json_config"
    priority = 37

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        if not _LLM_JSON_PATH_RE.search(rel_path):
            return []

        data = _try_load_json(content)
        if not isinstance(data, dict):
            _log.debug("llm_json_config: %s is not a JSON object — skipping", rel_path)
            return []

        detections: list[ComponentDetection] = []

        # Shape A: providers/models block (same structure as YAML adapter)
        providers_block = data.get("providers") or data.get("llm_providers") or data.get("models")
        if isinstance(providers_block, dict):
            seen_providers: set[str] = set()
            for entry_key, entry_val in providers_block.items():
                if not isinstance(entry_val, dict):
                    continue
                if entry_val.get("enabled") is False:
                    continue
                model = str(entry_val.get("model") or "").strip()
                base_url = str(
                    entry_val.get("base_url") or entry_val.get("api_base") or ""
                ).strip()
                provider = _provider_from_base_url(base_url) or str(entry_key).lower().strip()
                entry_line = _find_key_line(content, entry_key)

                if model:
                    _fw_canon = canonicalize_text(f"framework:{provider}")
                    _model_canon = canonicalize_text(model.lower())
                    detections.append(
                        ComponentDetection(
                            component_type=ComponentType.MODEL,
                            canonical_name=model.lower(),
                            display_name=model,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.85,
                            metadata={
                                "framework": provider,
                                "provider": provider,
                                "source": "json_config",
                                "base_url": base_url or None,
                            },
                            file_path=rel_path,
                            line=entry_line,
                            snippet=f"{entry_key}: model={model}",
                            evidence_kind="json",
                            relationships=[
                                RelationshipHint(
                                    source_canonical=_fw_canon,
                                    source_type=ComponentType.FRAMEWORK,
                                    target_canonical=_model_canon,
                                    target_type=ComponentType.MODEL,
                                    relationship_type="USES",
                                )
                            ],
                        )
                    )
                    _log.debug(
                        "llm_json_config: model=%r provider=%r in %s", model, provider, rel_path
                    )

                if provider not in seen_providers:
                    seen_providers.add(provider)
                    detections.append(
                        ComponentDetection(
                            component_type=ComponentType.FRAMEWORK,
                            canonical_name=f"framework:{provider}",
                            display_name=provider,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.80,
                            metadata={
                                "framework": provider,
                                "source": "json_config",
                                "base_url": base_url or None,
                            },
                            file_path=rel_path,
                            line=entry_line,
                            snippet=f"{entry_key}: base_url={base_url}" if base_url else entry_key,
                            evidence_kind="json",
                        )
                    )
            _log.info("llm_json_config: %d detections in %s", len(detections), rel_path)
            return detections

        # Shape B: flat single-model object {"model": "...", "provider": "..."}
        model = str(data.get("model") or "").strip()
        if model:
            provider = str(
                data.get("provider")
                or _provider_from_base_url(str(data.get("base_url") or ""))
                or ""
            ).strip()
            line = _find_key_line(content, "model")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=model.lower(),
                    display_name=model,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.75,
                    metadata={
                        "framework": provider or None,
                        "provider": provider or None,
                        "source": "json_config",
                    },
                    file_path=rel_path,
                    line=line,
                    snippet=f"model: {model}",
                    evidence_kind="json",
                )
            )
            _log.debug("llm_json_config: flat model=%r in %s", model, rel_path)

        _log.info("llm_json_config: %d detections in %s", len(detections), rel_path)
        return detections


# ---------------------------------------------------------------------------
# Prompt JSON adapter
# ---------------------------------------------------------------------------

_PROMPT_JSON_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:prompts?|system[_-]prompts?|prompt[_-]templates?|"
    r".*[_-]prompts?|.*[_-]system)(?:[_-].*)?\.json$",
    re.IGNORECASE,
)
_PROMPT_JSON_DIR_RE = re.compile(r"(?:^|[\\/])prompts?[\\/]", re.IGNORECASE)
_ROLES = {"system", "user", "assistant"}


class PromptJSONAdapter:
    """Detect prompt definitions from JSON files.

    Handles three shapes:

    1. OpenAI message array::

        [{"role": "system", "content": "You are a helpful assistant."}]

    2. Single message object::

        {"role": "system", "content": "..."}

    3. Named prompt map::

        {"system_prompt": "...", "user_prompt": "..."}

    Triggered by files in a ``prompts/`` directory **or** file names matching
    prompt-related patterns (e.g. ``prompts.json``, ``system_prompts.json``).
    """

    name = "prompt_json"
    priority = 45

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        path_str = rel_path.replace("\\", "/")
        in_prompt_dir = bool(_PROMPT_JSON_DIR_RE.search(path_str))
        is_prompt_file = bool(_PROMPT_JSON_PATH_RE.search(path_str))
        if not (in_prompt_dir or is_prompt_file):
            return []

        data = _try_load_json(content)
        if data is None:
            return []

        detections: list[ComponentDetection] = []
        stem = Path(rel_path).stem.replace("_", " ").replace("-", " ").title()

        # Shape 1: array of message objects
        if isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                role = str(entry.get("role") or "").lower().strip()
                msg_content = str(entry.get("content") or "").strip()
                if role not in _ROLES or not msg_content:
                    continue
                preview = msg_content[:160].replace("\n", " ")
                display = f"{stem} ({role})"
                detections.append(
                    self._make_prompt(
                        canonical=canonicalize_text(f"{stem} {role}"),
                        display=display,
                        role=role,
                        msg_content=msg_content,
                        preview=preview,
                        rel_path=rel_path,
                        line=_find_key_line(content, role),
                    )
                )
            return detections

        if not isinstance(data, dict):
            return []

        # Shape 2: single message object {"role": "system", "content": "..."}
        role = str(data.get("role") or "").lower().strip()
        msg_content = str(data.get("content") or "").strip()
        if role in _ROLES and msg_content:
            preview = msg_content[:160].replace("\n", " ")
            detections.append(
                self._make_prompt(
                    canonical=canonicalize_text(f"{stem} {role}"),
                    display=f"{stem} ({role})",
                    role=role,
                    msg_content=msg_content,
                    preview=preview,
                    rel_path=rel_path,
                    line=_find_key_line(content, "content"),
                )
            )
            return detections

        # Shape 3: named prompt map {"system_prompt": "...", "user_prompt": "..."}
        for key, val in data.items():
            if not isinstance(val, str) or not val.strip():
                continue
            key_lower = key.lower()
            if not any(kw in key_lower for kw in ("prompt", "system", "instruction")):
                continue
            inferred_role = "system" if "system" in key_lower else "user"
            stripped = val.strip()
            preview = stripped[:160].replace("\n", " ")
            display = key.replace("_", " ").replace("-", " ").title()
            detections.append(
                self._make_prompt(
                    canonical=canonicalize_text(display),
                    display=display,
                    role=inferred_role,
                    msg_content=stripped,
                    preview=preview,
                    rel_path=rel_path,
                    line=_find_key_line(content, key),
                )
            )

        return detections

    def _make_prompt(
        self,
        *,
        canonical: str,
        display: str,
        role: str,
        msg_content: str,
        preview: str,
        rel_path: str,
        line: int,
    ) -> ComponentDetection:
        from re import compile as _re_compile

        _TEMPLATE_VAR_RE = _re_compile(r"\{[\w_]+\}")
        is_template = bool(_TEMPLATE_VAR_RE.search(msg_content))
        template_vars = list({m.group(0) for m in _TEMPLATE_VAR_RE.finditer(msg_content)})
        _log.debug("prompt_json: detected %r in %s", display, rel_path)
        return ComponentDetection(
            component_type=ComponentType.PROMPT,
            canonical_name=canonical,
            display_name=display,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.75,
            metadata={
                "source": "json_config",
                "content": msg_content,
                "role": role,
                "is_template": is_template,
                "template_variables": template_vars,
                "char_count": len(msg_content),
            },
            file_path=rel_path,
            line=line,
            snippet=preview[:80],
            evidence_kind="json",
        )


# ---------------------------------------------------------------------------
# MCP server config JSON adapter
# ---------------------------------------------------------------------------

_MCP_JSON_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:mcp(?:[_-]config)?|claude[_-]desktop[_-]config|mcp[_-]servers?)\.json$",
    re.IGNORECASE,
)


class MCPServerJSONAdapter:
    """Detect MCP server/tool registrations from config files.

    Handles the Claude Desktop / VS Code MCP config format::

        {
          "mcpServers": {
            "filesystem": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
            }
          }
        }

    Also handles a flat ``servers`` key variant.

    Triggered by file names: ``mcp.json``, ``mcp_config.json``,
    ``claude_desktop_config.json``, ``mcp_servers.json``.
    """

    name = "mcp_server_json"
    priority = 36

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        if not _MCP_JSON_PATH_RE.search(rel_path):
            return []

        data = _try_load_json(content)
        if not isinstance(data, dict):
            return []

        servers_block = data.get("mcpServers") or data.get("servers") or {}
        if not isinstance(servers_block, dict):
            return []

        detections: list[ComponentDetection] = []
        for server_name, server_val in servers_block.items():
            if not isinstance(server_val, dict):
                continue
            name = str(server_name).strip()
            if not name:
                continue
            command = str(server_val.get("command") or "").strip()
            args: list[str] = [str(a) for a in (server_val.get("args") or [])]
            line = _find_key_line(content, name)
            snippet = f"{name}: {command} {' '.join(args[:3])}" if command else name
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canonicalize_text(f"mcp:{name}"),
                    display_name=name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "source": "mcp_config",
                        "command": command or None,
                        "args": args or None,
                        "env": server_val.get("env"),
                    },
                    file_path=rel_path,
                    line=line,
                    snippet=snippet[:80],
                    evidence_kind="json",
                )
            )
            _log.debug("mcp_server_json: server %r in %s (line %d)", name, rel_path, line)

        return detections


# ---------------------------------------------------------------------------
# Google ADK / Vertex AI Agent Builder JSON adapter
# ---------------------------------------------------------------------------

# Files that are clearly NOT agent/tool resources (skip to avoid false positives)
_GOOGLE_ADK_SKIP_NAMES: frozenset[str] = frozenset(
    {
        "api_response.json",
        "response.json",
        "environment.json",
        "package.json",
        "package-lock.json",
        "tsconfig.json",
        "open_api_schema.json",
    }
)


def _is_google_adk_resource(data: dict[str, Any]) -> bool:
    """Return True if *data* looks like a Google ADK resource object.

    All ADK resource files share a UUID ``name`` field and a ``displayName``
    string.  We require both to avoid false positives on generic JSON.
    """
    name_val = data.get("name")
    display_val = data.get("displayName")
    if not isinstance(name_val, str) or not isinstance(display_val, str):
        return False
    # UUID pattern or short resource path (e.g. "projects/.../agents/...")
    return bool(name_val.strip()) and bool(display_val.strip())


class GoogleADKJSONAdapter:
    """Detect agents, tools, models, and guardrails from Google ADK JSON files.

    Google ADK (Vertex AI Agent Builder / CX Agent Studio) stores each
    resource — app, agent, tool, toolset, guardrail — as an individual JSON
    file.  This adapter inspects the *content* of every ``.json`` file and
    emits SBOM nodes based on structural heuristics:

    * **App file** (``rootAgent`` + ``modelSettings``): emits MODEL and
      AGENT (for the root agent reference).
    * **Agent file** (``instruction`` or ``childAgents`` key): emits AGENT,
      plus MODEL if ``modelSettings.model`` is present.
    * **Tool file** (``pythonFunction`` or ``executionType`` key): emits TOOL.
    * **Toolset file** (``openApiToolset`` key): emits TOOL.
    * **Guardrail file** (``contentFilter`` or ``llmPromptSecurity`` key):
      emits GUARDRAIL.

    All resources must carry both ``name`` (UUID) and ``displayName`` fields —
    this is the primary guard against false positives.
    """

    name = "google_adk_json"
    priority = 36

    def scan(
        self, content: str, rel_path: str, root: Path | None = None
    ) -> list[ComponentDetection]:
        # Skip files known to not be ADK resources
        if Path(rel_path).name.lower() in _GOOGLE_ADK_SKIP_NAMES:
            return []

        data = _try_load_json(content)
        if not isinstance(data, dict):
            return []
        if not _is_google_adk_resource(data):
            return []

        display_name = str(data["displayName"]).strip()
        detections: list[ComponentDetection] = []

        # ── App file ──────────────────────────────────────────────────────
        if "rootAgent" in data:
            # Model at app level
            model = self._extract_model(data)
            if model:
                line = _find_key_line(content, "model")
                detections.append(self._model_det(model, "google_adk", rel_path, line, content))

            # Root agent reference
            root_agent = str(data["rootAgent"]).strip()
            if root_agent:
                line = _find_key_line(content, "rootAgent")
                detections.append(
                    ComponentDetection(
                        component_type=ComponentType.AGENT,
                        canonical_name=canonicalize_text(root_agent),
                        display_name=root_agent,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.85,
                        metadata={
                            "framework": "google_adk",
                            "source": "json_config",
                            "role": "root_agent",
                        },
                        file_path=rel_path,
                        line=line,
                        snippet=f"rootAgent: {root_agent}",
                        evidence_kind="json",
                    )
                )
            # Global instruction file at app level
            global_instr = str(data.get("globalInstruction") or "").strip()
            if global_instr:
                prompt_det = self._read_instruction_prompt(
                    global_instr, display_name, "system", rel_path, root
                )
                if prompt_det:
                    detections.append(prompt_det)
            _log.debug("google_adk_json: app %r in %s", display_name, rel_path)
            return detections

        # ── Guardrail file ────────────────────────────────────────────────
        if "contentFilter" in data or "llmPromptSecurity" in data:
            line = _find_key_line(content, "displayName")
            description = str(data.get("description") or "").strip()
            guardrail_type = "content_filter" if "contentFilter" in data else "llm_prompt_security"
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.GUARDRAIL,
                    canonical_name=canonicalize_text(display_name),
                    display_name=display_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": "google_adk",
                        "source": "json_config",
                        "guardrail_type": guardrail_type,
                        "description": description[:200] if description else None,
                        "enabled": data.get("enabled"),
                    },
                    file_path=rel_path,
                    line=line,
                    snippet=f"guardrail: {display_name}",
                    evidence_kind="json",
                )
            )
            _log.debug("google_adk_json: guardrail %r in %s", display_name, rel_path)
            return detections

        # ── Tool file ─────────────────────────────────────────────────────
        if "pythonFunction" in data or "executionType" in data:
            fn_block = data.get("pythonFunction") or {}
            tool_name = str(fn_block.get("name") or display_name).strip()
            description = str(fn_block.get("description") or data.get("description") or "").strip()
            line = _find_key_line(content, "displayName")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canonicalize_text(tool_name),
                    display_name=tool_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": "google_adk",
                        "source": "json_config",
                        "description": description[:200] if description else None,
                        "execution_type": data.get("executionType"),
                    },
                    file_path=rel_path,
                    line=line,
                    snippet=f"tool: {tool_name}" + (f" — {description[:60]}" if description else ""),
                    evidence_kind="json",
                )
            )
            _log.debug("google_adk_json: tool %r in %s", tool_name, rel_path)
            return detections

        # ── Toolset file ──────────────────────────────────────────────────
        if "openApiToolset" in data:
            description = str(data.get("description") or "").strip()
            schema_path = str(
                (data.get("openApiToolset") or {}).get("openApiSchema") or ""
            ).strip()
            line = _find_key_line(content, "displayName")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=canonicalize_text(f"toolset:{display_name}"),
                    display_name=display_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.85,
                    metadata={
                        "framework": "google_adk",
                        "source": "json_config",
                        "resource_kind": "toolset",
                        "description": description[:200] if description else None,
                        "open_api_schema": schema_path or None,
                    },
                    file_path=rel_path,
                    line=line,
                    snippet=f"toolset: {display_name}",
                    evidence_kind="json",
                )
            )
            _log.debug("google_adk_json: toolset %r in %s", display_name, rel_path)
            return detections

        # ── Agent file ────────────────────────────────────────────────────
        if "instruction" in data or "childAgents" in data:
            description = str(data.get("description") or "").strip()
            line = _find_key_line(content, "displayName")
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canonicalize_text(display_name),
                    display_name=display_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.90,
                    metadata={
                        "framework": "google_adk",
                        "source": "json_config",
                        "description": description[:200] if description else None,
                        "tools": data.get("tools"),
                        "child_agents": data.get("childAgents"),
                    },
                    file_path=rel_path,
                    line=line,
                    snippet=f"agent: {display_name}"
                    + (f" — {description[:60]}" if description else ""),
                    evidence_kind="json",
                )
            )
            _log.debug("google_adk_json: agent %r in %s", display_name, rel_path)

            # Model inside agent
            model = self._extract_model(data)
            if model:
                m_line = _find_key_line(content, "model")
                detections.append(self._model_det(model, "google_adk", rel_path, m_line, content))

            # Instruction file → PROMPT node
            instr_path = str(data.get("instruction") or "").strip()
            if instr_path:
                prompt_det = self._read_instruction_prompt(
                    instr_path, display_name, "system", rel_path, root
                )
                if prompt_det:
                    detections.append(prompt_det)

        return detections

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_instruction_prompt(
        self,
        instr_rel: str,
        agent_name: str,
        role: str,
        json_rel_path: str,
        root: Path | None,
    ) -> ComponentDetection | None:
        """Read *instr_rel* (relative to *root*) and return a PROMPT detection.

        Returns ``None`` if the file cannot be read or is empty.
        """
        if root is None:
            _log.debug(
                "google_adk_json: cannot resolve instruction %r — no root path", instr_rel
            )
            return None

        instr_file = root / instr_rel
        try:
            text = instr_file.read_text(encoding="utf-8", errors="ignore").strip()
        except OSError as exc:
            _log.debug("google_adk_json: could not read instruction file %s: %s", instr_file, exc)
            return None

        if not text:
            return None

        from re import compile as _re_compile

        _TEMPLATE_VAR_RE = _re_compile(r"\{[\w_]+\}")
        is_template = bool(_TEMPLATE_VAR_RE.search(text))
        template_vars = list({m.group(0) for m in _TEMPLATE_VAR_RE.finditer(text)})
        preview = text[:160].replace("\n", " ")
        display = f"{agent_name} instruction"
        instr_rel_str = str(Path(instr_rel).as_posix())

        _log.debug(
            "google_adk_json: prompt from instruction file %s for agent %r",
            instr_rel_str,
            agent_name,
        )
        return ComponentDetection(
            component_type=ComponentType.PROMPT,
            canonical_name=canonicalize_text(display),
            display_name=display,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.90,
            metadata={
                "framework": "google_adk",
                "source": "instruction_file",
                "content": text,
                "role": role,
                "is_template": is_template,
                "template_variables": template_vars,
                "char_count": len(text),
                "instruction_file": instr_rel_str,
            },
            file_path=instr_rel_str,
            line=1,
            snippet=preview[:80],
            evidence_kind="json",
        )

    @staticmethod
    def _extract_model(data: dict[str, Any]) -> str:
        """Return model name from ``modelSettings.model`` if present."""
        ms = data.get("modelSettings")
        if isinstance(ms, dict):
            return str(ms.get("model") or "").strip()
        return ""

    def _model_det(
        self, model: str, framework: str, rel_path: str, line: int, content: str
    ) -> ComponentDetection:
        return ComponentDetection(
            component_type=ComponentType.MODEL,
            canonical_name=model.lower(),
            display_name=model,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.90,
            metadata={
                "framework": framework,
                "source": "json_config",
            },
            file_path=rel_path,
            line=line,
            snippet=f"model: {model}",
            evidence_kind="json",
        )
