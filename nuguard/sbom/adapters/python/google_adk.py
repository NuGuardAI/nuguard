"""Google ADK (Agent Development Kit) Python adapter for Xelo SBOM.

Detects usage of the ``google.adk`` Python SDK:
- ``Agent(name=..., model=..., tools=[...])`` → AGENT + MODEL + TOOL refs
- ``SequentialAgent(sub_agents=[...])`` / ``ParallelAgent`` / ``LoopAgent`` / ``LlmAgent`` → AGENT
- ``Gemini(model=...)`` → MODEL node
- Module-level constants resolved: ``MODEL = "gemini-2.0-flash-001"`` used as Agent(model=MODEL)
- Function references in ``tools=[fn1, fn2]`` emitted as TOOL nodes
"""

from __future__ import annotations

import re
from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, FrameworkAdapter, RelationshipHint
from ..ces import extract_string_vars, resolve_template
from ..models_kb import get_model_details, infer_provider

_TEMPLATE_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_MIN_PROMPT_LENGTH = 40

_AGENT_CLASSES = {
    "Agent",
    "LlmAgent",
    "SequentialAgent",
    "ParallelAgent",
    "LoopAgent",
    "BaseAgent",
}
_MODEL_CLASSES = {"Gemini", "ChatModel", "GenerativeModel"}


