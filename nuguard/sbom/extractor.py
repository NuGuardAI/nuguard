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
import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters.base import (
    AdapterMatch,
    ComponentDetection,
    DetectionAdapter,
    FrameworkAdapter,
    RelationshipHint,
)
from .adapters.data_classification import DataClassificationSQLAdapter
from .adapters.dockerfile import DockerfileAdapter
from .adapters.registry import default_framework_adapters, default_registry
from .adapters.nginx import NginxAdapter, is_nginx_file
from .adapters.yaml_adapters import (
    AutoGenYAMLAdapter,
    CrewAIYAMLAdapter,
    LLMYAMLConfigAdapter,
    PromptFileAdapter,
)
from .adapters.iac import (
    BicepAdapter,
    CloudFormationAdapter,
    GcpDeploymentManagerAdapter,
    GitHubActionsAdapter,
    K8sAdapter,
    TerraformAdapter,
)
from .adapters.typescript._ts_regex import TSFrameworkAdapter
from .config import AiSbomConfig
from .core.application_summary import build_scan_summary
from .core.ts_parser import TSParseResult, parse_typescript as _parse_ts_impl
from .deps import DependencyScanner
from .models import AiSbomDocument, Edge, Evidence, Node, ScanSummary, SourceLocation
from .normalization import canonicalize_text
from .types import ComponentType, RelationshipType

_log = logging.getLogger(__name__)

