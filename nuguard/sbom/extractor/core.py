"""Core SBOM extraction engine.

Orchestrates the extraction pipeline:

1. **AST-aware framework adapters** (Python files):
   Uses ``ast_parser.parse()`` to build structured parse data, then runs
   ``FrameworkAdapter.extract()`` to emit rich ``ComponentDetection`` objects.

2. **Regex fallback adapters** (all files):
   Runs legacy ``RegexAdapter.detect()`` on raw file content for non-Python
   files (YAML, Terraform, Dockerfiles, etc.) and as a catch-all for Python
   files that the framework adapters didn't fully cover.

3. **LLM enrichment** (optional, when ``AiSbomConfig.enable_llm=True``):
   Verifies uncertain detections, re-aggregates confidence scores with LLM
   input, and enriches the scan-level summary.

Results are deduplicated by ``(component_type, canonical_name)``,
merged by confidence/priority, and assembled into an ``AiSbomDocument``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from nuguard.common.logging import get_logger

from ..adapters.base import (
    AdapterMatch,
    ComponentDetection,
    DetectionAdapter,
    FrameworkAdapter,
    RelationshipHint,
)
from ..adapters.data_classification import DataClassificationSQLAdapter
from ..adapters.dockerfile import DockerfileAdapter
from ..adapters.go._go_base import GoFrameworkAdapter
from ..adapters.iac import (
    BicepAdapter,
    CloudFormationAdapter,
    GcpDeploymentManagerAdapter,
    GitHubActionsAdapter,
    K8sAdapter,
    TerraformAdapter,
)
from ..adapters.json_adapters import (
    AgentJSONConfigAdapter,
    GoogleADKJSONAdapter,
    LLMJSONConfigAdapter,
    MCPServerJSONAdapter,
    OpenAIToolsJSONAdapter,
    PromptJSONAdapter,
)
from ..adapters.nginx import NginxAdapter, is_nginx_file
from ..adapters.prompt_sql import PromptSQLAdapter
from ..adapters.registry import default_framework_adapters, default_registry
from ..adapters.typescript._ts_regex import TSFrameworkAdapter
from ..adapters.yaml_adapters import (
    AutoGenYAMLAdapter,
    CrewAIYAMLAdapter,
    LLMYAMLConfigAdapter,
    PromptFileAdapter,
)
from ..config import AiSbomConfig
from ..core.application_summary import build_scan_summary
from ..core.ts_parser import TSParseResult
from ..core.ts_parser import parse_typescript as _parse_ts_impl
from ..deps import DependencyScanner
from ..models import (
    AiSbomDocument,
    AuthDetail,
    DataHandlingDetail,
    Edge,
    EncryptionDetail,
    Evidence,
    Node,
    NodeMetadata,
    RateLimitDetail,
    SourceLocation,
)
from ..normalization import canonicalize_text
from ..types import ComponentType, RelationshipType
from .postprocess import (
    _dedup_by_location,
    _dedup_by_name_prefix,
    _dedup_deployment_nodes,
    _dedup_generic_endpoints,
    _dedup_model_variants,
    _improve_generic_node_names,
    _make_scan_summary,
    _suppress_generic_tech_regex_datastore,
    _suppress_non_code_model_datastore,
)

_log = get_logger(__name__)

# File extensions that warrant Python AST parsing
_PYTHON_EXTENSIONS = {".py", ".pyw"}
# SQL schema files: scanned by DataClassificationSQLAdapter
_SQL_EXTENSIONS = {".sql"}
# Jupyter notebooks: cells are extracted and parsed as Python
_NOTEBOOK_EXTENSIONS = {".ipynb"}
# TypeScript/JavaScript: tree-sitter (or regex fallback) via core/ts_parser
_TYPESCRIPT_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}
_GO_EXTENSIONS = {".go"}
# Dockerfile: extensionless file named "Dockerfile" or suffixed ".dockerfile"
_DOCKERFILE_EXTENSIONS = {".dockerfile"}
_DOCKERFILE_NAMES = {"dockerfile"}  # lower-cased stem match

# ---------------------------------------------------------------------------
# Source-tier constants for dedup precedence: CODE > IAC > DOCS
# ---------------------------------------------------------------------------
_TIER_CODE = "code"
_TIER_IAC = "iac"
_TIER_DOCS = "docs"
# Lower rank number = higher precedence during dedup
_TIER_RANK: dict[str, int] = {_TIER_CODE: 0, _TIER_IAC: 1, _TIER_DOCS: 2}


def _strip_notebook_outputs(content: str) -> str:
    """Return notebook source code only, removing cell outputs to avoid base64 false matches.

    Jupyter notebooks embed base64-encoded images in ``outputs`` — these can
    contain arbitrary byte patterns that look like model names (e.g. 'o5' or
    'o7' inside PNG data).  Stripping outputs leaves only the code/markdown
    that is meaningful for SBOM detection.
    """
    try:
        nb = json.loads(content)
        for cell in nb.get("cells", []):
            cell["outputs"] = []
            cell.pop("execution_count", None)
        return json.dumps(nb)
    except Exception as exc:
        _log.debug("strip_notebook_outputs: failed to parse notebook JSON: %s", exc)
        return content


_PYTHON_COMMENT_LINE_RE = re.compile(r"^\s*#.*$", re.MULTILINE)


def _strip_python_comments(content: str) -> str:
    """Remove Python single-line comments before regex scanning.

    Prevents regex adapters (e.g. ``model_generic``) from matching model names
    that appear only in commented-out migration examples, like::

        # llm = ChatOllama(model="llama3:8b")

    Inline comments within code lines are not removed to preserve the rest of
    the line for other pattern matching.
    """
    return _PYTHON_COMMENT_LINE_RE.sub("", content)


# Pattern for variable names that explicitly indicate prompt content.
# Intentionally conservative: matches only names ending with _PROMPT (case-insensitive).
# Avoids: FORMAT_INSTRUCTIONS, EVAL_*, GROUNDTRUTH_*, etc.
_PROMPT_CONST_NAME_RE = re.compile(
    r"(?:^|_)PROMPT$",  # *_PROMPT or bare PROMPT (case-insensitive)
    re.IGNORECASE,
)
# Keywords in variable names that indicate an evaluation/testing artifact rather
# than a production AI prompt.  Names containing any of these are skipped.
_PROMPT_CONST_SKIP_WORDS = frozenset(
    {"EVAL", "EVALUATE", "EVALUATION", "GROUNDTRUTH", "GROUND_TRUTH", "TEST_PROMPT", "MOCK_PROMPT"}
)
_MIN_PROMPT_CONST_LENGTH = 80  # minimum char count to treat as a real prompt


def _extract_python_prompt_constants(
    parse_result: Any,
    rel_path: str,
) -> list["ComponentDetection"]:
    """Emit PROMPT nodes for module-level ALL_CAPS prompt constants.

    Runs on every Python file regardless of which framework adapters handled it,
    so that prompt-only modules (no langchain/langgraph imports) are still
    processed.  Examples that trigger this::

        PARSER_PROMPT = \"\"\"You are a document parsing specialist...\"\"\"
        ANALYST_PROMPT = \"\"\"You are a senior document analyst...\"\"\"

    Only captures string literals whose ``context`` (variable name) matches the
    ``_PROMPT_VAR_NAME_RE`` pattern and whose length exceeds
    ``_MIN_PROMPT_CONST_LENGTH``.
    """
    from ..adapters.base import ComponentDetection
    from ..normalization import canonicalize_text
    from ..types import ComponentType

    detections: list[ComponentDetection] = []
    for lit in parse_result.string_literals:
        if lit.is_docstring:
            continue
        # Only process literals that came from module-level variable assignments,
        # not strings captured from inside function/class bodies.
        if not lit.is_module_assignment:
            continue
        ctx = lit.context or ""
        # Skip private/dunder names (e.g. _PYDANTIC_FORMAT_INSTRUCTIONS)
        if ctx.startswith("_"):
            continue
        if not _PROMPT_CONST_NAME_RE.search(ctx):
            continue
        if len(lit.value) < _MIN_PROMPT_CONST_LENGTH:
            continue
        # Skip evaluation / testing prompts (not production AI prompts)
        ctx_upper = ctx.upper()
        if any(skip in ctx_upper for skip in _PROMPT_CONST_SKIP_WORDS):
            continue
        # Avoid duplicate with framework adapters by using a file-scoped canon
        dname = ctx
        canon = canonicalize_text(ctx.lower())
        template_vars = re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", lit.value)
        detections.append(
            ComponentDetection(
                component_type=ComponentType.PROMPT,
                canonical_name=canon,
                display_name=dname,
                adapter_name="python_prompt_const",
                priority=50,
                confidence=0.80,
                metadata={
                    "role": "system" if "system" in ctx.lower() else "unspecified",
                    "content": lit.value,
                    "char_count": len(lit.value),
                    "is_template": bool(template_vars),
                    "template_variables": template_vars,
                },
                file_path=rel_path,
                line=lit.line,
                snippet=lit.value[:80] + ("..." if len(lit.value) > 80 else ""),
                evidence_kind="ast_constant",
            )
        )
    return detections


def _run_prompt_detector(
    source: str,
    rel_path: str,
) -> "list[ComponentDetection]":
    """Run :class:`PromptDetector` and convert results to ``ComponentDetection`` objects.

    The ``PromptDetector`` returns ``Node`` objects; this bridge function
    re-packages them so they flow through the standard ``_merge_detection``
    pipeline (dedup, tier classification, evidence tracking).
    """
    from ..adapters.base import ComponentDetection  # noqa: PLC0415
    from ..normalization import canonicalize_text  # noqa: PLC0415
    from ..types import ComponentType  # noqa: PLC0415
    from .prompt_detector import PromptDetector  # noqa: PLC0415

    path = Path(rel_path)
    try:
        nodes = PromptDetector().detect(path, source)
    except Exception as exc:
        _log.debug("prompt_detector: failed on %s: %s", rel_path, exc)
        return []

    detections: list[ComponentDetection] = []
    for node in nodes:
        extras = node.metadata.extras if node.metadata else {}
        content = extras.get("content", "")
        detections.append(
            ComponentDetection(
                component_type=ComponentType.PROMPT,
                canonical_name=canonicalize_text(node.name.lower()),
                display_name=node.name,
                adapter_name="prompt_detector",
                priority=40,
                confidence=node.confidence,
                metadata={
                    "role": "system",
                    "content": content,
                    "char_count": len(content),
                    "is_template": extras.get("is_template", False),
                    "injection_risk_score": extras.get("injection_risk_score", 0.0),
                },
                file_path=rel_path,
                line=0,
                snippet=content[:80] + ("..." if len(content) > 80 else ""),
                evidence_kind="ast_prompt_detector",
            )
        )
    return detections


_PROMPT_DICT_NAME_RE = re.compile(
    r"(?:prompt|persona|instruction|system_message)",
    re.IGNORECASE,
)
_MIN_PROMPT_DICT_VALUE_LENGTH = 120


def _extract_python_prompt_dicts(
    source: str,
    rel_path: str,
) -> "list[ComponentDetection]":
    """Detect prompt strings stored as values in module-level Python dicts.

    Targets patterns like ``PROMPT_REGISTRY = {"key": "long prompt..."}``,
    including the ``" ".join([...])`` fragment pattern.
    """
    import ast as _ast  # noqa: PLC0415

    from ..adapters.base import ComponentDetection  # noqa: PLC0415
    from ..normalization import canonicalize_text  # noqa: PLC0415
    from ..types import ComponentType  # noqa: PLC0415

    try:
        tree = _ast.parse(source)
    except SyntaxError as exc:
        _log.debug("prompt_dicts: failed to parse %s: %s", rel_path, exc)
        return []

    # First pass: collect module-level list/tuple string constants so that
    # ``" ".join(_FRAGMENTS)`` can be resolved when the join argument is a
    # variable reference rather than an inline list literal.
    _module_lists: dict[str, list[str]] = {}
    for node in _ast.iter_child_nodes(tree):
        if isinstance(node, _ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, _ast.Name) and isinstance(node.value, (_ast.List, _ast.Tuple)):
                parts = []
                for elt in node.value.elts:
                    if isinstance(elt, _ast.Constant) and isinstance(elt.value, str):
                        parts.append(elt.value)
                    else:
                        parts = []
                        break
                if parts:
                    _module_lists[t.id] = parts

    detections: list[ComponentDetection] = []

    for node in _ast.iter_child_nodes(tree):
        targets: list[str] = []
        value: _ast.expr | None = None

        if isinstance(node, _ast.Assign):
            for t in node.targets:
                if isinstance(t, _ast.Name):
                    targets.append(t.id)
            value = node.value
        elif isinstance(node, _ast.AnnAssign) and isinstance(node.target, _ast.Name):
            targets.append(node.target.id)
            value = node.value

        if not targets or value is None or not isinstance(value, _ast.Dict):
            continue

        for var_name in targets:
            if not _PROMPT_DICT_NAME_RE.search(var_name):
                continue

            for dk, dv in zip(value.keys, value.values):
                dict_key = dk.value if isinstance(dk, _ast.Constant) and isinstance(dk.value, str) else None
                if dict_key is None:
                    continue

                prompt_text = _resolve_dict_value(dv, _module_lists)
                if prompt_text is None or len(prompt_text) < _MIN_PROMPT_DICT_VALUE_LENGTH:
                    continue

                canon = canonicalize_text(f"{var_name}_{dict_key}".lower())
                template_vars = re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", prompt_text)
                detections.append(
                    ComponentDetection(
                        component_type=ComponentType.PROMPT,
                        canonical_name=canon,
                        display_name=f"{var_name}[{dict_key}]",
                        adapter_name="python_prompt_dict",
                        priority=45,
                        confidence=0.82,
                        metadata={
                            "role": "system" if "system" in dict_key.lower() else "unspecified",
                            "content": prompt_text[:500],
                            "char_count": len(prompt_text),
                            "is_template": bool(template_vars),
                            "template_variables": template_vars,
                            "dict_name": var_name,
                            "dict_key": dict_key,
                        },
                        file_path=rel_path,
                        line=getattr(dk, "lineno", 0),
                        snippet=prompt_text[:80] + ("..." if len(prompt_text) > 80 else ""),
                        evidence_kind="ast_dict_value",
                    )
                )

    return detections


def _resolve_dict_value(
    node: Any,
    module_lists: dict[str, list[str]] | None = None,
) -> str | None:
    """Extract a string from an AST node, handling ``" ".join([...])``."""
    import ast as _ast  # noqa: PLC0415

    if isinstance(node, _ast.Constant) and isinstance(node.value, str):
        return node.value

    if isinstance(node, _ast.JoinedStr):
        return None

    # " ".join([...]) or " ".join(variable)
    if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Attribute):
        if (
            node.func.attr == "join"
            and isinstance(node.func.value, _ast.Constant)
            and isinstance(node.func.value.value, str)
            and node.args
        ):
            sep = node.func.value.value
            arg = node.args[0]
            if isinstance(arg, (_ast.List, _ast.Tuple)):
                parts: list[str] = []
                for elt in arg.elts:
                    if isinstance(elt, _ast.Constant) and isinstance(elt.value, str):
                        parts.append(elt.value)
                    else:
                        return None
                return sep.join(parts)
            if isinstance(arg, _ast.Name) and module_lists and arg.id in module_lists:
                return sep.join(module_lists[arg.id])

    return None


_IAC_EXTENSIONS = {".tf", ".tfvars", ".hcl", ".bicep", ".jinja", ".yaml", ".yml", ".json"}
# Bicep and Jinja IaC-specific extensions (not processed by the generic YAML phase)
_BICEP_EXTENSIONS = {".bicep"}
_JINJA_EXTENSIONS = {".jinja"}
_DOCS_EXTENSIONS = {
    ".md",
    ".rst",
    ".txt",
    ".html",
    ".htm",
    ".adoc",
    ".mk",
}
# Shell / script extensions are NOT in _DOCS_EXTENSIONS so that regex adapters
# (deployment_generic, privilege, auth, etc.) can scan them.  They receive the
# IAC source tier so their evidence confidence is scored like infrastructure
# files rather than code.
_SCRIPT_EXTENSIONS = {".sh", ".bash", ".zsh", ".fish", ".ps1"}


def _should_skip_path_parts(parts: tuple[str, ...]) -> bool:
    skip_dirs = {
        ".git", "__pycache__", "node_modules", ".tox", ".claude", "site-packages",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".pytype", "logs", "log",
        "reports", "nuguard-test-results", "test-results",
        # Build/generated-output directories — large volumes of generated code
        # (bundled JS, compiled artefacts) that match include_extensions but
        # carry no AIBOM signal, and just burn the file/byte budget.
        "dist", "build", ".next", ".nuxt", ".output", "target", "vendor",
        ".terraform", "coverage", ".turbo", ".parcel-cache", ".serverless",
        "bin", "obj", ".gradle", ".dvc",
        # Generated/vendored code and test artefacts — no AIBOM signal.
        "generated", "third_party", "external", "__snapshots__",
        ".ipynb_checkpoints", ".idea",
    }
    for part in parts:
        if part in skip_dirs:
            return True
        if part == ".venv" or part == "venv":
            return True
        if part.startswith(".venv") or re.fullmatch(r"venv[\w.-]*", part):
            return True
        if part.endswith(".egg-info") or part.endswith(".dist-info"):
            return True
    return False


# Filenames/suffixes with no AIBOM signal: generated code stubs, snapshot
# tests, Terraform state, and OS/editor cruft that can slip past the
# directory-level skip list above.
_SKIP_FILENAME_SUFFIXES = (
    "_pb2.py", "_pb2_grpc.py", ".pb.go", "_grpc.py", ".pbtxt",
    ".snap", ".tfstate", ".tfstate.backup", ".terraform.lock.hcl",
    ".DS_Store",
)
_SKIP_FILENAMES = {"Thumbs.db"}


def _should_skip_filename(name: str) -> bool:
    if name in _SKIP_FILENAMES:
        return True
    return any(name.endswith(suffix) for suffix in _SKIP_FILENAME_SUFFIXES)
_DOCS_STEMS = {
    "readme",
    "changelog",
    "license",
    "contributing",
    "makefile",
    "authors",
    "notice",
    "roadmap",
    "security",
    "support",
    # Dependency lock files — auto-generated, not meaningful for AI component detection
    "pnpm-lock",
    "package-lock",
    "yarn",  # yarn.lock
    "composer",  # composer.lock (PHP)
    "gemfile-lock",  # Gemfile.lock (Ruby)
    # Pre-commit / tooling configs — not AI application code
    ".pre-commit-config",
}


def _classify_source_tier(file_path: str, adapter_name: str, evidence_kind: str) -> str:
    """Classify a detection into one of three source tiers.

    CODE (0) > IAC (1) > DOCS (2).

    AST-derived evidence (``evidence_kind != "regex"``) is always CODE tier
    regardless of the file extension, since it came from actual program
    structure.  Regex detections are classified by file extension / adapter
    name so that the same component detected in source code can override a
    weaker mention in a README or Dockerfile.
    """
    # AST evidence always counts as code — the most authoritative source
    if evidence_kind != "regex":
        return _TIER_CODE
    # Dockerfile adapter is IaC regardless of file name
    if adapter_name == "dockerfile":
        return _TIER_IAC
    if not file_path:
        return _TIER_CODE
    p = Path(file_path)
    suffix = p.suffix.lower()
    stem = p.stem.lower()
    if suffix in _DOCS_EXTENSIONS or stem in _DOCS_STEMS:
        return _TIER_DOCS
    if suffix in _IAC_EXTENSIONS or suffix in _SCRIPT_EXTENSIONS:
        return _TIER_IAC
    # Python / TypeScript / notebook files processed by regex fallback → code
    return _TIER_CODE


@dataclass
class _NodeAccumulator:
    """Accumulates detections for a single logical component during dedup.

    ``source_tiers`` records every tier ("code", "iac", "docs") that has
    contributed a detection, enabling cross-tier corroboration and ensuring
    that code-level attribution always takes precedence over IaC/docs.
    ``best_tier_rank`` tracks the rank of the highest-priority tier seen so
    far (lower number = better); used to decide whether incoming metadata
    should override or merely fill gaps in the accumulated metadata.
    """

    component_type: ComponentType
    canonical_name: str
    display_name: str
    adapter_name: str
    priority: int
    confidence: float
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    relationships: list[RelationshipHint] = field(default_factory=list)
    # Source-tier tracking (populated by _merge_detection)
    source_tiers: set[str] = field(default_factory=set)
    best_tier_rank: int = 99  # 0=code, 1=iac, 2=docs; 99=uninitialised


def _load_gitignore_matcher(root: Path) -> "Callable[[str], bool] | None":
    """Return a predicate that matches paths against the repo's ``.gitignore``.

    Returns ``None`` when no ``.gitignore`` exists or the ``pathspec`` library
    is unavailable (graceful degradation).
    """
    gitignore_path = root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    try:
        import pathspec  # noqa: PLC0415
    except ImportError:
        _log.debug(".gitignore found but pathspec not installed; skipping")
        return None
    try:
        patterns = gitignore_path.read_text(encoding="utf-8", errors="ignore")
        spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns.splitlines())
        return spec.match_file  # type: ignore[return-value]
    except Exception as exc:
        _log.warning("failed to parse .gitignore: %s", exc)
        return None


class AiSbomExtractor:
    """Extract an AI SBOM from a local path or remote git repository.

    Parameters
    ----------
    framework_adapters:
        AST-aware adapters to run on Python files.  Defaults to all built-in
        framework adapters (LangGraph, OpenAI Agents, AutoGen, Semantic Kernel,
        CrewAI, LlamaIndex, LLMClients).
    regex_adapters:
        Regex-based fallback adapters for non-Python files.  Defaults to the
        built-in generic component detectors.
    """

    def __init__(
        self,
        framework_adapters: tuple[FrameworkAdapter, ...] | None = None,
        regex_adapters: tuple[DetectionAdapter, ...] | None = None,
        sql_adapters: tuple[Any, ...] | None = None,
        dockerfile_adapter: DockerfileAdapter | None = None,
        yaml_adapters: tuple[Any, ...] | None = None,
        json_adapters: tuple[Any, ...] | None = None,
        nginx_adapter: NginxAdapter | None = None,
        prompt_file_adapter: PromptFileAdapter | None = None,
        iac_adapters: tuple[Any, ...] | None = None,
        load_plugins: bool = False,
    ) -> None:
        from ..plugins import load_plugins as _load_plugins

        base_adapters = (
            framework_adapters if framework_adapters is not None else default_framework_adapters()
        )
        if load_plugins:
            plugin_adapters: tuple[FrameworkAdapter, ...] = tuple(_load_plugins())
            combined = base_adapters + plugin_adapters
            self.framework_adapters: tuple[FrameworkAdapter, ...] = tuple(
                sorted(combined, key=lambda a: getattr(a, "priority", 10))
            )
        else:
            self.framework_adapters = base_adapters
        self.regex_adapters = regex_adapters if regex_adapters is not None else default_registry()
        self.sql_adapters = (
            sql_adapters
            if sql_adapters is not None
            else (DataClassificationSQLAdapter(), PromptSQLAdapter())
        )
        self.dockerfile_adapter = (
            dockerfile_adapter if dockerfile_adapter is not None else DockerfileAdapter()
        )
        self.yaml_adapters = (
            yaml_adapters
            if yaml_adapters is not None
            else (CrewAIYAMLAdapter(), AutoGenYAMLAdapter(), LLMYAMLConfigAdapter())
        )
        self.json_adapters = (
            json_adapters
            if json_adapters is not None
            else (
                GoogleADKJSONAdapter(),
                OpenAIToolsJSONAdapter(),
                AgentJSONConfigAdapter(),
                LLMJSONConfigAdapter(),
                PromptJSONAdapter(),
                MCPServerJSONAdapter(),
            )
        )
        self.nginx_adapter = nginx_adapter if nginx_adapter is not None else NginxAdapter()
        self.prompt_file_adapter = (
            prompt_file_adapter if prompt_file_adapter is not None else PromptFileAdapter()
        )
        self.iac_adapters: tuple[Any, ...] = (
            iac_adapters
            if iac_adapters is not None
            else (
                K8sAdapter(),
                TerraformAdapter(),
                CloudFormationAdapter(),
                BicepAdapter(),
                GcpDeploymentManagerAdapter(),
                GitHubActionsAdapter(),
            )
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_from_path(
        self,
        path: str | Path,
        config: AiSbomConfig,
        source_ref: str | None = None,
        branch: str | None = None,
    ) -> AiSbomDocument:
        """Extract an SBOM from a directory on the local filesystem."""
        root = Path(path).resolve()
        _log.info("scanning files under %s", root)
        doc = AiSbomDocument(target=source_ref or str(root))
        node_map: dict[tuple[ComponentType, str], _NodeAccumulator] = {}
        # Classification-only metadata from data_classification adapters (not emitted as nodes)
        _dc_metadata: list[dict[str, Any]] = []
        # Keep full file contents only when LLM enrichment is enabled.
        keep_full_contents = bool(config.enable_llm)
        file_contents: dict[str, str] = {}
        # LOC tracking: lines of code per relative file path
        file_loc: dict[str, int] = {}
        # Import tracking per file for dependency cross-referencing
        _file_imports: dict[str, set[str]] = {}
        # Minified JS files detected during the scan (for SC-019)
        _minified_js_files: list[str] = []
        # Summary builder only needs a bounded content sample.
        files_sample: list[tuple[str, str]] = []
        files_scanned = 0
        files_sample_bytes = 0
        full_cache_files = 0
        full_cache_bytes = 0

        def _human_bytes(num_bytes: int) -> str:
            if num_bytes < 1024:
                return f"{num_bytes} B"
            if num_bytes < 1024 * 1024:
                return f"{num_bytes / 1024:.2f} KiB"
            if num_bytes < 1024 * 1024 * 1024:
                return f"{num_bytes / (1024 * 1024):.2f} MiB"
            return f"{num_bytes / (1024 * 1024 * 1024):.2f} GiB"

        # Pre-pass: build a cross-file Pydantic model schema index so FastAPI (and Flask)
        # adapters can resolve request-body models that are imported from other modules,
        # and a cross-file FastAPI router-prefix index so nested
        # app.include_router(router, prefix=...) mounts compose correctly across files
        # (e.g. a router declared in config/__init__.py and mounted with a prefix in
        # server.py). This is a fast AST-only pass — no detection, no merging.
        _global_model_schemas: dict[str, dict[str, str]] = {}
        _global_router_prefixes: dict[str, str] = {}
        # rel_path -> [tool_function_name, ...] for files with @tool-decorated
        # functions — used below (after the main per-file loop) to resolve
        # cross-file TOOL -[ACCESSES]-> DATASTORE hints. Populated outside the
        # try so a partial pre-pass still contributes what it found.
        _tool_defining_files: dict[str, list[str]] = {}
        # (rel_path, module_level_var_name) -> datastore canonical_name, populated
        # by the main per-file loop below as PythonDatastoreAdapter's real,
        # fully-disambiguated detections come in — see the DATASTORE branch
        # inside the Python-file adapter loop.
        _ds_symbol_index: dict[tuple[str, str], str] = {}
        try:
            import ast as _ast  # noqa: PLC0415  # isort:skip
            from nuguard.sbom.adapters.python.datastores import (  # noqa: PLC0415, I001
                _collect_tool_decorated_function_names as _collect_py_tool_names,
            )
            from nuguard.sbom.adapters.python.fastapi_adapter import (  # noqa: PLC0415, I001
                _collect_include_router_calls as _collect_py_includes,
                _collect_model_schemas as _collect_py_models,
                _collect_router_declarations as _collect_py_router_decls,
                _collect_router_imports as _collect_py_router_imports,
            )

            # rel_path -> {var_name: own_prefix}
            _router_decls: dict[str, dict[str, str]] = {}
            # rel_path -> [(receiver_var, included_var, mount_prefix)]
            _router_includes: dict[str, list[tuple[str, str | None, str]]] = {}
            # rel_path -> {local_name: (relative_level, dotted_module, original_name)}
            _router_imports: dict[str, dict[str, tuple[int, str, str]]] = {}
            _all_py_rel_paths: set[str] = set()

            for _py_path, _ in self._iter_files(root, config):
                if _py_path.suffix != ".py":
                    continue
                _router_rel = str(_py_path.relative_to(root))
                _all_py_rel_paths.add(_router_rel)
                try:
                    _py_src = _py_path.read_text(encoding="utf-8", errors="ignore")
                    _py_tree = _ast.parse(_py_src)
                except Exception:
                    continue
                _global_model_schemas.update(_collect_py_models(_py_tree))
                _router_decls[_router_rel] = _collect_py_router_decls(_py_tree)
                _router_includes[_router_rel] = _collect_py_includes(_py_tree)
                _router_imports[_router_rel] = _collect_py_router_imports(_py_tree)
                _tool_names = _collect_py_tool_names(_py_tree)
                if _tool_names:
                    _tool_defining_files[_router_rel] = _tool_names

            def _resolve_import_to_relpath(rel_path: str, level: int, module: str) -> str | None:
                """Best-effort resolve a (possibly relative) import to a scanned .py rel path."""
                if level > 0:
                    parts = rel_path.replace("\\", "/").split("/")[:-1]
                    up = level - 1
                    if up > 0:
                        parts = parts[:-up] if up <= len(parts) else []
                    base_parts = parts + ([p for p in module.split(".") if p] if module else [])
                else:
                    if not module:
                        return None
                    base_parts = [p for p in module.split(".") if p]
                if not base_parts:
                    return None
                candidate_module = "/".join(base_parts) + ".py"
                candidate_pkg = "/".join([*base_parts, "__init__"]) + ".py"
                for known in _all_py_rel_paths:
                    _known_norm = known.replace("\\", "/")
                    if _known_norm == candidate_module or _known_norm == candidate_pkg:
                        return known
                # Absolute imports may be rooted at a package name outside the scan
                # root's own path prefix — fall back to a suffix match.
                suffix_module = "/".join(base_parts) + ".py"
                suffix_pkg = "/".join([*base_parts, "__init__"]) + ".py"
                for known in _all_py_rel_paths:
                    _known_norm = known.replace("\\", "/")
                    if _known_norm.endswith("/" + suffix_module) or _known_norm.endswith("/" + suffix_pkg):
                        return known
                return None

            _prefix_cache: dict[tuple[str, str], str] = {}

            def _resolve_router_prefix(
                rel_path: str, var_name: str, _seen: frozenset[tuple[str, str]] = frozenset()
            ) -> str:
                key = (rel_path, var_name)
                if key in _prefix_cache:
                    return _prefix_cache[key]
                if key in _seen:
                    return ""  # cycle guard
                own_prefix = _router_decls.get(rel_path, {}).get(var_name, "")

                mount_prefix = ""
                parent_composed = ""
                for _mrel, _calls in _router_includes.items():
                    _found = False
                    for receiver, included, m_prefix in _calls:
                        if included is None:
                            continue
                        target_rel: str | None = None
                        target_var: str | None = None
                        if "." in included:
                            # Module-attribute style: include_router(chat.router, ...)
                            # where "chat" was imported via `from server.api import chat`.
                            # Resolve "chat" to its own dotted module path, then combine
                            # with the original import's module to get the submodule's
                            # dotted path (e.g. "server.api" + "chat" -> "server.api.chat").
                            _mod_alias, _attr = included.split(".", 1)
                            _imp = _router_imports.get(_mrel, {}).get(_mod_alias)
                            if _imp is not None:
                                _lvl, _mod, _orig = _imp
                                _combined_mod = f"{_mod}.{_orig}" if _mod else _orig
                                _resolved = _resolve_import_to_relpath(_mrel, _lvl, _combined_mod)
                                if _resolved is not None:
                                    target_rel, target_var = _resolved, _attr
                        elif included in _router_decls.get(_mrel, {}):
                            target_rel, target_var = _mrel, included
                        else:
                            _imp = _router_imports.get(_mrel, {}).get(included)
                            if _imp is not None:
                                _lvl, _mod, _orig = _imp
                                _resolved = _resolve_import_to_relpath(_mrel, _lvl, _mod)
                                if _resolved is not None:
                                    target_rel, target_var = _resolved, _orig
                        if target_rel == rel_path and target_var == var_name:
                            mount_prefix = m_prefix
                            if receiver != "app":
                                parent_composed = _resolve_router_prefix(
                                    _mrel, receiver, _seen | {key}
                                )
                            _found = True
                            break
                    if _found:
                        break

                composed = f"{parent_composed}{mount_prefix}{own_prefix}"
                _prefix_cache[key] = composed
                return composed

            for _router_rel, _vars in _router_decls.items():
                for _var in _vars:
                    _global_router_prefixes[f"{_router_rel}::{_var}"] = _resolve_router_prefix(
                        _router_rel, _var
                    )
        except Exception as _pre_exc:
            _log.debug("cross-file model/router pre-pass failed (non-fatal): %s", _pre_exc)
            _resolve_import_to_relpath = None  # type: ignore[assignment]
            _router_imports = {}

        def _iter_ts_sources(
            names: tuple[str, ...] | None = None,
        ) -> Iterator[tuple[str, str]]:
            """Yield (rel_path, source) for every scanned .ts/.tsx file, optionally
            restricted to specific file names (e.g. "main.ts"). Shared by the
            single-file TS pre-passes below — each has its own try/except so one
            collector's failure doesn't prevent the others from running.
            """
            for _ts_path, _ in self._iter_files(root, config):
                if _ts_path.suffix not in (".ts", ".tsx"):
                    continue
                if names is not None and _ts_path.name not in names:
                    continue
                try:
                    _ts_src = _ts_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                yield str(_ts_path.relative_to(root)), _ts_src

        # Cross-file TS DTO field-type index — shares the same
        # `_global_model_schemas` dict (and `set_global_model_schemas` hook) as
        # the Python Pydantic-model pre-pass above, so NestJSAdapter can resolve
        # a `@Body() dto: SendMessageDto` parameter even when the DTO
        # `interface`/`class` is declared in a different file (e.g. a
        # `*.service.ts` sibling of the controller that references it).
        try:
            from nuguard.sbom.adapters.typescript.nestjs_adapter import (  # noqa: PLC0415
                collect_dto_schemas as _collect_ts_dtos,
            )

            for _, _ts_src in _iter_ts_sources():
                _global_model_schemas.update(_collect_ts_dtos(_ts_src))
        except Exception as _ts_pre_exc:
            _log.debug("cross-file TS DTO pre-pass failed (non-fatal): %s", _ts_pre_exc)

        # NestJS app-wide route prefix (`app.setGlobalPrefix('api/v1')` in
        # main.ts) — applies outside every controller's own
        # `@Controller('prefix')`; see nestjs_adapter.py's `_compose_path`.
        _global_route_prefix = ""
        _global_route_prefix_exclude: list[str] = []
        try:
            from nuguard.sbom.adapters.typescript.nestjs_adapter import (  # noqa: PLC0415
                _extract_global_prefix as _extract_ts_global_prefix,
            )

            for _, _ts_src in _iter_ts_sources(names=("main.ts", "main.tsx")):
                _found = _extract_ts_global_prefix(_ts_src)
                if _found:
                    _global_route_prefix, _global_route_prefix_exclude = _found
                    break
        except Exception as _prefix_exc:
            _log.debug("NestJS global-prefix pre-pass failed (non-fatal): %s", _prefix_exc)

        # Cross-file TS class hierarchy (class X / class X extends Y) — feeds
        # the hand-rolled multi-agent orchestration heuristic, which needs to
        # cite a base class's real definition site even when it's declared in
        # a different file than the orchestrator that sequences its
        # subclasses (docs/sbom-fix2.md #6).
        _global_ts_class_bases: dict[str, str] = {}
        _global_ts_class_locations: dict[str, tuple[str, int]] = {}
        try:
            from nuguard.sbom.adapters.typescript.agent_orchestrator import (  # noqa: PLC0415
                collect_class_hierarchy as _collect_ts_class_hierarchy,
            )

            for _ts_rel, _ts_src in _iter_ts_sources():
                _bases, _locs = _collect_ts_class_hierarchy(_ts_src, _ts_rel)
                _global_ts_class_bases.update(_bases)
                _global_ts_class_locations.update(_locs)
        except Exception as _hier_exc:
            _log.debug("TS class-hierarchy pre-pass failed (non-fatal): %s", _hier_exc)

        # Inject the global indices into adapters that support them.
        _global_index_setters = {
            "set_global_model_schemas": (_global_model_schemas,),
            "set_global_router_prefixes": (_global_router_prefixes,),
            "set_global_route_prefix": (
                _global_route_prefix,
                _global_route_prefix_exclude,
            ),
            "set_global_class_hierarchy": (
                _global_ts_class_bases,
                _global_ts_class_locations,
            ),
        }
        for _adapter in self.framework_adapters:
            for _setter_name, _args in _global_index_setters.items():
                _setter = getattr(_adapter, _setter_name, None)
                if _setter is not None:
                    _setter(*_args)

        for file_path, file_size in self._iter_files(root, config):
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                _log.warning("skipping unreadable file %s: %s", file_path, exc)
                continue

            # Skip JSON files that were generated by nuguard itself — they are
            # output artefacts, not application source evidence.
            if file_path.suffix.lower() == ".json" and '"generator"' in content[:512]:
                try:
                    import json as _json
                    _peek = _json.loads(content)
                    if isinstance(_peek, dict) and str(_peek.get("generator", "")).lower().startswith("nuguard"):
                        _log.debug("skipping nuguard-generated file: %s", file_path)
                        continue
                except Exception:
                    pass

            files_scanned += 1
            rel_path = str(file_path.relative_to(root))
            content_bytes = file_size
            # Track LOC for every scanned file
            file_loc[rel_path] = content.count("\n") + 1
            if keep_full_contents:
                file_contents[rel_path] = content
                full_cache_files += 1
                full_cache_bytes += content_bytes
            if len(files_sample) < 200:
                files_sample.append((rel_path, content))
                files_sample_bytes += content_bytes
            suffix = file_path.suffix.lower()
            is_python = suffix in _PYTHON_EXTENSIONS
            is_notebook = suffix in _NOTEBOOK_EXTENSIONS
            is_typescript = suffix in _TYPESCRIPT_EXTENSIONS
            is_go = suffix in _GO_EXTENSIONS
            is_sql = suffix in _SQL_EXTENSIONS
            is_dockerfile = (
                suffix in _DOCKERFILE_EXTENSIONS or file_path.name.lower() in _DOCKERFILE_NAMES
            )
            is_nginx_conf = is_nginx_file(rel_path)

            # Phase 0a: Prompt file detection (before docs-tier skip)
            # .txt files in prompts/ dirs are normally skipped by the regex pass;
            # run the prompt adapter first so they are not silently ignored.
            if suffix == ".txt" and not is_dockerfile:
                try:
                    for det in self.prompt_file_adapter.scan(content, rel_path):
                        self._merge_detection(node_map, det)
                except Exception as exc:
                    _log.warning("prompt_file adapter failed on %s: %s", rel_path, exc)

            # Phase 1a: Python AST-aware framework adapters
            if is_python or is_notebook:
                py_source = content
                if is_notebook:
                    py_source = self._extract_notebook_python(content)
                    if not py_source:
                        _log.debug("no code cells in notebook %s", rel_path)

                if py_source:
                    parse_result = self._parse_python(py_source)
                    if parse_result is not None:
                        if parse_result.parse_error:
                            _log.debug(
                                "AST parse error in %s: %s", rel_path, parse_result.parse_error
                            )
                        imported_modules: set[str] = {
                            imp.module for imp in parse_result.imports if imp.module
                        }
                        # Capture top-level package names for dependency cross-referencing
                        _file_imports[rel_path] = {
                            imp.module.split(".")[0].lower()
                            for imp in parse_result.imports
                            if imp.module
                        }
                        for adapter in self.framework_adapters:
                            # Skip TypeScript/Go adapters for Python/notebook files
                            if isinstance(adapter, (TSFrameworkAdapter, GoFrameworkAdapter)):
                                continue
                            if not adapter.can_handle(imported_modules):
                                continue
                            _log.debug("running adapter %r on %s", adapter.name, rel_path)
                            try:
                                detections = adapter.extract(py_source, rel_path, parse_result)
                            except Exception as exc:
                                _log.warning(
                                    "adapter %r failed on %s: %s",
                                    adapter.name,
                                    rel_path,
                                    exc,
                                )
                                continue
                            for det in detections:
                                if (
                                    det.component_type == ComponentType.DATASTORE
                                    and det.metadata.get("source") in ("sql_schema", "python_model")
                                ):
                                    _dc_metadata.append(det.metadata)
                                else:
                                    self._merge_detection(node_map, det)
                                    _ds_symbol = det.metadata.get("module_level_symbol")
                                    if det.component_type == ComponentType.DATASTORE and _ds_symbol:
                                        # Record which (file, local variable name)
                                        # resolved to this datastore, using this
                                        # adapter's own fully-disambiguated
                                        # provider resolution — consumed below
                                        # (after this file loop) to resolve
                                        # cross-file TOOL -> DATASTORE hints.
                                        _ds_symbol_index[(rel_path, _ds_symbol)] = det.canonical_name

                        # Phase 1a-prime: module-level Python prompt constants.
                        # Runs on ALL .py files regardless of framework imports so that
                        # prompt-only modules (e.g. prompts.py with no langchain imports)
                        # are still processed.
                        for det in _extract_python_prompt_constants(parse_result, rel_path):
                            self._merge_detection(node_map, det)

                        # Phase 1a″: PromptDetector — system-prompt variables,
                        # f-string prompts, and dict-based {role: "system"} messages.
                        for det in _run_prompt_detector(content, rel_path):
                            self._merge_detection(node_map, det)

                        # Phase 1a‴: dict-based prompt stores (PROMPT_REGISTRY, etc.)
                        for det in _extract_python_prompt_dicts(content, rel_path):
                            self._merge_detection(node_map, det)

            # Phase 1b: SQL schema — data classification + prompt/template extraction
            elif is_sql:
                _log.debug("running SQL adapters on %s", rel_path)
                for sql_adapter in self.sql_adapters:
                    try:
                        detections = sql_adapter.scan(content, rel_path)
                    except Exception as exc:
                        _log.warning(
                            "SQL adapter %r failed on %s: %s", sql_adapter.name, rel_path, exc
                        )
                        continue
                    for det in detections:
                        # Data-classification DATASTORE detections feed the PII/PHI
                        # enrichment pass (_enrich_datastores) rather than becoming
                        # nodes directly; everything else (e.g. PROMPT detections
                        # from PromptSQLAdapter) goes through the normal merge path,
                        # mirroring the Python-file branch's discrimination above.
                        if (
                            det.component_type == ComponentType.DATASTORE
                            and det.metadata.get("source") in ("sql_schema", "python_model")
                        ):
                            _dc_metadata.append(det.metadata)
                        else:
                            self._merge_detection(node_map, det)

            # Phase 1c: TypeScript/JavaScript AST-aware framework adapters
            # Also detect minified single-line JS (>5 KB line) for SC-019
            elif is_typescript:
                ts_hints = self._parse_typescript(content, rel_path)
                imported_modules_ts: set[str] = {imp.module for imp in ts_hints.imports}
                for adapter in self.framework_adapters:
                    if not isinstance(adapter, TSFrameworkAdapter):
                        continue
                    if not adapter.can_handle(imported_modules_ts):
                        continue
                    _log.debug("running TS adapter %r on %s", adapter.name, rel_path)
                    try:
                        detections = adapter.extract(content, rel_path, ts_hints)
                    except Exception as exc:
                        _log.warning(
                            "TS adapter %r failed on %s: %s",
                            adapter.name,
                            rel_path,
                            exc,
                        )
                        continue
                    for det in detections:
                        self._merge_detection(node_map, det)

            # Phase 1c-go: Go AST-aware framework adapters
            elif is_go:
                go_result = self._parse_go(content, rel_path)
                if go_result is not None:
                    for adapter in self.framework_adapters:
                        if not isinstance(adapter, GoFrameworkAdapter):
                            continue
                        if not adapter.can_handle(go_result):
                            continue
                        _log.debug("running Go adapter %r on %s", adapter.name, rel_path)
                        try:
                            detections = adapter.extract(content, rel_path, go_result)
                        except Exception as exc:
                            _log.warning(
                                "Go adapter %r failed on %s: %s",
                                adapter.name,
                                rel_path,
                                exc,
                            )
                            continue
                        for det in detections:
                            self._merge_detection(node_map, det)

            # SC-019: detect minified JS (single line > 5000 chars) for supply-chain summary
            if suffix == ".js" and content:
                _js_lines = content.splitlines()
                if _js_lines and max(len(ln) for ln in _js_lines) > 5000:
                    _minified_js_files.append(rel_path)

            # Phase 1d: Dockerfile — container image extraction
            if is_dockerfile:
                _log.debug("running dockerfile adapter on %s", rel_path)
                try:
                    for det in self.dockerfile_adapter.scan(content, rel_path):
                        self._merge_detection(node_map, det)
                except Exception as exc:
                    _log.warning("dockerfile adapter failed on %s: %s", rel_path, exc)

            # Phase 1f: Nginx config — deployment and auth extraction
            if is_nginx_conf:
                _log.debug("running nginx adapter on %s", rel_path)
                try:
                    for det in self.nginx_adapter.scan(content, rel_path):
                        self._merge_detection(node_map, det)
                except Exception as exc:
                    _log.warning("nginx adapter failed on %s: %s", rel_path, exc)

            # Phase 1e: YAML-aware framework adapters (e.g. CrewAI agents.yaml)
            if suffix in {".yaml", ".yml"}:
                for yaml_adapter in self.yaml_adapters:
                    _log.debug("running YAML adapter %r on %s", yaml_adapter.name, rel_path)
                    try:
                        for det in yaml_adapter.scan(content, rel_path):
                            self._merge_detection(node_map, det)
                    except Exception as exc:
                        _log.warning(
                            "YAML adapter %r failed on %s: %s", yaml_adapter.name, rel_path, exc
                        )

            # Phase 1f: JSON-aware framework adapters (tools.json, agents.json, etc.)
            if suffix == ".json":
                for json_adapter in self.json_adapters:
                    _log.debug("running JSON adapter %r on %s", json_adapter.name, rel_path)
                    try:
                        # GoogleADKJSONAdapter needs root to resolve instruction file paths
                        if isinstance(json_adapter, GoogleADKJSONAdapter):
                            dets = json_adapter.scan(content, rel_path, root=root)
                        else:
                            dets = json_adapter.scan(content, rel_path)
                        for det in dets:
                            self._merge_detection(node_map, det)
                    except Exception as exc:
                        _log.warning(
                            "JSON adapter %r failed on %s: %s", json_adapter.name, rel_path, exc
                        )

            # Phase 1g: IaC adapters (K8s, CFN, GCP DM for YAML/JSON;
            #           Terraform for .tf/.tfvars; Bicep for .bicep; Jinja for .jinja)
            _is_iac_file = (
                suffix in {".yaml", ".yml", ".json", ".tf", ".tfvars"}
                or suffix in _BICEP_EXTENSIONS
                or suffix in _JINJA_EXTENSIONS
            )
            if _is_iac_file:
                for iac_adapter in self.iac_adapters:
                    # Gate each adapter to its relevant extensions to avoid
                    # redundant YAML loading on non-matching files
                    adapter_handles: bool
                    if isinstance(iac_adapter, TerraformAdapter):
                        adapter_handles = suffix in {".tf", ".tfvars"}
                    elif isinstance(iac_adapter, BicepAdapter):
                        adapter_handles = suffix in _BICEP_EXTENSIONS
                    elif isinstance(iac_adapter, GcpDeploymentManagerAdapter):
                        adapter_handles = suffix in {".yaml", ".yml", ".jinja"}
                    elif isinstance(iac_adapter, GitHubActionsAdapter):
                        # GitHub Actions workflows are YAML; content guard in adapter
                        # also handles .github/workflows/**/*.yml naming convention
                        adapter_handles = suffix in {".yaml", ".yml"}
                    else:
                        # K8sAdapter + CloudFormationAdapter handle YAML and JSON
                        adapter_handles = suffix in {".yaml", ".yml", ".json"}
                    if not adapter_handles:
                        continue
                    _log.debug("running IaC adapter %r on %s", iac_adapter.name, rel_path)
                    try:
                        for det in iac_adapter.scan(content, rel_path):
                            self._merge_detection(node_map, det)
                    except Exception as exc:
                        _log.warning(
                            "IaC adapter %r failed on %s: %s", iac_adapter.name, rel_path, exc
                        )

            # Phase 2: Regex fallback
            # Skip documentation and shell-script files to eliminate CI/README FP floods.
            # For .ipynb files, strip cell outputs to avoid base64-encoded image data
            # producing false-positive model matches (e.g. 'o5'/'o7' in PNG base64).
            # For Python files, strip comment-only lines so that commented-out migration
            # examples (e.g. `# llm = ChatOllama(model="llama3:8b")`) do not become
            # false-positive model nodes.
            if suffix == ".ipynb":
                _regex_content = _strip_notebook_outputs(content)
            elif suffix in _PYTHON_EXTENSIONS:
                _regex_content = _strip_python_comments(content)
            else:
                _regex_content = content
            for rx_adapter in (
                self.regex_adapters
                if suffix not in _DOCS_EXTENSIONS and Path(rel_path).stem.lower() not in _DOCS_STEMS
                else ()
            ):
                # Adapters may declare path-scoped exclusions (e.g. privilege
                # adapters skip test dirs and __init__.py to reduce FPs).
                if (
                    getattr(rx_adapter, "skip_path_parts", None)
                    or getattr(rx_adapter, "skip_init_py", False)
                    or getattr(rx_adapter, "skip_extensions", None)
                ):
                    _rel = Path(rel_path)
                    if getattr(rx_adapter, "skip_init_py", False) and _rel.name == "__init__.py":
                        continue
                    _skip_parts = getattr(rx_adapter, "skip_path_parts", None)
                    if _skip_parts and bool(set(_rel.parts) & _skip_parts):
                        continue
                    _skip_exts = getattr(rx_adapter, "skip_extensions", None)
                    if _skip_exts and suffix in _skip_exts:
                        continue
                detection = rx_adapter.detect(_regex_content)
                if detection is None:
                    continue
                # For adapters with per-match naming (canonical_name=None on the
                # adapter), emit one node per distinct matched name so that a
                # single file containing multiple different entities of the same
                # type (e.g. a mock route.ts listing gpt-5, deepseek-v3.2,
                # gemini-3-pro; or a CI workflow using both docker and vercel)
                # gets a separate node for each instead of only the first match.
                # Originally MODEL-only; also applies to DEPLOYMENT so distinct
                # deployment technologies don't collapse into one "generic" node.
                if detection.component_type in (
                    ComponentType.MODEL,
                    ComponentType.DEPLOYMENT,
                ) and not getattr(rx_adapter, "canonical_name", None):
                    # Group matches by their normalised name; keep first
                    # occurrence as the representative match for location/snippet.
                    _match_first: dict[str, AdapterMatch] = {}
                    _match_count: dict[str, int] = {}
                    for _m in detection.matches:
                        _key = _m.snippet.strip().lower()
                        if _key not in _match_first:
                            _match_first[_key] = _m
                            _match_count[_key] = 1
                        else:
                            _match_count[_key] += 1
                    for _raw_lower, _first_match in _match_first.items():
                        _raw_name = _first_match.snippet.strip()
                        _cnt = _match_count[_raw_lower]
                        _conf = min(0.95, 0.50 + 0.05 * _cnt)
                        _comp_det = ComponentDetection(
                            component_type=detection.component_type,
                            canonical_name=canonicalize_text(_raw_name),
                            display_name=_raw_name,
                            adapter_name=detection.adapter_name,
                            priority=detection.priority,
                            confidence=_conf,
                            metadata=dict(detection.metadata),
                            file_path=rel_path,
                            line=_first_match.line,
                            snippet=_first_match.snippet,
                            evidence_kind="regex",
                        )
                        self._merge_detection(node_map, _comp_det)
                    continue
                # API_ENDPOINT: group per-match by the parsed (method, path) —
                # not raw snippet text, since different route-declaration styles
                # (decorator vs. bare "METHOD /path") produce different snippets
                # for the same logical route. The canonical_name scheme
                # (endpoint:{METHOD}:{path}) matches the one used by the FastAPI/
                # Flask AST adapters, so a route found by both a generic regex
                # match and a framework-specific adapter merges into one node
                # instead of producing a duplicate.
                if detection.component_type == ComponentType.API_ENDPOINT and not getattr(
                    rx_adapter, "canonical_name", None
                ):
                    _ep_first: dict[tuple[str, str], AdapterMatch] = {}
                    for _m in detection.matches:
                        _path = _m.groups.get("path")
                        if not _path or _path == "/":
                            continue
                        _method = (_m.groups.get("method") or "").upper()
                        _ep_key = (_method, _path)
                        if _ep_key not in _ep_first:
                            _ep_first[_ep_key] = _m
                    for (_ep_method, _ep_path), _first_match in _ep_first.items():
                        _ep_meta = dict(detection.metadata)
                        _ep_meta["endpoint"] = _ep_path
                        if _ep_method:
                            _ep_meta["method"] = _ep_method
                        # Marks this as a raw, unresolved-prefix regex guess so
                        # _dedup_generic_endpoints() can find and fold it into a
                        # framework-adapter node with the same (prefix-resolved)
                        # route, instead of leaving both as separate nodes.
                        _ep_meta["_generic_endpoint_fallback"] = True
                        _comp_det = ComponentDetection(
                            component_type=detection.component_type,
                            canonical_name=f"endpoint:{_ep_method or 'ANY'}:{_ep_path}",
                            display_name=_ep_path,
                            adapter_name=detection.adapter_name,
                            priority=detection.priority,
                            confidence=0.55,
                            metadata=_ep_meta,
                            file_path=rel_path,
                            line=_first_match.line,
                            snippet=_first_match.snippet,
                            evidence_kind="regex",
                        )
                        self._merge_detection(node_map, _comp_det)
                    continue
                confidence = min(0.95, 0.50 + 0.05 * len(detection.matches))
                canonical = canonicalize_text(detection.canonical_name)
                # For MODEL type, keep the full canonical name (e.g., "llama3.2:3b"
                # must not be truncated to "3b"). For other types, strip any
                # type-category prefix so "auth:generic" → "generic" and
                # "privilege:email_out" → "email_out".
                if detection.component_type == ComponentType.MODEL:
                    display = detection.canonical_name
                else:
                    display = detection.canonical_name.split(":")[-1]
                first = detection.matches[0]
                comp_det = ComponentDetection(
                    component_type=detection.component_type,
                    canonical_name=canonical,
                    display_name=display,
                    adapter_name=detection.adapter_name,
                    priority=detection.priority,
                    confidence=confidence,
                    metadata=dict(detection.metadata),
                    file_path=rel_path,
                    line=first.line,
                    snippet=first.snippet,
                    evidence_kind="regex",
                )
                self._merge_detection(node_map, comp_det)

        # Enrich DATASTORE nodes with PII/PHI classification metadata
        self._enrich_datastores(node_map, _dc_metadata)

        # Cross-file TOOL -[ACCESSES]-> DATASTORE hints: a file that defines
        # @tool-decorated functions but only *imports* its datastore client
        # (rather than instantiating one directly) gets no same-file hint from
        # PythonDatastoreAdapter, since that adapter never emits a relationship
        # for datastores detected in another file. Resolve the import (reusing
        # the same _resolve_import_to_relpath used for router prefixes in the
        # pre-pass above) against _ds_symbol_index, which was populated from
        # this adapter's own fully-disambiguated provider resolution as
        # DATASTORE detections came in above — not re-derived independently,
        # so the canonical name always matches the real node.
        try:
            if _resolve_import_to_relpath is None:
                raise RuntimeError("pre-pass import resolver unavailable")  # noqa: TRY301
            for _tool_rel, _tool_fn_names in _tool_defining_files.items():
                for _local_name, (_lvl, _mod, _orig) in _router_imports.get(_tool_rel, {}).items():
                    _target_rel = _resolve_import_to_relpath(_tool_rel, _lvl, _mod)
                    if not _target_rel:
                        continue
                    _ds_canon = _ds_symbol_index.get((_target_rel, _orig))
                    if not _ds_canon:
                        continue
                    for _fn_name in _tool_fn_names:
                        _tool_key = (ComponentType.TOOL, canonicalize_text(f"langchain:tool:{_fn_name}"))
                        _ds_key = (ComponentType.DATASTORE, canonicalize_text(_ds_canon))
                        _acc = node_map.get(_tool_key)
                        if _acc is None or _ds_key not in node_map:
                            continue
                        _acc.relationships.append(
                            RelationshipHint(
                                source_canonical=canonicalize_text(f"langchain:tool:{_fn_name}"),
                                source_type=ComponentType.TOOL,
                                target_canonical=_ds_canon,
                                target_type=ComponentType.DATASTORE,
                                relationship_type="ACCESSES",
                            )
                        )
        except Exception as _cross_ds_exc:
            _log.debug("cross-file datastore ACCESSES resolution failed (non-fatal): %s", _cross_ds_exc)

        # Deduplicate nodes that share (component_type, file, line) — e.g. a
        # regex adapter and an AST adapter both firing on the same token.
        _dedup_by_location(node_map)
        # Deduplicate nodes where one name is a prefix of another from the same
        # file — e.g. regex matches "gemini-2.0" while AST extracts the full
        # "gemini-2.0-flash" from an adjacent line of the same call.
        _dedup_by_name_prefix(node_map)
        # Merge MODEL nodes that are repo-id/filename spellings of the same
        # underlying preconfigured model entry (e.g. ``unsloth/Qwen3.5-9B-GGUF``
        # + ``Qwen3.5-9B-Q4_K_M.gguf`` from the same config dict).
        _dedup_model_variants(node_map)
        # Suppress generic tech-name DATASTORE nodes emitted by regex adapters
        # when the same file already has specific AST-detected ones for the same
        # technology (e.g. "faiss" regex when "docs_index" / "tickets_index" AST
        # nodes already exist in that file with provider="faiss").
        _suppress_generic_tech_regex_datastore(node_map)

        # Fold generic-regex API_ENDPOINT nodes (raw, unprefixed path guesses)
        # into the framework-adapter node for the same route once its real,
        # prefix-resolved path is known — see _dedup_generic_endpoints for why
        # this can't be resolved earlier, at detection time.
        _dedup_generic_endpoints(node_map)

        # For MODEL and DATASTORE, suppress nodes whose only evidence comes from
        # docs-tier files (lock files, README mentions, shell scripts).  Lock
        # files like pnpm-lock.yaml and semantic changelogs are classified as
        # DOCS tier and produce noisy nodes when package names happen to look
        # like model or datastore names.  IaC-tier detections (YAML/JSON config,
        # Dockerfiles) are kept because they legitimately describe components.
        _suppress_non_code_model_datastore(node_map, _TIER_RANK[_TIER_DOCS])

        # Improve 'generic' display names for AUTH/DEPLOYMENT nodes to reflect
        # the dominant technology keyword found in the evidence.
        _improve_generic_node_names(node_map)

        # Build nodes + edges
        for key in sorted(node_map.keys(), key=lambda v: (v[0].value, v[1])):
            acc = node_map[key]

            # Cross-tier corroboration: each additional source tier adds a
            # small confidence boost (capped at 0.99) because independent
            # evidence from code + IaC or code + docs raises certainty.
            if len(acc.source_tiers) > 1:
                acc.confidence = min(0.99, acc.confidence + 0.03 * (len(acc.source_tiers) - 1))

            _raw_display = acc.display_name
            # Normalize display names that are raw variable identifiers
            if " " not in _raw_display:
                from ..normalization import normalize_display_name as _norm_dn
                _raw_display = _norm_dn(_raw_display, acc.component_type)
            node = Node(
                name=_raw_display,
                component_type=acc.component_type,
                confidence=acc.confidence,
            )
            node.metadata.extras["canonical_name"] = acc.canonical_name
            node.metadata.extras["adapter"] = acc.adapter_name
            node.metadata.extras["evidence_count"] = len(acc.evidence)
            if len(acc.source_tiers) > 1:
                # Expose which tiers corroborated this detection for consumers
                node.metadata.extras["detected_by_tiers"] = sorted(acc.source_tiers)
            node.metadata.extras.update(
                {
                    k: v
                    for k, v in acc.metadata.items()
                    if k
                    not in (
                        "adapter",
                        "evidence_count",
                        "canonical_name",
                        "data_classification",
                        "classified_tables",
                        "classified_fields",
                        "_generic_endpoint_fallback",
                    )
                }
            )
            # Copy typed metadata fields
            if "framework" in acc.metadata:
                node.metadata.framework = str(acc.metadata["framework"])
            if "provider" in acc.metadata:
                node.metadata.extras["provider"] = acc.metadata["provider"]
            if "model_family" in acc.metadata and acc.metadata["model_family"]:
                node.metadata.extras["model_family"] = acc.metadata["model_family"]
            if "version" in acc.metadata and acc.metadata["version"]:
                node.metadata.extras["version"] = acc.metadata["version"]
            if "model_card_url" in acc.metadata and acc.metadata["model_card_url"]:
                node.metadata.extras["model_card_url"] = acc.metadata["model_card_url"]
            if "api_endpoint" in acc.metadata and acc.metadata["api_endpoint"]:
                node.metadata.extras["api_endpoint"] = acc.metadata["api_endpoint"]
            # AGENT node typed fields
            if acc.component_type == ComponentType.AGENT:
                _spe = (
                    acc.metadata.get("instructions_preview")   # openai_agents
                    or acc.metadata.get("backstory_preview")   # crewai
                    or acc.metadata.get("goal_preview")        # crewai fallback
                    or acc.metadata.get("system_prompt_preview")
                )
                if _spe:
                    node.metadata.system_prompt_excerpt = str(_spe)[:500]
                _irs = acc.metadata.get("injection_risk_score")
                if _irs is not None:
                    node.metadata.injection_risk_score = float(_irs)
            # GUARDRAIL node typed fields
            if acc.component_type == ComponentType.GUARDRAIL:
                if acc.metadata.get("rules_excerpt"):
                    node.metadata.rules_excerpt = str(acc.metadata["rules_excerpt"])
                _bt = acc.metadata.get("blocked_topics")
                if isinstance(_bt, list) and _bt:
                    node.metadata.blocked_topics = [str(t) for t in _bt]
                _ba = acc.metadata.get("blocked_actions")
                if isinstance(_ba, list) and _ba:
                    node.metadata.blocked_actions = [str(a) for a in _ba]
                if acc.metadata.get("refusal_style"):
                    node.metadata.refusal_style = str(acc.metadata["refusal_style"])
                # Derive rules_excerpt from known guardrail metadata when not explicitly provided
                if not node.metadata.rules_excerpt:
                    vc = acc.metadata.get("validator_class")
                    guard_type = acc.metadata.get("guard_type") or acc.metadata.get(
                        "guardrail_type"
                    )
                    if vc:
                        node.metadata.rules_excerpt = f"Hub validator: {vc}"
                    elif guard_type:
                        node.metadata.rules_excerpt = f"Guard type: {guard_type}"
            # TOOL node typed fields
            if acc.component_type == ComponentType.TOOL:
                if acc.metadata.get("mcp_server_url"):
                    node.metadata.mcp_server_url = str(acc.metadata["mcp_server_url"])
                if acc.metadata.get("trust_level"):
                    node.metadata.trust_level = str(acc.metadata["trust_level"])
                for _bool_field in (
                    "no_auth_required",
                    "high_privilege",
                    "sql_injectable",
                    "ssrf_possible",
                    "accepts_external_url",
                    "reads_external_content",
                ):
                    _v = acc.metadata.get(_bool_field)
                    if _v is not None:
                        setattr(node.metadata, _bool_field, bool(_v))
            # AUTH node typed fields
            if acc.component_type == ComponentType.AUTH:
                if acc.metadata.get("auth_type"):
                    node.metadata.auth_type = str(acc.metadata["auth_type"])
                if acc.metadata.get("auth_class"):
                    node.metadata.auth_class = str(acc.metadata["auth_class"])
                if acc.metadata.get("server_name"):
                    node.metadata.server_name = str(acc.metadata["server_name"])
            # API_ENDPOINT node typed fields
            if acc.component_type == ComponentType.API_ENDPOINT:
                host = acc.metadata.get("host", "")
                port = acc.metadata.get("port", "")
                transport = acc.metadata.get("transport", "")
                if host or port:
                    node.metadata.endpoint = (
                        f"{host}:{port}" if (host and port) else str(host or port)
                    )
                if transport:
                    node.metadata.transport = str(transport)
                if acc.metadata.get("server_name"):
                    node.metadata.server_name = str(acc.metadata["server_name"])
                if acc.metadata.get("method"):
                    node.metadata.method = str(acc.metadata["method"])
                _ar = acc.metadata.get("auth_required")
                if _ar is not None:
                    node.metadata.auth_required = bool(_ar)
                if acc.metadata.get("auth_scope"):
                    node.metadata.auth_scope = str(acc.metadata["auth_scope"])
                _aui = acc.metadata.get("accepts_user_input")
                if _aui is not None:
                    node.metadata.accepts_user_input = bool(_aui)
                _rsd = acc.metadata.get("returns_sensitive_data")
                if _rsd is not None:
                    node.metadata.returns_sensitive_data = bool(_rsd)
                _rl = acc.metadata.get("rate_limited")
                if _rl is not None:
                    node.metadata.rate_limited = bool(_rl)
                _ids = acc.metadata.get("idor_surface")
                if _ids is not None:
                    node.metadata.idor_surface = bool(_ids)
                _pp = acc.metadata.get("path_params")
                if isinstance(_pp, list) and _pp:
                    node.metadata.path_params = [str(p) for p in _pp]
                # Schema discovery fields (populated by FastAPI/Flask AST adapters)
                if acc.metadata.get("endpoint") and not node.metadata.endpoint:
                    node.metadata.endpoint = str(acc.metadata["endpoint"])
                _cpk = acc.metadata.get("chat_payload_key")
                if _cpk:
                    node.metadata.chat_payload_key = str(_cpk)
                _cpl = acc.metadata.get("chat_payload_list")
                if _cpl is not None:
                    node.metadata.chat_payload_list = bool(_cpl)
                _rtk = acc.metadata.get("response_text_key")
                if _rtk:
                    node.metadata.response_text_key = str(_rtk)
                _rbs = acc.metadata.get("request_body_schema")
                if isinstance(_rbs, dict) and _rbs:
                    node.metadata.request_body_schema = {
                        str(k): str(v) for k, v in _rbs.items()
                    }
                    node.metadata.request_schema = dict(_rbs)
                _resp_schema = acc.metadata.get("response_body_schema")
                if isinstance(_resp_schema, dict) and _resp_schema:
                    node.metadata.response_schema = dict(_resp_schema)
                _cpf = acc.metadata.get("context_payload_fields")
                if isinstance(_cpf, dict) and _cpf:
                    existing = node.metadata.context_payload_fields or {}
                    merged = {str(k): str(v) for k, v in _cpf.items()}
                    merged.update(existing)  # existing node values win on conflict
                    node.metadata.context_payload_fields = merged
            # server_name for all MCP FRAMEWORK/TOOL nodes
            if acc.metadata.get("framework") == "mcp-server":
                if acc.metadata.get("server_name"):
                    node.metadata.server_name = str(acc.metadata["server_name"])
            # Data classification metadata (DATASTORE nodes)
            if acc.component_type == ComponentType.DATASTORE:
                if acc.metadata.get("datastore_type"):
                    node.metadata.datastore_type = str(acc.metadata["datastore_type"])
                if acc.metadata.get("data_classification"):
                    node.metadata.data_classification = acc.metadata["data_classification"]
                if acc.metadata.get("classified_tables"):
                    node.metadata.classified_tables = acc.metadata["classified_tables"]
                if acc.metadata.get("classified_fields"):
                    node.metadata.classified_fields = acc.metadata["classified_fields"]
                # Typed PII/PHI field lists for red-team pre-scoring — computed
                # by _enrich_datastores from per-field labels (classified_fields
                # above is table -> field names, not field -> labels, so it
                # can't be re-derived from here).
                if acc.metadata.get("pii_fields"):
                    node.metadata.pii_fields = acc.metadata["pii_fields"]
                if acc.metadata.get("phi_fields"):
                    node.metadata.phi_fields = acc.metadata["phi_fields"]
            # PRIVILEGE node typed fields
            if acc.component_type == ComponentType.PRIVILEGE:
                if acc.metadata.get("privilege_scope"):
                    node.metadata.privilege_scope = str(acc.metadata["privilege_scope"])
            # Container image metadata
            if acc.component_type == ComponentType.CONTAINER_IMAGE:
                node.metadata.image_name = acc.metadata.get("image_name")
                node.metadata.image_tag = acc.metadata.get("image_tag") or None
                node.metadata.image_digest = acc.metadata.get("image_digest")
                node.metadata.registry = acc.metadata.get("registry")
                node.metadata.base_image = acc.metadata.get("base_image")
                # Security signals annotated by DockerfileAdapter
                _rar = acc.metadata.get("runs_as_root")
                if _rar is not None:
                    node.metadata.runs_as_root = bool(_rar)
                _hc = acc.metadata.get("has_health_check")
                if _hc is not None:
                    node.metadata.has_health_check = bool(_hc)
            # IaC security / resilience metadata (DEPLOYMENT nodes from IaC adapters)
            if acc.component_type == ComponentType.DEPLOYMENT:
                if acc.metadata.get("deployment_target"):
                    node.metadata.deployment_target = str(acc.metadata["deployment_target"])
                _cr = acc.metadata.get("cloud_region")
                if _cr:
                    node.metadata.cloud_region = str(_cr)
                _az = acc.metadata.get("availability_zones")
                if isinstance(_az, list) and _az:
                    node.metadata.availability_zones = [str(z) for z in _az]
                _ss = acc.metadata.get("secret_store")
                if _ss:
                    node.metadata.secret_store = str(_ss)
                _enc = acc.metadata.get("encryption_at_rest")
                if _enc is not None:
                    node.metadata.encryption_at_rest = bool(_enc)
                _ekr = acc.metadata.get("encryption_key_ref")
                if _ekr:
                    node.metadata.encryption_key_ref = str(_ekr)
                _ha = acc.metadata.get("ha_mode")
                if _ha:
                    node.metadata.ha_mode = str(_ha)
                _rar2 = acc.metadata.get("runs_as_root")
                if _rar2 is not None:
                    node.metadata.runs_as_root = bool(_rar2)
                _hc2 = acc.metadata.get("has_health_check")
                if _hc2 is not None:
                    node.metadata.has_health_check = bool(_hc2)
                _rl = acc.metadata.get("has_resource_limits")
                if _rl is not None:
                    node.metadata.has_resource_limits = bool(_rl)
                _hnp = acc.metadata.get("has_network_policy")
                if _hnp is not None:
                    node.metadata.has_network_policy = bool(_hnp)
            # MODEL node typed fields (source_url, integrity_hash, checksum)
            if acc.component_type == ComponentType.MODEL:
                _su = acc.metadata.get("source_url")
                if _su:
                    node.metadata.source_url = str(_su)
                _ih = acc.metadata.get("integrity_hash")
                if _ih:
                    node.metadata.integrity_hash = str(_ih)
                _cs = acc.metadata.get("checksum")
                if _cs:
                    node.metadata.checksum = str(_cs)
            # IAM node typed fields
            if acc.component_type == ComponentType.IAM:
                _it = acc.metadata.get("iam_type")
                if _it:
                    node.metadata.iam_type = str(_it)
                _pr = acc.metadata.get("principal")
                if _pr:
                    node.metadata.principal = str(_pr)
                _pm = acc.metadata.get("permissions")
                if isinstance(_pm, list) and _pm:
                    node.metadata.permissions = [str(p) for p in _pm[:20]]
                _is = acc.metadata.get("iam_scope")
                if _is:
                    node.metadata.iam_scope = str(_is)
                _tp = acc.metadata.get("trust_principals")
                if isinstance(_tp, list) and _tp:
                    node.metadata.trust_principals = [str(p) for p in _tp[:20]]

            # ── SBOM 1.5.0 sub-model mapping (applies to all component types) ──────
            _rld = acc.metadata.get("rate_limit_detail")
            if isinstance(_rld, dict) and _rld and node.metadata.rate_limit_detail is None:
                node.metadata.rate_limit_detail = RateLimitDetail(
                    **{k: v for k, v in _rld.items() if v is not None}
                )
            _ad = acc.metadata.get("auth_detail")
            if isinstance(_ad, dict) and _ad and node.metadata.auth_detail is None:
                node.metadata.auth_detail = AuthDetail(
                    **{k: v for k, v in _ad.items() if v is not None}
                )
            _ed = acc.metadata.get("encryption_detail")
            if isinstance(_ed, dict) and _ed and node.metadata.encryption_detail is None:
                node.metadata.encryption_detail = EncryptionDetail(
                    **{k: v for k, v in _ed.items() if v is not None}
                )
                if node.metadata.encryption_detail.at_rest is not None:
                    node.metadata.encryption_at_rest = node.metadata.encryption_detail.at_rest
            _dh = acc.metadata.get("data_handling")
            if isinstance(_dh, dict) and _dh and node.metadata.data_handling is None:
                node.metadata.data_handling = DataHandlingDetail(
                    **{k: v for k, v in _dh.items() if v is not None}
                )

            node.evidence = sorted(acc.evidence, key=lambda e: e.confidence, reverse=True)
            doc.nodes.append(node)

        self._resolve_edges(doc, node_map)

        # Deduplicate DEPLOYMENT nodes: merge github-actions workflow nodes into
        # the cloud-provider service nodes they deploy to.
        _dedup_deployment_nodes(doc)

        # Phase CES: scan for Google Customer Engagement Suite deployments.
        # Runs after the main AST/regex loop so CES nodes can be appended.
        try:
            from ..adapters.ces import CESScanner, build_ces_sbom_nodes  # noqa: PLC0415
            ces_detections = CESScanner().scan_directory(root)
            if ces_detections:
                _log.info(
                    "CESScanner: detected %d CES deployment(s)", len(ces_detections)
                )
                ces_nodes = build_ces_sbom_nodes(ces_detections, doc)
                if ces_nodes:
                    # Collect source files covered by CES endpoints
                    ces_source_files = {
                        det.source_file for det in ces_detections if det.source_file
                    }
                    # Remove generic API_ENDPOINT nodes whose only evidence comes from the
                    # same source files — CES nodes are more specific (auth, schema, endpoint URL)
                    from ..types import ComponentType as _CT  # noqa: PLC0415
                    doc.nodes = [
                        n for n in doc.nodes
                        if not (
                            n.component_type == _CT.API_ENDPOINT
                            and not getattr(n.metadata, "framework", None)
                            and all(
                                (e.location.path if e.location else "") in ces_source_files
                                for e in (n.evidence or [])
                            )
                        )
                    ]
                doc.nodes.extend(ces_nodes)
        except Exception as exc:  # noqa: BLE001
            _log.warning("CES scanner failed: %s", exc)

        # Synthesize a fallback AGENT node representing the app itself when no
        # agentic-framework adapter (LangGraph/CrewAI/AutoGen/etc.) fired but
        # the app clearly calls an LLM directly (MODEL node) and/or exposes
        # tools (TOOL/MCP_SERVER) — e.g. a plain FastAPI backend that builds
        # raw OpenAI tool schemas by hand, with no agent framework in sight.
        # Without this, such apps get zero AGENT nodes and every AGENT-gated
        # redteam scenario family (PROMPT_DRIVEN_THREAT, DATA_EXFILTRATION)
        # is silently starved. Mirrors the same fallback already used by the
        # behavior/redteam runtime path (nuguard.common.auto_sbom_enricher.
        # _enrich_static) but runs here so `nuguard sbom generate` itself
        # reports the node, and before _enrich_sbom() below so the enricher's
        # generic injection_risk_score computation covers it too.
        if not any(n.component_type == ComponentType.AGENT for n in doc.nodes) and any(
            n.component_type in (ComponentType.MODEL, ComponentType.TOOL, ComponentType.MCP_SERVER)
            for n in doc.nodes
        ):
            _app_stem = PurePosixPath(doc.target.rstrip("/")).name or "Application"
            _app_name = f"{_app_stem.replace('-', ' ').replace('_', ' ').strip().title() or 'Application'} Assistant"
            doc.nodes.append(
                Node(
                    name=_app_name,
                    component_type=ComponentType.AGENT,
                    confidence=0.55,
                    metadata=NodeMetadata(
                        description=(doc.summary.use_case if doc.summary else "") or "",
                        extras={"source": "auto_enrichment"},
                    ),
                    evidence=[],
                )
            )

        # Post-extraction enrichment: derive risk attributes from graph topology
        from ..enricher import enrich as _enrich_sbom
        _enrich_sbom(doc)

        # Scan package manifest dependencies (pyproject.toml, requirements*.txt, package.json, …)
        doc.deps = DependencyScanner().scan(root)
        _log.info("deps scan: %d packages found", len(doc.deps))

        # Per-node LOC and dependency cross-referencing
        dep_names = {d.name.lower().replace("-", "_") for d in doc.deps}
        for node in doc.nodes:
            # LOC: sum lines from all evidence source files for this node
            ev_paths = {
                ev.location.path
                for ev in node.evidence
                if ev.location and ev.location.path
            }
            loc_sum = sum(file_loc.get(p, 0) for p in ev_paths)
            if loc_sum:
                node.metadata.loc = loc_sum

            # Dependency names: intersect imports from evidence files with manifest deps
            all_imports: set[str] = set()
            for p in ev_paths:
                all_imports |= _file_imports.get(p, set())
            matched = sorted(all_imports & dep_names)
            if matched:
                node.metadata.dependency_names = matched

        _log.info("scan complete: %d files processed under %s", files_scanned, root)
        _log.info(
            "memory metrics: sample_files=%d sample_bytes=%d (%s) full_cache_files=%d full_cache_bytes=%d (%s) llm_enabled=%s",
            len(files_sample),
            files_sample_bytes,
            _human_bytes(files_sample_bytes),
            full_cache_files,
            full_cache_bytes,
            _human_bytes(full_cache_bytes),
            keep_full_contents,
        )

        # Build deterministic scan-level summary (always populated)
        doc.summary = _make_scan_summary(
            build_scan_summary(
                doc.nodes,
                files_sample,
                source_ref=source_ref,
                branch=branch,
                dc_metadata=_dc_metadata,
            )
        )

        # Write total LOC and minified JS list into the summary
        if doc.summary and file_loc:
            doc.summary.total_loc = sum(file_loc.values()) or None
        if doc.summary is not None and _minified_js_files:
            doc.summary.minified_js_files = _minified_js_files

        # Ensure google-ces is listed in summary.frameworks when CES nodes exist
        _has_ces = any(
            getattr(n.metadata, "framework", "") == "google-ces"
            for n in doc.nodes
        )
        if _has_ces and doc.summary is not None:
            if "google-ces" not in doc.summary.frameworks:
                doc.summary.frameworks.append("google-ces")

        # Phase 2b: Supply-chain second pass
        # Runs DevToolConfigAdapter, GithubActionsAdapter, and LifecycleScriptAdapter
        # against paths the main pass skips (.claude/, AGENTS.md, .mcp.json, workflows).
        # Wrapped in a broad try/except so a malformed config never crashes SBOM generation.
        if getattr(config, "supply_chain_scan", True):
            try:
                from nuguard.sbom.adapters.dev_tools import (  # noqa: PLC0415
                    DevToolConfigAdapter,
                    GithubActionsAdapter,
                    LifecycleScriptAdapter,
                )

                for adapter_cls in (DevToolConfigAdapter, GithubActionsAdapter, LifecycleScriptAdapter):
                    sc_nodes, sc_edges = adapter_cls().scan(root)
                    doc.nodes.extend(sc_nodes)
                    doc.edges.extend(sc_edges)
                    _log.debug(
                        "supply-chain pass (%s): +%d nodes, +%d edges",
                        adapter_cls.__name__, len(sc_nodes), len(sc_edges),
                    )
            except Exception as _sc_exc:
                _log.warning("supply-chain second pass failed (continuing): %s", _sc_exc)

            # Populate lockfile summary fields while the repo is still on disk.
            # Used by supply-chain scanner (SC-024) when analyzing remote SBOMs
            # without a live filesystem (the temp clone will be deleted after extraction).
            try:
                if doc.summary is not None:
                    doc.summary.has_package_json = (root / "package.json").exists()
                    doc.summary.has_lockfile = any(
                        (root / lf).exists()
                        for lf in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")
                    )
            except Exception as _lf_exc:
                _log.debug("lockfile summary population failed (non-fatal): %s", _lf_exc)

        # Phase 3: LLM enrichment (skipped unless enable_llm=True)
        if config.enable_llm:
            _log.info(
                "llm enrichment input cache: files=%d bytes=%d (%s)",
                full_cache_files,
                full_cache_bytes,
                _human_bytes(full_cache_bytes),
            )
            try:
                try:
                    running_loop = asyncio.get_running_loop()
                except RuntimeError:
                    running_loop = None

                if running_loop is not None and running_loop.is_running():
                    # Already inside an event loop (e.g. evaluate.py's async harness).
                    # Run the coroutine in a dedicated thread with its own fresh loop.
                    import concurrent.futures

                    coro = self._llm_enrich(doc, file_contents, config)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        doc = pool.submit(asyncio.run, coro).result()
                else:
                    doc = asyncio.run(self._llm_enrich(doc, file_contents, config))
            except Exception as exc:  # noqa: BLE001
                _log.warning("LLM enrichment failed, continuing with deterministic output: %s", exc)

        return doc

    def extract_from_repo(
        self,
        url: str,
        ref: str,
        config: AiSbomConfig,
        cache_dir: str | Path | None = None,
        source_ref: str | None = None,
    ) -> AiSbomDocument:
        """Clone a git repository and extract an SBOM from it.

        Args:
            url: Git repository URL to clone (may contain auth tokens).
            ref: Branch, tag, or commit to check out.
            config: Extraction configuration.
            cache_dir: Optional path where the cloned repository should be
                preserved after extraction.  When supplied the directory is
                created (if it does not exist), the repo is cloned inside it
                as ``repo/<app-name>/`` (where *app-name* is the last path
                segment of the URL, e.g. ``myapp`` for
                ``https://github.com/org/myapp``), and the directory is
                **not** deleted on return — callers own the lifecycle and can
                use the files for downstream processing.  When *None*
                (default) a temporary directory is used and cleaned up
                automatically.
            source_ref: Display URL stored in the SBOM ``target`` field.
                Defaults to *url* when not supplied.  Use this to avoid
                leaking auth tokens embedded in *url*.

        Returns:
            The extracted :class:`AiSbomDocument`.

        Example::

            extractor = AiSbomExtractor()
            cache = Path("/tmp/my_repo_cache")
            doc = extractor.extract_from_repo(url, ref, config, cache_dir=cache)
            # For url="https://github.com/org/myapp" the source files are at:
            #   cache / "repo" / "myapp"
            app_name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            for f in (cache / "repo" / app_name).rglob("*.py"):
                print(f)
        """
        app_name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "repo"
        display_url = source_ref or url

        if cache_dir is not None:
            repo_dir = Path(cache_dir) / "repo" / app_name
            repo_dir.mkdir(parents=True, exist_ok=True)
            self._clone_repo(url=url, ref=ref, dest=repo_dir)
            return self.extract_from_path(repo_dir, config, source_ref=display_url, branch=ref)

        with tempfile.TemporaryDirectory(prefix="nuguard_clone_", ignore_cleanup_errors=True) as temp_dir:
            repo_dir = Path(temp_dir) / "repo" / app_name
            repo_dir.mkdir(parents=True, exist_ok=True)
            self._clone_repo(url=url, ref=ref, dest=repo_dir)
            doc = self.extract_from_path(repo_dir, config, source_ref=display_url, branch=ref)
        _log.debug("Deleted cloned repo temp dir: %s", temp_dir)
        return doc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_python(content: str) -> Any | None:
        """Run the AST parser; return None on parse failure."""
        try:
            from ..ast_parser import parse

            result = parse(content)
            return result
        except Exception:
            return None

    @staticmethod
    def _parse_typescript(content: str, file_path: str = "") -> TSParseResult:
        """Parse TypeScript/JavaScript via tree-sitter (or regex fallback)."""
        return _parse_ts_impl(content, file_path or None)

    @staticmethod
    def _parse_go(content: str, file_path: str = "") -> Any | None:
        """Parse Go source via tree-sitter; return None on parse failure."""
        try:
            from ..core.go_parser import parse_go

            return parse_go(content, file_path)
        except Exception:
            return None

    @staticmethod
    def _extract_notebook_python(content: str) -> str:
        """Extract Python source from a Jupyter notebook (.ipynb).

        Concatenates all ``code`` cell sources separated by blank lines so
        the result can be passed directly to the Python AST parser.
        """
        import json

        try:
            nb = json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return ""
        cells = nb.get("cells", [])
        parts: list[str] = []
        for cell in cells:
            if cell.get("cell_type") != "code":
                continue
            source = cell.get("source", "")
            if isinstance(source, list):
                source = "".join(source)
            source = source.strip()
            if source:
                # Strip IPython magic/shell lines (e.g. %pip install, !command), including
                # any backslash-continuation lines that belong to the same command.
                clean_lines: list[str] = []
                skip_continuation = False
                for ln in source.splitlines():
                    if skip_continuation:
                        skip_continuation = ln.rstrip().endswith("\\")
                        continue
                    if ln.lstrip().startswith(("%", "!")):
                        skip_continuation = ln.rstrip().endswith("\\")
                        continue
                    skip_continuation = False
                    clean_lines.append(ln)
                cleaned = "\n".join(clean_lines).strip()
                if cleaned:
                    parts.append(cleaned)
        return "\n\n".join(parts)

    def _merge_detection(
        self,
        node_map: dict[tuple[ComponentType, str], _NodeAccumulator],
        det: ComponentDetection,
    ) -> None:
        """Merge a ComponentDetection into the accumulator map.

        Applies source-tier precedence: CODE > IAC > DOCS.  When the incoming
        detection comes from a higher tier than what we have accumulated so far,
        its adapter attribution and metadata take precedence.  Evidence from all
        tiers is always appended so the final node reflects every source.
        """
        # Always canonicalize to ensure regex-adapter and AST-adapter nodes
        # for the same component deduplicate correctly.
        canon = canonicalize_text(det.canonical_name)
        key = (det.component_type, canon)
        acc = node_map.get(key)

        tier = _classify_source_tier(det.file_path, det.adapter_name, det.evidence_kind)
        tier_rank = _TIER_RANK.get(tier, 2)

        if det.component_type == ComponentType.PROMPT:
            # Content is already stored in metadata.extras["content"]; keep
            # the evidence detail as a compact location label only.
            _detail = f"{det.adapter_name}: {det.evidence_kind}"
        else:
            _detail = f"{det.adapter_name}: {det.snippet[:500]}"
        evidence = Evidence(
            kind=det.evidence_kind,
            confidence=det.confidence,
            detail=_detail,
            location=SourceLocation(path=det.file_path, line=det.line or None),
        )

        if acc is None:
            acc = _NodeAccumulator(
                component_type=det.component_type,
                canonical_name=canon,
                display_name=det.display_name,
                adapter_name=det.adapter_name,
                priority=det.priority,
                confidence=det.confidence,
                metadata=dict(det.metadata),
                relationships=list(det.relationships),
                source_tiers={tier},
                best_tier_rank=tier_rank,
            )
            acc.evidence.append(evidence)
            node_map[key] = acc
        else:
            current_best_rank = acc.best_tier_rank  # snapshot before any mutation
            acc.source_tiers.add(tier)

            # Attribution: better tier wins; within the same tier, lower priority wins
            if tier_rank < current_best_rank or (
                tier_rank == current_best_rank and det.priority < acc.priority
            ):
                acc.adapter_name = det.adapter_name
                acc.priority = det.priority
                acc.display_name = det.display_name

            if tier_rank < current_best_rank:
                acc.best_tier_rank = tier_rank

            acc.confidence = max(acc.confidence, det.confidence)

            # Metadata precedence:
            #   Better tier  → its values override existing ones; old unique keys kept
            #   Same/worse tier → only fill gaps (first-write-wins per key)
            if tier_rank < current_best_rank:
                # Incoming detection is from a higher-authority tier.
                # Start from its metadata, then backfill any keys not present
                # from the accumulated metadata so nothing is lost.
                new_meta = {k: v for k, v in det.metadata.items() if v is not None}
                for k, v in acc.metadata.items():
                    if k not in new_meta and v is not None:
                        new_meta[k] = v
                acc.metadata = new_meta
            else:
                for k, v in det.metadata.items():
                    if v is not None:
                        acc.metadata.setdefault(k, v)

            acc.evidence.append(evidence)
            # Accumulate relationship hints
            acc.relationships.extend(det.relationships)

    def _enrich_datastores(
        self,
        node_map: dict[tuple[ComponentType, str], _NodeAccumulator],
        dc_metadata: list[dict[str, Any]],
    ) -> None:
        """Merge PII/PHI classification data from schema adapters into DATASTORE nodes.

        Classification data (from SQL CREATE TABLE and Python model analysis) is
        attached as metadata on every detected DATASTORE node rather than emitted
        as separate nodes.
        """
        if not dc_metadata:
            return
        datastore_keys = [k for k in node_map if k[0] == ComponentType.DATASTORE]
        if not datastore_keys:
            return

        # Aggregate labels, table names, and per-table field detail
        all_labels: set[str] = set()
        classified_tables: list[str] = []
        classified_fields: dict[str, list[str]] = {}
        # field_name -> labels, e.g. {"dob": ["PII"], "address": ["PII","PHI"]} —
        # kept separate from `classified_fields` above (which is table -> field
        # names, per NodeMetadata.classified_fields' documented shape) because
        # pii_fields/phi_fields need per-field labels, not per-table field lists.
        field_labels: dict[str, set[str]] = {}
        for meta in dc_metadata:
            all_labels.update(meta.get("data_classification") or [])
            table = meta.get("table_name") or meta.get("model_name")
            if table:
                classified_tables.append(table)
                cf = meta.get("classified_fields")
                if cf:
                    classified_fields[table] = sorted(cf.keys())
                    for field_name, labels in cf.items():
                        field_labels.setdefault(field_name, set()).update(labels or [])

        pii_fields = sorted(f for f, lbls in field_labels.items() if "PII" in lbls)
        phi_fields = sorted(f for f, lbls in field_labels.items() if "PHI" in lbls)

        # Merge into every DATASTORE accumulator (project-wide enrichment)
        for key in datastore_keys:
            acc = node_map[key]
            existing_labels = set(acc.metadata.get("data_classification") or [])
            acc.metadata["data_classification"] = sorted(all_labels | existing_labels)
            existing_tables = set(acc.metadata.get("classified_tables") or [])
            acc.metadata["classified_tables"] = sorted(set(classified_tables) | existing_tables)
            existing_cf = dict(acc.metadata.get("classified_fields") or {})
            existing_cf.update(classified_fields)
            acc.metadata["classified_fields"] = existing_cf
            if pii_fields:
                existing_pii = set(acc.metadata.get("pii_fields") or [])
                acc.metadata["pii_fields"] = sorted(existing_pii | set(pii_fields))
            if phi_fields:
                existing_phi = set(acc.metadata.get("phi_fields") or [])
                acc.metadata["phi_fields"] = sorted(existing_phi | set(phi_fields))

    @staticmethod
    def _structural_edge_related(a: Node, b: Node) -> bool:
        """Return True if two nodes have a real file-level relationship.

        Used to gate structural fallback edges (DEPLOYMENT<->CONTAINER_IMAGE,
        IAM<->DEPLOYMENT) so they don't degenerate into an all-to-all N*M
        cross join when a scan has several unrelated deployment/image nodes
        (e.g. one DEPLOYMENT node per keyword hit across start.sh, ci.yml,
        multiple Dockerfiles). Falls back to True when either node has no
        evidence locations at all, since there's nothing to disambiguate on.
        """
        paths_a = {ev.location.path for ev in a.evidence if ev.location and ev.location.path}
        paths_b = {ev.location.path for ev in b.evidence if ev.location and ev.location.path}
        if not paths_a or not paths_b:
            return True
        if paths_a & paths_b:
            return True
        # Same containing directory counts as related, but only for
        # non-root directories — most repos keep many unrelated files
        # (Dockerfile, docker-compose.yml, ci.yml, start.sh) at the repo
        # root, so matching on "." would reintroduce the cross join.
        dirs_a = {d for p in paths_a if (d := str(PurePosixPath(p).parent)) != "."}
        dirs_b = {d for p in paths_b if (d := str(PurePosixPath(p).parent)) != "."}
        if dirs_a & dirs_b:
            return True
        # Last resort: shared name token (e.g. a "postgres" DEPLOYMENT node
        # and a "postgres:14" CONTAINER_IMAGE node referenced by the same
        # compose service, even when their evidence cites different files).
        tokens_a = {t for t in re.split(r"[^a-z0-9]+", a.name.lower()) if len(t) > 2}
        tokens_b = {t for t in re.split(r"[^a-z0-9]+", b.name.lower()) if len(t) > 2}
        return bool(tokens_a & tokens_b)

    def _resolve_edges(
        self,
        doc: AiSbomDocument,
        node_map: dict[tuple[ComponentType, str], _NodeAccumulator],
    ) -> None:
        """Turn RelationshipHints into Edge objects using built node UUIDs.

        Falls back to simple type-based edge inference for any agents that
        don't already have explicit relationships.
        """
        # Build canonical_name → node.id lookup
        canonical_to_id: dict[str, Any] = {}
        for node in doc.nodes:
            canon = node.metadata.extras.get("canonical_name", "")
            if canon:
                canonical_to_id[canon] = node.id

        rel_type_map = {
            "USES": RelationshipType.USES,
            "CALLS": RelationshipType.CALLS,
            "ACCESSES": RelationshipType.ACCESSES,
            "PROTECTS": RelationshipType.PROTECTS,
            "DEPLOYS": RelationshipType.DEPLOYS,
            "DELEGATES_TO": RelationshipType.DELEGATES_TO,
        }

        # Process explicit relationship hints
        seen_edges: set[tuple[Any, Any, str]] = set()
        for acc in node_map.values():
            for hint in acc.relationships:
                # Hints may store raw canonical names (colons) while the node
                # lookup map uses canonicalized names (underscores). Try both.
                src_id = canonical_to_id.get(hint.source_canonical) or canonical_to_id.get(
                    canonicalize_text(hint.source_canonical)
                )
                tgt_id = canonical_to_id.get(hint.target_canonical) or canonical_to_id.get(
                    canonicalize_text(hint.target_canonical)
                )
                if src_id is None or tgt_id is None:
                    continue
                rel = rel_type_map.get(hint.relationship_type, RelationshipType.USES)
                edge_key = (src_id, tgt_id, hint.relationship_type)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                doc.edges.append(
                    Edge(
                        source=src_id,
                        target=tgt_id,
                        relationship_type=rel,
                        access_type=getattr(hint, "access_type", None),
                        derivation="hint",
                    )
                )

        # Fallback edge inference — fills in edges not covered by explicit hints.
        by_type: dict[ComponentType, list[Node]] = {}
        for node in doc.nodes:
            by_type.setdefault(node.component_type, []).append(node)

        def _add_edge(src_id: Any, tgt_id: Any, rel: str, confidence: float = 0.5) -> None:
            key = (src_id, tgt_id, rel)
            if key in seen_edges:
                return
            seen_edges.add(key)
            doc.edges.append(
                Edge(
                    source=src_id,
                    target=tgt_id,
                    relationship_type=rel_type_map.get(rel, RelationshipType.USES),
                    derivation="fallback_heuristic",
                    confidence=confidence,
                )
            )

        # Build framework name/adapter → id map for metadata-based fallbacks.
        fw_name_to_id: dict[str, Any] = {}
        for fw in by_type.get(ComponentType.FRAMEWORK, []):
            fw_meta_name = fw.metadata.framework or ""
            if fw_meta_name:
                fw_name_to_id.setdefault(fw_meta_name, fw.id)
            fw_name_to_id.setdefault(fw.name, fw.id)
            adapter_name = fw.metadata.extras.get("adapter", "")
            if adapter_name:
                fw_name_to_id.setdefault(adapter_name, fw.id)

        model_ids: set[Any] = {n.id for n in by_type.get(ComponentType.MODEL, [])}

        # Track which agents already have tool vs. model edges separately so
        # the fallback can add only what is missing.
        agent_ids_with_tool_edges: set[Any] = set()
        agent_ids_with_model_edges: set[Any] = set()
        for e in doc.edges:
            if e.relationship_type == RelationshipType.CALLS and e.target in {
                n.id for n in by_type.get(ComponentType.TOOL, [])
            }:
                agent_ids_with_tool_edges.add(e.source)
            if e.relationship_type == RelationshipType.USES and e.target in model_ids:
                agent_ids_with_model_edges.add(e.source)

        # Fallback: AGENT → TOOL (CALLS) for agents with no tool edges
        for agent in by_type.get(ComponentType.AGENT, []):
            if agent.id in agent_ids_with_tool_edges:
                continue
            for tool in sorted(by_type.get(ComponentType.TOOL, []), key=lambda n: n.name)[:5]:
                _add_edge(agent.id, tool.id, "CALLS")

        # Fallback: AGENT → MODEL (USES) for agents with no model edges
        for agent in by_type.get(ComponentType.AGENT, []):
            if agent.id in agent_ids_with_model_edges:
                continue
            for model in sorted(
                by_type.get(ComponentType.MODEL, []), key=lambda n: -n.confidence
            )[:3]:
                _add_edge(agent.id, model.id, "USES")

        # Fallback: FRAMEWORK → AGENT (CALLS) via shared metadata.framework
        for agent in by_type.get(ComponentType.AGENT, []):
            fw_name = agent.metadata.framework or ""
            fw_id = fw_name_to_id.get(fw_name)
            if fw_id:
                _add_edge(fw_id, agent.id, "CALLS")

        # Fallback: FRAMEWORK → TOOL (CALLS) via shared metadata.framework.
        # Covers cases where hint resolution already added explicit CALLS edges
        # (e.g. MCP tools) — _add_edge is idempotent so duplicate keys are skipped.
        for tool in by_type.get(ComponentType.TOOL, []):
            fw_name = tool.metadata.framework or ""
            fw_id = fw_name_to_id.get(fw_name)
            if fw_id:
                _add_edge(fw_id, tool.id, "CALLS")

        # Fallback: FRAMEWORK → MODEL (USES) for frameworks with no outgoing edges.
        # Covers custom-orchestrator apps (no AGENT nodes) where LLM provider
        # config was detected from YAML / regex without explicit AST hints.
        frameworks_with_outgoing: set[Any] = {e.source for e in doc.edges}
        for fw in by_type.get(ComponentType.FRAMEWORK, []):
            if fw.id in frameworks_with_outgoing:
                continue
            for model in sorted(
                by_type.get(ComponentType.MODEL, []),
                key=lambda n: -n.confidence,
            )[:5]:
                _add_edge(fw.id, model.id, "USES")

        # Fallback: AGENT → DATASTORE (ACCESSES) transitively via CALLS chain.
        # Requires TOOL→DATASTORE ACCESSES edges (from adapter hints or
        # PythonDatastoreAdapter same-file detection).
        tool_to_datastores: dict[Any, list[Any]] = {}
        for e in doc.edges:
            if e.relationship_type == RelationshipType.ACCESSES:
                tool_to_datastores.setdefault(e.source, []).append(e.target)

        for e in doc.edges:
            if e.relationship_type == RelationshipType.CALLS:
                for ds_id in tool_to_datastores.get(e.target, []):
                    _add_edge(e.source, ds_id, "ACCESSES")

        # Structural edges: DEPLOYMENT → CONTAINER_IMAGE (DEPLOYS).
        # Gated by _structural_edge_related to avoid an all-to-all N*M join
        # when several unrelated DEPLOYMENT keyword nodes and CONTAINER_IMAGE
        # nodes coexist in the same scan.
        for dep in by_type.get(ComponentType.DEPLOYMENT, []):
            for img in by_type.get(ComponentType.CONTAINER_IMAGE, []):
                if not self._structural_edge_related(dep, img):
                    continue
                key = (dep.id, img.id, "DEPLOYS")
                if key not in seen_edges:
                    seen_edges.add(key)
                    doc.edges.append(
                        Edge(
                            source=dep.id,
                            target=img.id,
                            relationship_type=RelationshipType.DEPLOYS,
                            derivation="fallback_heuristic",
                            confidence=0.6,
                        )
                    )

        # Structural edges: IAM → DEPLOYMENT (USES) — identity binds to infra.
        # Same relatedness gate as above.
        for iam_node in by_type.get(ComponentType.IAM, []):
            for dep in by_type.get(ComponentType.DEPLOYMENT, []):
                if not self._structural_edge_related(iam_node, dep):
                    continue
                key = (iam_node.id, dep.id, "USES")
                if key not in seen_edges:
                    seen_edges.add(key)
                    doc.edges.append(
                        Edge(
                            source=iam_node.id,
                            target=dep.id,
                            relationship_type=RelationshipType.USES,
                            derivation="fallback_heuristic",
                            confidence=0.6,
                        )
                    )

        # Structural edges: AUTH → API_ENDPOINT (PROTECTS).
        # Prefer the precise signal already carried by both node types
        # (metadata.extras["auth_type"], populated by fastapi_adapter's
        # _AUTH_CLASSES detection on both the AUTH node and the endpoints
        # it guards) over the previous broad "every AUTH node protects the
        # first 10 endpoints alphabetically" fallback. Only fall back to the
        # broad behavior for endpoints that carry no auth_type signal at
        # all, since that's the only case with no better information
        # available (e.g. non-FastAPI frameworks).
        def _auth_type(n: Node) -> str | None:
            v = n.metadata.extras.get("auth_type")
            return str(v) if v else None

        endpoints = by_type.get(ComponentType.API_ENDPOINT, [])
        endpoints_with_auth_type = [ep for ep in endpoints if _auth_type(ep)]
        endpoints_without_auth_type = [ep for ep in endpoints if not _auth_type(ep)]
        for auth in by_type.get(ComponentType.AUTH, []):
            matched = [ep for ep in endpoints_with_auth_type if _auth_type(ep) == _auth_type(auth)]
            is_matched = bool(matched)
            targets = matched if matched else sorted(endpoints_without_auth_type, key=lambda n: n.name)[:10]
            for ep in targets:
                key = (auth.id, ep.id, "PROTECTS")
                if key not in seen_edges:
                    seen_edges.add(key)
                    doc.edges.append(
                        Edge(
                            source=auth.id,
                            target=ep.id,
                            relationship_type=RelationshipType.PROTECTS,
                            derivation="hint" if is_matched else "fallback_heuristic",
                            confidence=None if is_matched else 0.4,
                        )
                    )

    async def _llm_enrich(
        self,
        doc: AiSbomDocument,
        file_contents: dict[str, str],
        config: AiSbomConfig,
    ) -> AiSbomDocument:
        """Phase 3: LLM-based enrichment of detection results.

        Steps:
        0. Gap-fill discovery — find component types absent from deterministic results
        1. Verify uncertain nodes (confidence 0.60–0.85) via LLM
        2. Re-aggregate confidence scores with LLM input baked in
        2.5. Annotate MCP FRAMEWORK nodes with a short LLM description
        3. Enrich the scan-level use-case summary
        """
        from nuguard.common.llm_client import LLMClient  # noqa: PLC0415

        from ..core.application_summary import (
            maybe_refine_use_case_summary_with_llm,  # noqa: PLC0415
        )
        from ..core.confidence import aggregate_node_confidence  # noqa: PLC0415
        from ..core.gap_fill import (  # noqa: PLC0415
            GapFillBudget,
            apply_discovery_results,
            discover_missing_nodes,
        )
        from ..core.verification import (  # noqa: PLC0415
            apply_verification_results,
            verify_uncertain_nodes,
        )

        client = LLMClient(
            model=config.llm_model,
            api_key=config.llm_api_key,
            api_base=config.llm_api_base,
            budget_tokens=config.llm_budget_tokens,
            google_api_key=config.google_api_key,
        )
        evidence_map = {n.id: n.evidence for n in doc.nodes}

        # Step 0: Gap-fill discovery — find component types absent (or, for
        # PROBE-eligible categories, plausibly under-represented) from
        # deterministic results.
        gap_fill_budget = GapFillBudget(
            max_calls=config.gap_fill_max_calls,
            max_cost_usd=config.gap_fill_max_cost_usd,
        )
        self_critique_categories = set()
        for raw_category in config.gap_fill_self_critique_categories:
            try:
                self_critique_categories.add(ComponentType(raw_category.strip().upper()))
            except ValueError:
                _log.warning("gap-fill: unknown self_critique_categories entry %r", raw_category)
        try:
            new_nodes, gap_fill_budget = await discover_missing_nodes(
                doc,
                file_contents,
                client,
                budget=gap_fill_budget,
                enable_privilege=config.gap_fill_enable_privilege,
                enable_guardrail=config.gap_fill_enable_guardrail,
                self_critique_categories=self_critique_categories,
            )
            doc = apply_discovery_results(doc, new_nodes)
            _log.info("gap-fill: %s", gap_fill_budget.to_dict())
        except Exception as exc:
            _log.warning("gap-fill: unexpected error — continuing without: %s", exc)

        # Step 1: Verify uncertain detections
        async def _llm_call(system: str, user: str) -> tuple[str, int]:
            # Fixed in #246. ``client.token_counts`` is a cumulative counter
            # shared by every in-flight verification call, so a before/after
            # delta of it can attribute tokens consumed by OTHER overlapping
            # calls to this call (inflated cost stats, and the "verification
            # cost" line in the report). Use the per-call counter that
            # ``LLMClient.complete`` accumulates under this coroutine's own
            # context instead — concurrent calls each read their own counter
            # and no cross-call attribution is possible.
            text = await client.complete(prompt=user, system=system)
            return text, sum(client.call_token_counts)

        results, v_stats = await verify_uncertain_nodes(
            doc.nodes,
            evidence_map,
            _llm_call,
            file_contents=file_contents,
            cost_budget=config.verification_cost_budget,
            max_candidates=config.verification_max_verifications,
            concurrency=config.llm_concurrency,
        )
        doc.nodes = apply_verification_results(doc.nodes, results)
        _log.info("llm verification: %s", v_stats.to_dict())

        # Step 2: Re-aggregate confidence with LLM scores
        doc.nodes, a_stats = aggregate_node_confidence(doc.nodes)
        _log.info("llm confidence aggregation: %s", a_stats.to_dict())

        # Step 2.5: Annotate MCP server FRAMEWORK nodes with a short LLM description.
        # These nodes have confidence=0.95 and skip verification, so we give the LLM
        # a dedicated chance to write a one-sentence description for each one.
        try:
            doc = await self._annotate_mcp_nodes(doc, file_contents, client)
        except Exception as exc:
            _log.warning("mcp-annotate: unexpected error — continuing without: %s", exc)

        # Step 3: Refine use-case summary with LLM
        if doc.summary:
            files_sample = list(file_contents.items())[:200]
            llm_ctx = {
                "use_case_summary": doc.summary.use_case,
                "modality_support": doc.summary.modality_support,
                "frameworks": doc.summary.frameworks,
            }
            doc.summary.use_case = await maybe_refine_use_case_summary_with_llm(
                llm_ctx, doc.nodes, files_sample, llm_client=client
            )

        # Step 4: IaC security summary for security practitioners
        # Only run when IaC/deployment nodes are present and budget remains.
        try:
            doc = await self._llm_summarize_iac(doc, client)
        except Exception as exc:
            _log.warning("iac-summary: unexpected error — continuing without: %s", exc)

        # Step 5: Enrich descriptions for AGENT/TOOL nodes missing them
        try:
            from ..llm_client import enrich_node_descriptions
            await enrich_node_descriptions(doc.nodes, client, concurrency=config.llm_concurrency)
            _log.info("description-enrichment: completed for agent/tool nodes")
        except Exception as exc:
            _log.warning("description-enrichment: unexpected error — continuing without: %s", exc)

        # Step 6: Build Markdown relationship graph (Mermaid diagram + LLM narrative)
        try:
            from ..core.relationship_graph import build_relationship_graph_with_llm
            graph_md = await build_relationship_graph_with_llm(doc, client)
            if graph_md:
                doc.relationship_graph_md = graph_md
                _log.info("relationship-graph: Mermaid diagram and narrative generated")
        except Exception as exc:
            _log.warning("relationship-graph: unexpected error — continuing without: %s", exc)

        # Step 7: Generate LLM descriptive names for all nodes
        try:
            from ..llm_client import enrich_descriptive_names
            await enrich_descriptive_names(doc.nodes, client)
            _log.info("descriptive-names: completed")
        except Exception as exc:
            _log.warning("descriptive-names: unexpected error — continuing without: %s", exc)

        # Recompute node_counts from the final node list after all verification,
        # aggregation, and discovery steps — gap-fill may have added nodes that
        # verification later rejected, leaving the counts stale.
        if doc.summary:
            from ..types import ComponentType as _CT
            doc.summary.node_counts = {
                ct.value: sum(1 for n in doc.nodes if n.component_type == ct)
                for ct in _CT
                if any(n.component_type == ct for n in doc.nodes)
            }

        # Write LLM token usage into the summary
        if doc.summary:
            _in, _out = client.token_counts
            doc.summary.tokens_used_for_enrichment = _in + _out
            doc.summary.input_tokens_used = _in
            doc.summary.output_tokens_used = _out
            doc.summary.llm_model_used = config.llm_model

        _in, _out = client.token_counts
        _log.info("llm enrichment complete: tokens_used=%d", _in + _out)
        return doc

    async def _llm_summarize_iac(
        self,
        doc: AiSbomDocument,
        client: Any,
    ) -> AiSbomDocument:
        """Step 4: Generate a security-professional IaC summary via LLM.

        Collects all DEPLOYMENT, CONTAINER_IMAGE, and IAM nodes, assembles a
        structured JSON context, and asks the LLM to produce a concise
        security briefing covering:
        - Cloud/runtime deployment posture (providers, regions, availability zones)
        - HA and resilience configuration
        - Secret management and encryption posture
        - IAM / least-privilege assessment
        - CI/CD pipeline security (GitHub Actions, OIDC, runners)
        - Container image security (rootless, health-checks, resource limits)

        The result is stored in ``doc.summary.iac_security_summary``.
        """
        import json as _json

        if doc.summary is None:
            return doc

        # Gather IaC-relevant nodes
        iac_types = {ComponentType.DEPLOYMENT, ComponentType.CONTAINER_IMAGE, ComponentType.IAM}
        iac_nodes = [n for n in doc.nodes if n.component_type in iac_types]
        if not iac_nodes:
            return doc

        # Build a compact representation of each node for the LLM prompt
        node_summaries: list[dict[str, Any]] = []
        for n in iac_nodes:
            meta = n.metadata
            ns: dict[str, Any] = {
                "type": n.component_type.value,
                "name": n.name,
            }
            # DEPLOYMENT fields
            if n.component_type == ComponentType.DEPLOYMENT:
                for attr in (
                    "deployment_target",
                    "cloud_region",
                    "availability_zones",
                    "secret_store",
                    "encryption_at_rest",
                    "encryption_key_ref",
                    "ha_mode",
                    "has_health_check",
                    "has_resource_limits",
                    "runs_as_root",
                ):
                    v = getattr(meta, attr, None)
                    if v is not None:
                        ns[attr] = v
                # GHA-specific extras
                for key in (
                    "workflow_triggers",
                    "runners",
                    "cloud_providers",
                    "uses_oidc",
                    "environments",
                ):
                    v = meta.extras.get(key)
                    if v is not None:
                        ns[key] = v
            # CONTAINER_IMAGE fields
            elif n.component_type == ComponentType.CONTAINER_IMAGE:
                for attr in ("base_image", "runs_as_root", "has_health_check"):
                    v = getattr(meta, attr, None)
                    if v is not None:
                        ns[attr] = v
            # IAM fields
            elif n.component_type == ComponentType.IAM:
                for attr in (
                    "iam_type",
                    "principal",
                    "permissions",
                    "iam_scope",
                    "trust_principals",
                ):
                    v = getattr(meta, attr, None)
                    if v is not None:
                        ns[attr] = v
                cloud_provider = meta.extras.get("cloud_provider")
                if cloud_provider:
                    ns["cloud_provider"] = cloud_provider

            node_summaries.append(ns)

        # Pull top-level security aggregate fields from summary
        aggregate: dict[str, Any] = {
            "secret_stores": doc.summary.secret_stores,
            "availability_zones": doc.summary.availability_zones,
            "encryption_at_rest_coverage": doc.summary.encryption_at_rest_coverage,
            "security_findings": doc.summary.security_findings,
            "iam_principals": doc.summary.iam_principals,
            "service_accounts": doc.summary.service_accounts,
        }

        user_prompt = (
            "You are analysing an AI application's infrastructure configuration.\n\n"
            "## Detected Infrastructure Nodes\n"
            f"```json\n{_json.dumps(node_summaries, indent=2)}\n```\n\n"
            "## Aggregate Security Signals\n"
            f"```json\n{_json.dumps(aggregate, indent=2)}\n```\n\n"
            "Write a concise security briefing for a security engineer / DevSecOps "
            "practitioner. Use exactly this Markdown structure and heading text:\n\n"
            "## Security briefing\n\n"
            "### 1) Deployment posture\n"
            "- **Cloud provider / runtime:** <evidence or Not evidenced>\n"
            "- **Regions / HA / resilience:** <evidence or Not evidenced>\n"
            "- **Assessment:** <one sentence>\n\n"
            "### 2) Secret management\n"
            "- **Secret stores:** <evidence or Not evidenced>\n"
            "- **Credential handling:** <evidence or Not evidenced>\n"
            "- **Assessment:** <one sentence>\n\n"
            "### 3) Encryption\n"
            "- **Encryption at rest:** <evidence or Not evidenced>\n"
            "- **Key management:** <evidence or Not evidenced>\n"
            "- **Assessment:** <one sentence>\n\n"
            "### 4) IAM / least privilege\n"
            "- **Principals / service accounts:** <evidence or Not evidenced>\n"
            "- **Scope / permissions / trust:** <evidence or Not evidenced>\n"
            "- **Assessment:** <one sentence>\n\n"
            "### 5) CI/CD security\n"
            "- **Runners / triggers:** <evidence or Not evidenced>\n"
            "- **OIDC / credential flow:** <evidence or Not evidenced>\n"
            "- **Assessment:** <one sentence>\n\n"
            "### 6) Container security\n"
            "- **Images / root user:** <evidence or Not evidenced>\n"
            "- **Health checks / resource limits:** <evidence or Not evidenced>\n"
            "- **Assessment:** <one sentence>\n\n"
            "### 7) Top 3 prioritized actions\n"
            "1. <action tied to evidence>\n"
            "2. <action tied to evidence>\n"
            "3. <action tied to evidence>\n\n"
            "Rules:\n"
            "- Stay under 500 words.\n"
            "- Use only the provided JSON. Do not infer providers, plaintext secrets, "
            "permissions, regions, containers, or key management when absent.\n"
            "- Treat `${{ secrets.NAME }}` and similar placeholders as secret references, "
            "not plaintext secret values. Call it a plaintext secret only if an actual "
            "secret value is shown.\n"
            "- When data is missing, write `Not evidenced` rather than guessing.\n"
            "- Prefer stable field names from the JSON over model-specific phrasing."
        )

        system_prompt = (
            "You are a cloud security architect producing IaC security briefings. "
            "Follow the requested template exactly. Use precise technical language. "
            "Do not hallucinate — only report what the provided data shows."
        )

        raw = await client.complete(prompt=user_prompt, system=system_prompt)
        _log.info("iac-summary: generated %d chars", len(raw))
        doc.summary.iac_security_summary = raw.strip()
        return doc

    async def _annotate_mcp_nodes(
        self,
        doc: AiSbomDocument,
        file_contents: dict[str, str],
        client: Any,
    ) -> AiSbomDocument:
        """Step 2.5: Generate short LLM descriptions for MCP FRAMEWORK nodes.

        Deterministic MCP nodes have confidence=0.95 and are skipped by the
        verification pass.  This step asks the LLM to write a one-sentence
        description for each MCP server that does not already have one,
        covering: server name, exposed tools, transport, and auth mechanism.
        """
        import json as _json

        mcp_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.FRAMEWORK
            and "mcp"
            in str(n.metadata.extras.get("framework", "") or n.metadata.framework or n.name).lower()
            and not n.metadata.extras.get("description")
        ]
        if not mcp_nodes:
            _log.debug("mcp-annotate: no undescribed MCP nodes — skipping")
            return doc

        # Collect associated tools / auth / endpoints per server canonical name
        def _extras_framework(node: Any) -> str:
            return str(node.metadata.extras.get("framework", "")).lower()

        tool_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.TOOL
            and _extras_framework(n) in ("mcp-server", "mcp_server")
        ]
        auth_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.AUTH
            and _extras_framework(n) in ("mcp-server", "mcp_server")
        ]
        ep_nodes = [
            n
            for n in doc.nodes
            if n.component_type == ComponentType.API_ENDPOINT
            and _extras_framework(n) in ("mcp-server", "mcp_server")
        ]

        # Build a compact payload for the LLM
        servers_payload = []
        for mcp_node in mcp_nodes:
            ex = mcp_node.metadata.extras
            servers_payload.append(
                {
                    "server_name": ex.get("server_name") or mcp_node.name,
                    "tools": [t.name for t in tool_nodes[:12]],
                    "auth": [a.name for a in auth_nodes[:4]],
                    "endpoints": [
                        {
                            "display": e.name,
                            "transport": e.metadata.extras.get("transport", ""),
                            "host": e.metadata.extras.get("host", ""),
                            "port": e.metadata.extras.get("port", ""),
                        }
                        for e in ep_nodes[:4]
                    ],
                }
            )

        system = (
            "You are an AI asset cataloguer. Given MCP server metadata, "
            "write a SHORT one-sentence description for each server. "
            "Include: server name, tool count + names (up to 5), transport, and auth type. "
            'Return a JSON array: [{"server_name": "...", "description": "..."}]. '
            "Return ONLY the JSON array, no prose."
        )
        user = "Generate descriptions for these MCP servers:\n" + _json.dumps(
            servers_payload, indent=2
        )

        try:
            raw = await client.complete(prompt=user, system=system)
            _log.debug("mcp-annotate: completed")
            text = raw.strip()
            if text.startswith("```"):
                text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("```"))
            start, end = text.find("["), text.rfind("]")
            if start != -1 and end > start:
                results = _json.loads(text[start : end + 1])
                name_to_desc = {
                    str(r.get("server_name", "")).lower(): str(r.get("description", ""))
                    for r in results
                    if isinstance(r, dict) and r.get("description")
                }
                for mcp_node in mcp_nodes:
                    ex = mcp_node.metadata.extras
                    key = str(ex.get("server_name") or mcp_node.name).lower()
                    desc = name_to_desc.get(key) or next(iter(name_to_desc.values()), "")
                    if desc:
                        mcp_node.metadata.extras["description"] = desc[:2000]
                        _log.info("mcp-annotate: described %r → %s", mcp_node.name, desc[:80])
        except Exception as exc:
            _log.warning("mcp-annotate: LLM call failed: %s", exc)

        return doc

    # Git rejects positional refs that begin with ``-`` by refusing them as
    # "ambiguous argument", but only after it has already consumed earlier
    # options.  Some versions of git also accept leading ``-`` arguments as
    # flags in older builds (see CVE-2017-1000117 et al.), so we reject the
    # value up-front rather than rely on the child process's behaviour.
    # Accepting such a ref would let a hostile ref string (e.g. one pasted from
    # a malicious README) trick ``git clone`` into invoking other git options
    # such as ``--upload-pack=<command>`` — a known argument-injection vector.
    _SAFE_REF_RE = re.compile(r"^[^-,\s\x00][^,\s\x00]*\Z")
    # Same defence for the URL: even though the CLI validates it as http(s)
    # upstream, ``extract_from_repo`` is a public API callable from any
    # embedding, so we re-validate here to avoid the same injection class.
    # Accepted at the clone boundary:
    #   - http(s)://host/path
    #   - ssh://[user@]host[:port]/path
    #   - scp-style SSH: [user@]host:path  where the path either starts
    #     with ``/`` (absolute) or with a non-flag character (so a
    #     hostile provider cannot smuggle a flag through
    #     ``git@host:--option``). ``extract_from_repo`` historically
    #     accepted the scp form, so the regex preserves that compatibility
    #     while still rejecting anything that smells like an injected flag.
    # The scp path sub-pattern forbids ``-``/``,``/``\s``/``\x00``/``\Z`` at
    # the start (no leading flag or whitespace) and continues with
    # safe characters. The host portion also forbids ``-`` so a hostile
    # user@ portion cannot itself look like an option.
    _SAFE_URL_RE = re.compile(
        r"(?:https?|ssh)://[^\s\x00]*\Z"
        r"|"
        r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[^-,:\s\x00][^,\s\x00]*\Z"
    )

    @staticmethod
    def _clone_repo(url: str, ref: str, dest: Path) -> None:
        if shutil.which("git") is None:
            raise RuntimeError("git executable not found on PATH")
        # Reject argument-injection attempts.  ``ref`` and ``url`` are passed
        # to ``git clone`` as positional arguments; if either starts with
        # ``-`` git may interpret it as a flag (``--upload-pack=<cmd>``,
        # ``--config=<key>=<value>``, etc.), giving the ref provider a way to
        # run arbitrary commands on the operator's machine.
        if not AiSbomExtractor._SAFE_REF_RE.match(ref):
            raise ValueError(
                f"Invalid git ref {ref!r}: must not be empty, start with '-', "
                "or contain whitespace."
            )
        if not AiSbomExtractor._SAFE_URL_RE.match(url):
            raise ValueError(
                f"Invalid repository URL {url!r}: must be an absolute URL with "
                "an explicit scheme (e.g. https://...)."
            )
        # Use ``--`` to terminate option parsing so any future ref/url values
        # that pass validation can never be reinterpreted as flags.
        cmd = [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            ref,
            "--",
            url,
            str(dest),
        ]
        _log.debug("running: %s", " ".join(cmd))
        try:
            result = subprocess.run(cmd, check=True, capture_output=True)
            _log.debug(
                "git clone succeeded (stderr: %s)",
                result.stderr.decode(errors="replace").strip()[:200] or "(none)",
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace").strip() if exc.stderr else ""
            raise RuntimeError(
                f"git clone failed for {url!r} @ {ref!r}" + (f": {stderr}" if stderr else "")
            ) from exc

    @staticmethod
    def _iter_files(root: Path, config: AiSbomConfig) -> Iterator[tuple[Path, int]]:
        import fnmatch as _fnmatch  # noqa: PLC0415

        _gitignore_match = _load_gitignore_matcher(root) if getattr(config, "honor_gitignore", True) else None
        count = 0
        for dirpath, dirnames, filenames in os.walk(root):
            # Keep traversal deterministic and prune irrelevant dirs early.
            dirnames[:] = sorted(d for d in dirnames if not _should_skip_path_parts((d,)))
            for filename in sorted(filenames):
                path = Path(dirpath) / filename
                suffix = path.suffix.lower()
                # Always include Dockerfile* files (extensionless or .dockerfile suffix)
                is_dockerfile = (
                    suffix in _DOCKERFILE_EXTENSIONS or path.name.lower() in _DOCKERFILE_NAMES
                )
                if suffix not in config.include_extensions and not is_dockerfile:
                    continue
                # Skip common irrelevant directories
                parts = path.parts
                if _should_skip_path_parts(parts):
                    continue
                # Skip .github/** except .github/workflows/**
                if ".github" in parts and "workflows" not in parts:
                    continue
                # Skip meta/tooling instruction files
                if path.name in {"CLAUDE.md", "AGENTS.md"}:
                    continue
                # Skip NuGuard-specific config, policy, and output files
                # Match any nuguard-prefixed config/output file, not just exact "nuguard.yaml"
                if path.name.lower().startswith("nuguard") and suffix in {".yaml", ".yml", ".json"}:
                    continue
                if _should_skip_filename(path.name):
                    continue
                name_lower = path.name.lower()
                if name_lower in {"cognitive_policy.md", "cognitive_policy.json"}:
                    continue
                if name_lower.endswith("sbom.json") or name_lower.endswith("aibom.json"):
                    continue
                if name_lower.endswith(".sbom.enriched.json"):
                    continue
                if name_lower.startswith("redteam-prompts-"):
                    continue
                if name_lower.startswith("nuguard-sbom-") and suffix in {".yaml", ".yml"}:
                    continue
                if name_lower in {"mcp_test_results.json", "canary.json"}:
                    continue
                # User-configured exclude patterns
                if config.exclude_patterns:
                    _rel = str(path.relative_to(root))
                    if any(
                        _fnmatch.fnmatch(_rel, pat) or _fnmatch.fnmatch(filename, pat)
                        for pat in config.exclude_patterns
                    ):
                        continue
                # .gitignore support
                if _gitignore_match is not None and _gitignore_match(
                    str(path.relative_to(root))
                ):
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > config.max_file_size_bytes:
                    continue
                yield path, size
                count += 1
                if config.max_files is not None and count >= config.max_files:
                    return