class GoogleADKPythonAdapter(FrameworkAdapter):
    """Adapter for the Google ADK Python SDK (google.adk)."""

    name = "google_adk"
    priority = 22
    handles_imports = [
        "google.adk",
        "google.adk.agents",
        "google.adk.models",
        "google.adk.tools",
        "google.adk.runners",
    ]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        if parse_result is None:
            return []

        # Build a map of module-level string constants
        # e.g. MODEL = "gemini-2.0-flash-001" stored as {context="MODEL": value="gemini-2.0-flash-001"}
        const_map: dict[str, str] = {}
        for lit in parse_result.string_literals:
            if lit.context and not lit.is_docstring:
                const_map[lit.context] = lit.value

        # Merge with raw-text variable extraction so f-string variables like
        # PROJECT, APP_ID, VERSION, DEPLOY are also available for URL resolution.
        const_map.update(extract_string_vars(content))

        # Resolve any CES/ADK deployment URLs that use variable placeholders.
        # E.g. CES_BASE = f"https://ces.googleapis.com/v1beta/projects/{PROJECT}/..."
        # becomes the literal URL after substitution.
        resolved_content = resolve_template(content, const_map)
        deployment_meta = _extract_deployment_meta(resolved_content, const_map)

        detected: list[ComponentDetection] = [self._framework_node(file_path)]

        # --- Explicit model class instantiations: Gemini(model=...) ---
        # Track which model canonicals we've already emitted to avoid duplicates
        emitted_models: set[str] = set()

        for inst in parse_result.instantiations:
            if inst.class_name not in _MODEL_CLASSES:
                continue
            model_val = _resolve_const(
                inst.args.get("model")
                or (inst.positional_args[0] if inst.positional_args else None),
                const_map,
            )
            if model_val:
                canon = canonicalize_text(model_val.lower())
                if canon not in emitted_models:
                    emitted_models.add(canon)
                    provider = infer_provider(model_val)
                    details = get_model_details(model_val, provider)
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.MODEL,
                            canonical_name=canon,
                            display_name=model_val,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.92,
                            metadata={
                                "provider": provider,
                                "framework": "google-adk",
                                "class": inst.class_name,
                                **{k: v for k, v in details.items() if v is not None},
                            },
                            file_path=file_path,
                            line=inst.line,
                            snippet=f"{inst.class_name}(model={model_val!r})",
                            evidence_kind="ast_instantiation",
                        )
                    )

        # --- Agent class instantiations ---
        for inst in parse_result.instantiations:
            if inst.class_name not in _AGENT_CLASSES:
                continue
            args = inst.args or {}

            agent_name = (
                _clean(
                    args.get("name")
                    or (inst.positional_args[0] if inst.positional_args else None)
                    or inst.assigned_to
                )
                or f"agent_{inst.line}"
            )

            # model argument — may be literal string, $VAR_NAME ref, or <complex> for Gemini()
            model_raw = args.get("model")
            model_val = _resolve_const(model_raw, const_map)

            canon = canonicalize_text(f"google_adk:{agent_name}")
            rels: list[RelationshipHint] = []

            # Emit MODEL node and relationship if model resolved
            if model_val:
                provider = infer_provider(model_val)
                details = get_model_details(model_val, provider)
                model_canon = canonicalize_text(model_val.lower())
                rels.append(
                    RelationshipHint(
                        source_canonical=canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=model_canon,
                        target_type=ComponentType.MODEL,
                        relationship_type="USES",
                    )
                )
                if model_canon not in emitted_models:
                    emitted_models.add(model_canon)
                    detected.append(
                        ComponentDetection(
                            component_type=ComponentType.MODEL,
                            canonical_name=model_canon,
                            display_name=model_val,
                            adapter_name=self.name,
                            priority=self.priority,
                            confidence=0.90,
                            metadata={
                                "provider": provider,
                                "framework": "google-adk",
                                **{k: v for k, v in details.items() if v is not None},
                            },
                            file_path=file_path,
                            line=inst.line,
                            snippet=f"Agent(model={model_val!r})",
                            evidence_kind="ast_instantiation",
                        )
                    )

            # tools= argument — list mixing function refs ($funcname) and strings
            tools_raw = args.get("tools", [])
            if isinstance(tools_raw, list):
                for tool_ref in tools_raw:
                    if isinstance(tool_ref, str) and tool_ref.startswith("$"):
                        tool_name = tool_ref[1:]  # strip leading $
                        tool_canon = canonicalize_text(f"google_adk:tool:{tool_name}")
                        rels.append(
                            RelationshipHint(
                                source_canonical=canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=tool_canon,
                                target_type=ComponentType.TOOL,
                                relationship_type="CALLS",
                            )
                        )
                        detected.append(
                            ComponentDetection(
                                component_type=ComponentType.TOOL,
                                canonical_name=tool_canon,
                                display_name=tool_name,
                                adapter_name=self.name,
                                priority=self.priority,
                                confidence=0.85,
                                metadata={
                                    "framework": "google-adk",
                                    "tool_type": "python_function",
                                },
                                file_path=file_path,
                                line=inst.line,
                                snippet=f"tools=[..., {tool_name}, ...]",
                                evidence_kind="ast_instantiation",
                            )
                        )

            # sub_agents= for SequentialAgent / ParallelAgent / LoopAgent
            sub_agents_raw = args.get("sub_agents", [])
            if isinstance(sub_agents_raw, list):
                for sub_ref in sub_agents_raw:
                    if isinstance(sub_ref, str) and sub_ref.startswith("$"):
                        sub_name = sub_ref[1:]
                        sub_canon = canonicalize_text(f"google_adk:{sub_name}")
                        rels.append(
                            RelationshipHint(
                                source_canonical=canon,
                                source_type=ComponentType.AGENT,
                                target_canonical=sub_canon,
                                target_type=ComponentType.AGENT,
                                relationship_type="CALLS",
                            )
                        )

            # instruction= / description= argument → PROMPT node
            instruction_raw = args.get("instruction") or args.get("description", "")
            instruction = _resolve_const(instruction_raw, const_map)
            prompt_display = f"{agent_name} Instructions"
            prompt_canon = canonicalize_text(f"google_adk:{prompt_display.lower()}")
            if instruction and len(instruction) >= _MIN_PROMPT_LENGTH:
                rels.append(
                    RelationshipHint(
                        source_canonical=canon,
                        source_type=ComponentType.AGENT,
                        target_canonical=prompt_canon,
                        target_type=ComponentType.PROMPT,
                        relationship_type="USES",
                    )
                )

            agent_meta: dict[str, Any] = {
                "framework": "google-adk",
                "agent_subtype": _agent_subtype(inst.class_name),
                "has_instructions": bool(instruction),
                **({"model": model_val} if model_val else {}),
                **deployment_meta,
            }
            if instruction:
                template_vars = _TEMPLATE_VAR_RE.findall(instruction)
                agent_meta["instructions_preview"] = instruction[:500]
                agent_meta["is_template"] = bool(template_vars)
                agent_meta["template_variables"] = template_vars

            detected.append(
                ComponentDetection(
                    component_type=ComponentType.AGENT,
                    canonical_name=canon,
                    display_name=agent_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.92,
                    metadata=agent_meta,
                    file_path=file_path,
                    line=inst.line,
                    snippet=f"{inst.class_name}(name={agent_name!r})",
                    evidence_kind="ast_instantiation",
                    relationships=rels,
                )
            )

            if instruction and len(instruction) >= _MIN_PROMPT_LENGTH:
                template_vars = _TEMPLATE_VAR_RE.findall(instruction)
                detected.append(
                    ComponentDetection(
                        component_type=ComponentType.PROMPT,
                        canonical_name=prompt_canon,
                        display_name=prompt_display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.88,
                        metadata={
                            "role": "system",
                            "content": instruction,
                            "char_count": len(instruction),
                            "is_template": bool(template_vars),
                            "template_variables": template_vars,
                        },
                        file_path=file_path,
                        line=inst.line,
                        snippet=instruction[:80],
                        evidence_kind="ast_instantiation",
                    )
                )

        return detected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_subtype(class_name: str) -> str:
    if class_name in {"Agent", "LlmAgent"}:
        return "llm"
    if "Sequential" in class_name:
        return "sequential"
    if "Parallel" in class_name:
        return "parallel"
    if "Loop" in class_name:
        return "loop"
    return "generic"