# File extensions that warrant Python AST parsing
_PYTHON_EXTENSIONS = {".py", ".pyw"}
# SQL schema files: scanned by DataClassificationSQLAdapter
_SQL_EXTENSIONS = {".sql"}
# Jupyter notebooks: cells are extracted and parsed as Python
_NOTEBOOK_EXTENSIONS = {".ipynb"}
# TypeScript/JavaScript: tree-sitter (or regex fallback) via core/ts_parser
_TYPESCRIPT_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx"}
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
    except Exception:
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
    from xelo.adapters.base import ComponentDetection
    from xelo.normalization import canonicalize_text
    from xelo.types import ComponentType

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
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".mk",
}
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
    if suffix in _IAC_EXTENSIONS:
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
        sql_adapters: tuple[DataClassificationSQLAdapter, ...] | None = None,
        dockerfile_adapter: DockerfileAdapter | None = None,
        yaml_adapters: tuple[Any, ...] | None = None,
        nginx_adapter: NginxAdapter | None = None,
        prompt_file_adapter: PromptFileAdapter | None = None,
        iac_adapters: tuple[Any, ...] | None = None,
        load_plugins: bool = False,
    ) -> None:
        from .plugins import load_plugins as _load_plugins

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
            sql_adapters if sql_adapters is not None else (DataClassificationSQLAdapter(),)
        )
        self.dockerfile_adapter = (
            dockerfile_adapter if dockerfile_adapter is not None else DockerfileAdapter()
        )
        self.yaml_adapters = (
            yaml_adapters
            if yaml_adapters is not None
            else (CrewAIYAMLAdapter(), AutoGenYAMLAdapter(), LLMYAMLConfigAdapter())
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
        files = list(self._iter_files(root, config))
        _log.info("scanning %d files under %s", len(files), root)
        doc = AiSbomDocument(target=source_ref or str(root))
        node_map: dict[tuple[ComponentType, str], _NodeAccumulator] = {}
        # Classification-only metadata from data_classification adapters (not emitted as nodes)
        _dc_metadata: list[dict[str, Any]] = []
        # Accumulated for Phase 3 LLM enrichment (rel_path → content)
        file_contents: dict[str, str] = {}

        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                _log.warning("skipping unreadable file %s: %s", file_path, exc)
                continue

            rel_path = str(file_path.relative_to(root))
            file_contents[rel_path] = content
            suffix = file_path.suffix.lower()
            is_python = suffix in _PYTHON_EXTENSIONS
            is_notebook = suffix in _NOTEBOOK_EXTENSIONS
            is_typescript = suffix in _TYPESCRIPT_EXTENSIONS
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
                        for adapter in self.framework_adapters:
                            # Skip TypeScript adapters for Python/notebook files
                            if isinstance(adapter, TSFrameworkAdapter):
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

                        # Phase 1a-prime: module-level Python prompt constants.
                        # Runs on ALL .py files regardless of framework imports so that
                        # prompt-only modules (e.g. prompts.py with no langchain imports)
                        # are still processed.
                        for det in _extract_python_prompt_constants(parse_result, rel_path):
                            self._merge_detection(node_map, det)

            # Phase 1b: SQL schema — data classification
            elif is_sql:
                _log.debug("running SQL data classification on %s", rel_path)
                for sql_adapter in self.sql_adapters:
                    try:
                        detections = sql_adapter.scan(content, rel_path)
                    except Exception as exc:
                        _log.warning(
                            "SQL adapter %r failed on %s: %s", sql_adapter.name, rel_path, exc
                        )
                        continue
                    for det in detections:
                        _dc_metadata.append(det.metadata)

            # Phase 1c: TypeScript/JavaScript AST-aware framework adapters
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
                # For MODEL adapter with per-match naming (canonical_name=None on
                # the adapter), emit one node per distinct model name so that a
                # single file containing multiple different models (e.g. a mock
                # route.ts listing gpt-5, deepseek-v3.2, gemini-3-pro) gets a
                # separate node for each instead of only the first match.
                if detection.component_type == ComponentType.MODEL and not getattr(
                    rx_adapter, "canonical_name", None
                ):
                    # Group matches by their normalised model name; keep first
                    # occurrence as the representative match for location/snippet.
                    _model_first: dict[str, AdapterMatch] = {}
                    _model_count: dict[str, int] = {}
                    for _m in detection.matches:
                        _key = _m.snippet.strip().lower()
                        if _key not in _model_first:
                            _model_first[_key] = _m
                            _model_count[_key] = 1
                        else:
                            _model_count[_key] += 1
                    for _raw_lower, _first_match in _model_first.items():
                        _raw_name = _first_match.snippet.strip()
                        _cnt = _model_count[_raw_lower]
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

        # Deduplicate nodes that share (component_type, file, line) — e.g. a
        # regex adapter and an AST adapter both firing on the same token.
        _dedup_by_location(node_map)
        # Deduplicate nodes where one name is a prefix of another from the same
        # file — e.g. regex matches "gemini-2.0" while AST extracts the full
        # "gemini-2.0-flash" from an adjacent line of the same call.
        _dedup_by_name_prefix(node_map)
        # Suppress generic tech-name DATASTORE nodes emitted by regex adapters
        # when the same file already has specific AST-detected ones for the same
        # technology (e.g. "faiss" regex when "docs_index" / "tickets_index" AST
        # nodes already exist in that file with provider="faiss").
        _suppress_generic_tech_regex_datastore(node_map)

        # For MODEL and DATASTORE, suppress nodes whose only evidence comes from
        # docs-tier files (lock files, README mentions, shell scripts).  Lock
        # files like pnpm-lock.yaml and semantic changelogs are classified as
        # DOCS tier and produce noisy nodes when package names happen to look
        # like model or datastore names.  IaC-tier detections (YAML/JSON config,
        # Dockerfiles) are kept because they legitimately describe components.
        _suppress_non_code_model_datastore(node_map)

        # Build nodes + edges
        for key in sorted(node_map.keys(), key=lambda v: (v[0].value, v[1])):
            acc = node_map[key]

            # Cross-tier corroboration: each additional source tier adds a
            # small confidence boost (capped at 0.99) because independent
            # evidence from code + IaC or code + docs raises certainty.
            if len(acc.source_tiers) > 1:
                acc.confidence = min(0.99, acc.confidence + 0.03 * (len(acc.source_tiers) - 1))

            node = Node(
                name=acc.display_name,
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

            node.evidence = sorted(acc.evidence, key=lambda e: e.confidence, reverse=True)
            doc.nodes.append(node)

        self._resolve_edges(doc, node_map)

        # Scan package manifest dependencies (pyproject.toml, requirements*.txt, package.json, …)
        doc.deps = DependencyScanner().scan(root)
        _log.info("deps scan: %d packages found", len(doc.deps))

        # Build deterministic scan-level summary (always populated)
        files_sample = list(file_contents.items())[:200]
        doc.summary = _make_scan_summary(
            build_scan_summary(
                doc.nodes,
                files_sample,
                source_ref=source_ref,
                branch=branch,
                dc_metadata=_dc_metadata,
            )
        )

        # Phase 3: LLM enrichment (skipped unless enable_llm=True)
        if config.enable_llm:
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

        with tempfile.TemporaryDirectory(prefix="xelo_") as temp_dir:
            repo_dir = Path(temp_dir) / "repo" / app_name
            repo_dir.mkdir(parents=True, exist_ok=True)
            self._clone_repo(url=url, ref=ref, dest=repo_dir)
            return self.extract_from_path(repo_dir, config, source_ref=display_url, branch=ref)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_python(content: str) -> Any | None:
        """Run the AST parser; return None on parse failure."""
        try:
            from .ast_parser import parse

            result = parse(content)
            return result
        except Exception:
            return None

    @staticmethod
    def _parse_typescript(content: str, file_path: str = "") -> TSParseResult:
        """Parse TypeScript/JavaScript via tree-sitter (or regex fallback)."""
        return _parse_ts_impl(content, file_path or None)

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
        for meta in dc_metadata:
            all_labels.update(meta.get("data_classification") or [])
            table = meta.get("table_name") or meta.get("model_name")
            if table:
                classified_tables.append(table)
                cf = meta.get("classified_fields")
                if cf:
                    classified_fields[table] = sorted(cf.keys())

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
        }

        # Process explicit relationship hints
        seen_edges: set[tuple[Any, Any, str]] = set()
        for acc in node_map.values():
            for hint in acc.relationships:
                src_id = canonical_to_id.get(hint.source_canonical)
                tgt_id = canonical_to_id.get(hint.target_canonical)
                if src_id is None or tgt_id is None:
                    continue
                rel = rel_type_map.get(hint.relationship_type, RelationshipType.USES)
                edge_key = (src_id, tgt_id, hint.relationship_type)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                doc.edges.append(Edge(source=src_id, target=tgt_id, relationship_type=rel))

        # Fallback: connect agents to tools/models they have no explicit link to
        by_type: dict[ComponentType, list[Node]] = {}
        for node in doc.nodes:
            by_type.setdefault(node.component_type, []).append(node)

        agent_ids_with_edges: set[Any] = {e.source for e in doc.edges}

        for agent in by_type.get(ComponentType.AGENT, []):
            if agent.id in agent_ids_with_edges:
                continue  # Already has explicit edges

            for tool in sorted(by_type.get(ComponentType.TOOL, []), key=lambda n: n.name)[:5]:
                key = (agent.id, tool.id, "CALLS")
                if key not in seen_edges:
                    seen_edges.add(key)
                    doc.edges.append(
                        Edge(
                            source=agent.id,
                            target=tool.id,
                            relationship_type=RelationshipType.CALLS,
                        )
                    )
            for model in sorted(by_type.get(ComponentType.MODEL, []), key=lambda n: n.name)[:3]:
                key = (agent.id, model.id, "USES")
                if key not in seen_edges:
                    seen_edges.add(key)
                    doc.edges.append(
                        Edge(
                            source=agent.id,
                            target=model.id,
                            relationship_type=RelationshipType.USES,
                        )
                    )

        # Fallback: connect frameworks to models when no explicit edges exist.
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
                key = (fw.id, model.id, "USES")
                if key not in seen_edges:
                    seen_edges.add(key)
                    doc.edges.append(
                        Edge(
                            source=fw.id,
                            target=model.id,
                            relationship_type=RelationshipType.USES,
                        )
                    )

        # Structural edges: DEPLOYMENT → CONTAINER_IMAGE (DEPLOYS)
        for dep in by_type.get(ComponentType.DEPLOYMENT, []):
            for img in by_type.get(ComponentType.CONTAINER_IMAGE, []):
                key = (dep.id, img.id, "DEPLOYS")
                if key not in seen_edges:
                    seen_edges.add(key)
                    doc.edges.append(
                        Edge(
                            source=dep.id,
                            target=img.id,
                            relationship_type=RelationshipType.DEPLOYS,
                        )
                    )

        # Structural edges: IAM → DEPLOYMENT (USES) — identity binds to infra
        for iam_node in by_type.get(ComponentType.IAM, []):
            for dep in by_type.get(ComponentType.DEPLOYMENT, []):
                key = (iam_node.id, dep.id, "USES")
                if key not in seen_edges:
                    seen_edges.add(key)
                    doc.edges.append(
                        Edge(
                            source=iam_node.id,
                            target=dep.id,
                            relationship_type=RelationshipType.USES,
                        )
                    )

        # Structural edges: AUTH → API_ENDPOINT (PROTECTS)
        for auth in by_type.get(ComponentType.AUTH, []):
            for ep in sorted(by_type.get(ComponentType.API_ENDPOINT, []), key=lambda n: n.name)[
                :10
            ]:
                key = (auth.id, ep.id, "PROTECTS")
                if key not in seen_edges:
                    seen_edges.add(key)
                    doc.edges.append(
                        Edge(
                            source=auth.id,
                            target=ep.id,
                            relationship_type=RelationshipType.PROTECTS,
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
        from .llm_client import LLMClient
        from .core.application_summary import maybe_refine_use_case_summary_with_llm
        from .core.confidence import aggregate_node_confidence
        from .core.gap_fill import apply_discovery_results, discover_missing_nodes
        from .core.verification import apply_verification_results, verify_uncertain_nodes

        client = LLMClient(
            model=config.llm_model,
            api_key=config.llm_api_key,
            api_base=config.llm_api_base,
            budget_tokens=config.llm_budget_tokens,
            google_api_key=config.google_api_key,
            vertex_location=config.vertex_location,
        )
        evidence_map = {n.id: n.evidence for n in doc.nodes}

        # Step 0: Gap-fill discovery — find component types absent from deterministic results
        gap_budget = min(config.llm_budget_tokens // 3, 15_000)
        try:
            new_nodes = await discover_missing_nodes(
                doc, file_contents, client, budget_tokens=gap_budget
            )
            doc = apply_discovery_results(doc, new_nodes)
            _log.info("gap-fill: %d new node(s) discovered", len(new_nodes))
        except Exception as exc:
            _log.warning("gap-fill: unexpected error — continuing without: %s", exc)

        # Step 1: Verify uncertain detections
        results, v_stats = await verify_uncertain_nodes(
            doc.nodes, evidence_map, client.complete_text, file_contents=file_contents
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

        _log.info("llm enrichment complete: tokens_used=%d", client.tokens_used)
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
            "Write a concise (≤500 words) security briefing for a security engineer / DevSecOps "
            "practitioner reviewing this AI application. Cover:\n"
            "1. **Deployment posture** — cloud providers, regions, HA / resilience setup\n"
            "2. **Secret management** — secret stores in use, any plaintext-secret risks\n"
            "3. **Encryption** — encryption-at-rest coverage and key management\n"
            "4. **IAM / least-privilege** — service accounts, OIDC trusts, "
            "over-permissive policies\n"
            "5. **CI/CD security** — GitHub Actions runners, OIDC config, "
            "workflow trigger surface\n"
            "6. **Container security** — root containers, missing health checks, "
            "missing resource limits\n"
            "7. **Key risks and recommendations** — top 3 prioritised actions\n\n"
            "Respond in plain Markdown. Be specific; avoid generic statements."
        )

        system_prompt = (
            "You are a cloud security architect producing IaC security briefings. "
            "Use precise technical language. Do not hallucinate — only report what "
            "the provided data shows."
        )

        raw, tokens = await client.complete_text(system_prompt, user_prompt)
        _log.info("iac-summary: generated %d chars using %d tokens", len(raw), tokens)
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
            raw, tokens = await client.complete_text(system, user)
            _log.debug("mcp-annotate: %d tokens used", tokens)
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

    @staticmethod
    def _clone_repo(url: str, ref: str, dest: Path) -> None:
        if shutil.which("git") is None:
            raise RuntimeError("git executable not found on PATH")
        cmd = ["git", "clone", "--depth", "1", "--branch", ref, url, str(dest)]
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
    def _iter_files(root: Path, config: AiSbomConfig) -> Iterator[Path]:
        count = 0
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            # Always include Dockerfile* files (extensionless or .dockerfile suffix)
            is_dockerfile = (
                suffix in _DOCKERFILE_EXTENSIONS or path.name.lower() in _DOCKERFILE_NAMES
            )
            if suffix not in config.include_extensions and not is_dockerfile:
                continue
            # Skip common irrelevant directories
            parts = set(path.parts)
            if parts & {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox", ".claude"}:
                continue
            # Skip .github/** except .github/workflows/**
            if ".github" in parts and "workflows" not in parts:
                continue
            # Skip meta/tooling instruction files
            if path.name in {"CLAUDE.md", "AGENTS.md"}:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > config.max_file_size_bytes:
                continue
            yield path
            count += 1
            if count >= config.max_files:
                break


def _make_scan_summary(d: dict[str, Any]) -> ScanSummary:
    """Convert the dict from ``build_scan_summary`` into a typed ``ScanSummary``."""
    return ScanSummary(
        use_case=d.get("use_case_summary") or "",
        frameworks=d.get("frameworks") or [],
        modalities=d.get("modalities") or [],
        modality_support=d.get("modality_support") or {},
        api_endpoints=d.get("api_endpoints") or [],
        deployment_platforms=d.get("deployment_platforms") or [],
        regions=d.get("regions") or [],
        environments=d.get("environments") or [],
        deployment_urls=d.get("deployment_urls") or [],
        iac_accounts=d.get("subscription_account_project") or [],
        node_counts=d.get("node_type_counts") or {},
        data_classification=d.get("data_classification") or [],
        classified_tables=d.get("classified_tables") or [],
        # IaC security / resilience aggregate fields
        secret_stores=d.get("secret_stores") or [],
        availability_zones=d.get("availability_zones") or [],
        encryption_at_rest_coverage=bool(d.get("encryption_at_rest_coverage")),
        security_findings=d.get("security_findings") or [],
        iam_principals=d.get("iam_principals") or [],
        service_accounts=d.get("service_accounts") or [],
    )


def _dedup_by_name_prefix(
    node_map: dict[tuple[ComponentType, str], _NodeAccumulator],
) -> None:
    """Remove accumulator entries whose name is a strict prefix of another
    entry of the same component type that shares at least one source file.

    Handles cases where a regex adapter extracts a truncated model name
    (e.g. ``gemini-2.0``) while an AST adapter extracts the full string
    (``gemini-2.0-flash``) from an adjacent line of the same call.
    The shorter entry is dropped and its evidence absorbed by the longer one.
    """
    keys = list(node_map.keys())
    keys_to_remove: set[tuple[ComponentType, str]] = set()

    for i, key_a in enumerate(keys):
        if key_a in keys_to_remove:
            continue
        acc_a = node_map[key_a]
        files_a = {ev.location.path for ev in acc_a.evidence if ev.location}

        for key_b in keys[i + 1 :]:
            if key_b in keys_to_remove:
                continue
            if key_a[0] != key_b[0]:  # must be same component_type
                continue
            acc_b = node_map[key_b]
            files_b = {ev.location.path for ev in acc_b.evidence if ev.location}

            if not files_a & files_b:  # must share at least one file
                continue

            name_a = acc_a.display_name.lower()
            name_b = acc_b.display_name.lower()
            if name_b.startswith(name_a) and name_b != name_a:
                # a is the shorter prefix — drop it, keep b
                node_map[key_b].evidence.extend(node_map[key_a].evidence)
                keys_to_remove.add(key_a)
                _log.debug("dedup_by_name_prefix: dropped %s → kept %s", key_a, key_b)
                break
            elif name_a.startswith(name_b) and name_a != name_b:
                # b is the shorter prefix — drop it, keep a
                node_map[key_a].evidence.extend(node_map[key_b].evidence)
                keys_to_remove.add(key_b)
                _log.debug("dedup_by_name_prefix: dropped %s → kept %s", key_b, key_a)

    for k in keys_to_remove:
        del node_map[k]


def _dedup_by_location(
    node_map: dict[tuple[ComponentType, str], _NodeAccumulator],
) -> None:
    """Remove accumulator entries that share (component_type, file, line) with a
    higher-priority entry, merging their evidence into the winner.

    Applies when two adapters fire on the exact same source token — e.g. an AST
    adapter producing ``gemini-2.0-flash`` and a regex adapter producing
    ``gemini-2.0`` from the same line.  The lower-priority-number (higher
    precedence) adapter wins; ties broken by confidence descending.
    """
    # loc → [key, ...] for all keys that have at least one evidence item at that location
    loc_to_keys: dict[tuple[ComponentType, str, int | None], list[tuple[ComponentType, str]]] = {}
    for key, acc in node_map.items():
        for ev in acc.evidence:
            if ev.location:
                loc = (key[0], ev.location.path, ev.location.line)
                if key not in loc_to_keys.get(loc, []):
                    loc_to_keys.setdefault(loc, []).append(key)

    # Helper: does an accumulator have at least one regex-based evidence item?
    def _has_regex_evidence(acc: _NodeAccumulator) -> bool:
        return any(ev.kind == "regex" for ev in acc.evidence)

    keys_to_remove: set[tuple[ComponentType, str]] = set()
    for loc, keys in loc_to_keys.items():
        if len(keys) <= 1:
            continue
        # Sort: lower priority number = higher precedence; break ties by confidence desc
        keys_sorted = sorted(
            keys,
            key=lambda k: (node_map[k].priority, -node_map[k].confidence),
        )
        winner = keys_sorted[0]
        for loser in keys_sorted[1:]:
            if loser in keys_to_remove:
                continue
            # Only deduplicate when at least one node has regex evidence.
            # Two AST-only nodes at the same line are distinct components (e.g.
            # multiple imports on one line → multiple FAISS stores) and must
            # both be kept.
            if not (_has_regex_evidence(node_map[winner]) or _has_regex_evidence(node_map[loser])):
                continue
            # Absorb evidence so the winner node reflects all source locations
            node_map[winner].evidence.extend(node_map[loser].evidence)
            keys_to_remove.add(loser)
            _log.debug(
                "dedup_by_location: dropped %s (priority=%d conf=%.2f) → kept %s",
                loser,
                node_map[loser].priority,
                node_map[loser].confidence,
                winner,
            )

    for k in keys_to_remove:
        del node_map[k]


def _suppress_generic_tech_regex_datastore(
    node_map: dict[tuple[ComponentType, str], _NodeAccumulator],
) -> None:
    """Suppress generic tech-name DATASTORE nodes emitted by regex adapters when
    the same file already has specific AST-detected DATASTORE nodes for the same
    underlying technology.

    Example: a regex adapter emitting ``faiss`` from an import line in a file
    where the AST adapter has already extracted specific named stores
    (``docs_index``, ``tickets_index``, etc. with ``provider="faiss"``).  The
    generic node is redundant and introduces false positives.

    Only purely regex-evidence nodes whose ``display_name`` matches a known
    vector-store / database technology shortname are candidates for suppression.
    Suppression only fires when at least one specific (non-regex) DATASTORE node
    shares the same source file AND has the same technology in its provider
    metadata (or its display name starts with the tech name as a prefix).
    """
    keys_to_remove: set[tuple[ComponentType, str]] = set()

    # Collect: file → set of tech names from specific (non-regex) DATASTORE nodes
    file_to_specific_techs: dict[str, set[str]] = {}
    for key, acc in node_map.items():
        if key[0] != ComponentType.DATASTORE:
            continue
        if all(ev.kind == "regex" for ev in acc.evidence):
            continue  # Skip — this is itself a regex-only node
        provider = str(acc.metadata.get("provider", "")).lower().strip()
        tech = provider or acc.display_name.lower()
        for ev in acc.evidence:
            if ev.location and ev.location.path:
                file_to_specific_techs.setdefault(ev.location.path, set()).add(tech)

    # Identify generic regex-only DATASTORE nodes that are covered by specific ones
    for key, acc in node_map.items():
        if key[0] != ComponentType.DATASTORE:
            continue
        if not all(ev.kind == "regex" for ev in acc.evidence):
            continue  # Only suppress regex-only nodes
        tech_name = acc.display_name.lower()
        # Check whether any source file for this node already has a specific node
        for ev in acc.evidence:
            if ev.location and ev.location.path:
                specific_techs = file_to_specific_techs.get(ev.location.path, set())
                if tech_name in specific_techs:
                    keys_to_remove.add(key)
                    _log.debug(
                        "suppress_generic_tech_regex: dropped generic %r"
                        " (file %s already has specific %r nodes)",
                        tech_name,
                        ev.location.path,
                        tech_name,
                    )
                    break

    for k in keys_to_remove:
        del node_map[k]


def _suppress_non_code_model_datastore(
    node_map: dict[tuple[ComponentType, str], _NodeAccumulator],
) -> None:
    """Drop MODEL and DATASTORE nodes whose only evidence is from DOCS tier.

    Detections from lock files (``pnpm-lock.yaml``, ``package-lock.json``),
    README mentions, shell scripts, and plain-text files frequently produce
    spurious MODEL/DATASTORE nodes.  These files are classified as DOCS tier;
    IaC-tier detections (YAML configs, JSON configs, Dockerfiles) are kept
    because they legitimately describe datastores and models in environment
    definitions.

    Only DOCS-tier-only nodes are dropped.  This does not affect AGENT, TOOL,
    PROMPT, etc. which are typically harder to detect and worth surfacing from
    any tier.
    """
    _suppressed_types = {ComponentType.MODEL, ComponentType.DATASTORE}
    keys_to_drop = [
        key
        for key, acc in node_map.items()
        if key[0] in _suppressed_types and acc.best_tier_rank >= _TIER_RANK[_TIER_DOCS]
    ]
    for key in keys_to_drop:
        _log.debug(
            "suppress_docs_only: dropped %s (best_tier_rank=%d)",
            key,
            node_map[key].best_tier_rank,
        )
        del node_map[key]


def stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