def _resolve_const(value: Any, const_map: dict[str, str]) -> str:
    """Resolve a value that may be a string literal, $VAR_NAME reference, or <complex>."""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith("$"):
        # Variable reference — look up in module-level constants
        var_name = value[1:]
        return const_map.get(var_name, "")
    return _clean(value)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).strip("'\"` ")
    if s.startswith("$") or s in {"<complex>", "<lambda>", "<dict>", "<list>"}:
        return ""
    return s


# CES / ADK variable name patterns mapped to their SBOM metadata key.
# Checked against const_map keys (case-insensitive suffix match).
_DEPLOY_VAR_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("project", "gcp_project", "google_project"), "ces_project"),
    (("app_id", "appid", "app_name", "appname"), "ces_app_id"),
    (("version", "version_id", "versionid", "app_version"), "ces_version_id"),
    (("deploy", "deploy_id", "deployment", "deployment_id"), "ces_deployment_id"),
    (("location", "region"), "ces_location"),
]

# Regex to capture a fully-expanded CES URL (no placeholders).
_EXPANDED_CES_URL_RE = re.compile(
    r"https://ces\.googleapis\.com/v1beta/projects/[A-Za-z0-9][A-Za-z0-9._-]*/[^\s\"'<>]+"
)


def _extract_deployment_meta(
    resolved_content: str,
    const_map: dict[str, str],
) -> dict[str, Any]:
    """Return deployment-related metadata extracted from a resolved source file.

    Uses *const_map* (variable name → value) to surface CES / ADK deployment
    variables (PROJECT, APP_ID, VERSION, DEPLOY, etc.) and any fully-expanded
    CES URLs found after variable substitution.
    """
    meta: dict[str, Any] = {}

    # Match variable names from const_map against known aliases
    lower_map = {k.lower().lstrip("_"): v for k, v in const_map.items()}
    for aliases, meta_key in _DEPLOY_VAR_ALIASES:
        for alias in aliases:
            if alias in lower_map:
                meta[meta_key] = lower_map[alias]
                break

    # Capture the first expanded CES URL (after resolve_template substitution)
    url_match = _EXPANDED_CES_URL_RE.search(resolved_content)
    if url_match:
        meta["ces_target_url"] = url_match.group(0)

    return meta
