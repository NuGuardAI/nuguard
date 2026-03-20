"""Core AI-SBOM extractor pipeline entry point.

This module contains ``AiSbomExtractor``, which orchestrates:

1. Directory walk with pattern exclusion
2. Framework adapter extraction (LangChain, OpenAI, CrewAI, AutoGen)
3. IaC scanning (Terraform, Docker Compose, Kubernetes, GitHub Actions)
4. PII/PHI classification of datastores
5. Dependency scanning (pyproject.toml, requirements.txt, package.json)
6. Node deduplication and confidence merging
7. ScanSummary construction
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from nuguard.common.errors import ExtractorError
from nuguard.common.logging import get_logger
from nuguard.models.sbom import (
    AiSbomDocument,
    DataClassification,
    DatastoreType,
    Edge,
    EdgeRelationshipType,
    Evidence,
    EvidenceKind,
    EvidenceLocation,
    Node,
    NodeMetadata,
    NodeType,
    PackageDep,
    ScanSummary,
)
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.extractor.pii_classifier import PiiClassifier

if TYPE_CHECKING:
    pass


@runtime_checkable
class FrameworkAdapter(Protocol):
    def extract(self, file_path: Path, source: str) -> tuple[list[Node], list[Edge]]: ...


_log = get_logger(__name__)

_IaC_PATTERNS = [
    "*.tf",
    "docker-compose.yml",
    "docker-compose.yaml",
    "**/k8s/**/*.yaml",
    "**/k8s/**/*.yml",
    "**/.github/workflows/*.yml",
    "**/.github/workflows/*.yaml",
]

_DEP_FILES = {"pyproject.toml", "requirements.txt", "package.json"}


def _stable_id(name: str, component_type: NodeType) -> str:
    """Produce a stable 8-char hex ID from name + type."""
    raw = f"{name}:{component_type.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _is_excluded(path: Path, patterns: list[str]) -> bool:
    """Return True when *path* matches any of *patterns* using fnmatch."""
    path_str = str(path)
    for pattern in patterns:
        if fnmatch.fnmatch(path_str, pattern):
            return True
        # Also match against the path parts individually for simple wildcards
        if fnmatch.fnmatch(path.name, pattern):
            return True
    return False


def _is_iac(path: Path) -> bool:
    for pat in _IaC_PATTERNS:
        if fnmatch.fnmatch(str(path), pat) or fnmatch.fnmatch(path.name, pat):
            return True
    return False


class _NodeAccumulator:
    """Merge nodes by (name, component_type), keeping max confidence."""

    def __init__(self) -> None:
        self._nodes: dict[tuple[str, NodeType], Node] = {}

    def add(self, node: Node) -> None:
        key = (node.name, node.component_type)
        if key not in self._nodes:
            self._nodes[key] = node
        else:
            existing = self._nodes[key]
            # Merge evidence lists (deduplicated by detail)
            existing_details = {e.detail for e in existing.evidence}
            for ev in node.evidence:
                if ev.detail not in existing_details:
                    existing.evidence.append(ev)
                    existing_details.add(ev.detail)
            # Take max confidence
            existing.confidence = max(existing.confidence, node.confidence)

    def nodes(self) -> list[Node]:
        return list(self._nodes.values())

    def id_for(self, name: str, component_type: NodeType) -> str | None:
        node = self._nodes.get((name, component_type))
        return node.id if node else None


class AiSbomExtractor:
    """Main extractor that walks a source tree and produces an AI-SBOM.

    Usage::

        extractor = AiSbomExtractor()
        doc = extractor.extract_from_path(Path("./my-agent-app"))
    """

    def __init__(self) -> None:
        self._pii = PiiClassifier()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_from_path(
        self,
        path: Path,
        config: AiSbomConfig | None = None,
    ) -> AiSbomDocument:
        """Scan the directory at *path* and return an :class:`AiSbomDocument`.

        Args:
            path: Root directory to scan.
            config: Extractor configuration.  Uses defaults when ``None``.

        Returns:
            Populated :class:`~nuguard.models.sbom.AiSbomDocument`.

        Raises:
            :class:`~nuguard.common.errors.ExtractorError`: When *path* does
                not exist or is not a directory.
        """
        config = config or AiSbomConfig()
        path = Path(path).resolve()
        if not path.is_dir():
            raise ExtractorError(f"Source path is not a directory: {path}")

        _log.info("Scanning %s", path)

        accumulator = _NodeAccumulator()
        all_edges: list[Edge] = []
        all_deps: list[PackageDep] = []
        all_security_findings: list[str] = []

        # Walk all files
        python_files: list[Path] = []
        iac_files: list[Path] = []
        dep_files: list[Path] = []

        for file_path in path.rglob("*"):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(path)
            if _is_excluded(rel, config.exclude_patterns):
                continue
            if file_path.suffix == ".py":
                python_files.append(file_path)
            if config.include_iac and _is_iac(rel):
                iac_files.append(file_path)
            if config.include_deps and file_path.name in _DEP_FILES:
                dep_files.append(file_path)

        _log.debug(
            "Found %d Python files, %d IaC files, %d dep files",
            len(python_files),
            len(iac_files),
            len(dep_files),
        )

        # Framework adapters
        from nuguard.sbom.extractor.framework_adapters.langchain import (
            LangChainAdapter,
        )
        from nuguard.sbom.extractor.framework_adapters.openai_functions import (
            OpenAIFunctionsAdapter,
        )
        from nuguard.sbom.extractor.framework_adapters.crewai import CrewAIAdapter
        from nuguard.sbom.extractor.framework_adapters.autogen import AutoGenAdapter
        from nuguard.sbom.extractor.framework_adapters.mcp import McpAdapter
        from nuguard.sbom.extractor.framework_adapters.fastapi import FastApiAdapter
        from nuguard.sbom.extractor.framework_adapters.flask import FlaskAdapter
        from nuguard.sbom.extractor.prompt_detector import PromptDetector

        adapters: list[FrameworkAdapter] = [
            LangChainAdapter(),
            OpenAIFunctionsAdapter(),
            CrewAIAdapter(),
            AutoGenAdapter(),
            McpAdapter(),
            FastApiAdapter(),
            FlaskAdapter(),
        ]

        prompt_detector = PromptDetector()

        for py_file in python_files:
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for adapter in adapters:
                try:
                    nodes, edges = adapter.extract(py_file, source)
                except Exception as exc:  # noqa: BLE001
                    _log.debug("Adapter %s failed on %s: %s", adapter, py_file, exc)
                    continue
                for node in nodes:
                    if node.confidence >= config.min_confidence:
                        accumulator.add(node)
                all_edges.extend(edges)

            # Prompt detection
            try:
                prompt_nodes = prompt_detector.detect(py_file, source)
            except Exception as exc:  # noqa: BLE001
                _log.debug("PromptDetector failed on %s: %s", py_file, exc)
                prompt_nodes = []
            for node in prompt_nodes:
                if node.confidence >= config.min_confidence:
                    accumulator.add(node)

        # PII classification
        pii_files = python_files + [
            p for p in path.rglob("*.sql") if not _is_excluded(p.relative_to(path), config.exclude_patterns)
        ]
        for pii_file in pii_files:
            try:
                source = pii_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                classifications = self._pii.classify_file(pii_file, source)
            except Exception as exc:  # noqa: BLE001
                _log.debug("PII classifier failed on %s: %s", pii_file, exc)
                continue
            for table_name, classification in classifications:
                node_id = _stable_id(table_name, NodeType.DATASTORE)
                node = Node(
                    id=node_id,
                    name=table_name,
                    component_type=NodeType.DATASTORE,
                    confidence=0.7,
                    metadata=NodeMetadata(
                        datastore_type=DatastoreType.RELATIONAL,
                        data_classification=[classification],
                    ),
                    evidence=[
                        Evidence(
                            kind=EvidenceKind.AST,
                            confidence=0.7,
                            detail=f"{classification.value} classification from field analysis",
                            location=EvidenceLocation(path=str(pii_file)),
                        )
                    ],
                )
                if node.confidence >= config.min_confidence:
                    accumulator.add(node)

        # IaC scanning
        if config.include_iac:
            for iac_file in iac_files:
                try:
                    nodes, edges, iac_findings = self._scan_iac(iac_file)
                except Exception as exc:  # noqa: BLE001
                    _log.debug("IaC scan failed on %s: %s", iac_file, exc)
                    continue
                for node in nodes:
                    if node.confidence >= config.min_confidence:
                        accumulator.add(node)
                all_edges.extend(edges)
                all_security_findings.extend(iac_findings)

        # Dependency scanning
        if config.include_deps:
            for dep_file in dep_files:
                try:
                    deps = self._scan_deps(dep_file)
                    all_deps.extend(deps)
                except Exception as exc:  # noqa: BLE001
                    _log.debug("Dep scan failed on %s: %s", dep_file, exc)

        # Deduplicate edges (same source/target/type)
        seen_edges: set[tuple[str, str, str]] = set()
        deduped_edges: list[Edge] = []
        for edge in all_edges:
            key = (edge.source, edge.target, edge.relationship_type.value)
            if key not in seen_edges:
                seen_edges.add(key)
                deduped_edges.append(edge)

        final_nodes = accumulator.nodes()

        # Collect security findings from node metadata
        node_findings = self._collect_security_findings(final_nodes)
        all_security_findings.extend(node_findings)
        # Deduplicate while preserving order
        deduped_findings = list(dict.fromkeys(all_security_findings))

        summary = self._build_summary(final_nodes, all_deps, path, deduped_findings)

        # LLM enrichment (optional)
        if config.enable_llm:
            import asyncio
            from nuguard.common.llm_client import LLMClient
            from nuguard.sbom.extractor.llm_enricher import LLMEnricher

            doc_pre = AiSbomDocument(
                generated_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
                target=str(path),
                nodes=final_nodes,
                edges=deduped_edges,
                deps=all_deps,
                summary=summary,
            )
            llm_client = LLMClient(model=config.llm_model)
            enricher = LLMEnricher(llm_client=llm_client, budget_tokens=config.llm_budget_tokens)
            try:
                asyncio.get_event_loop().run_until_complete(enricher.enrich(doc_pre))
            except RuntimeError:
                # Already inside an event loop
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, enricher.enrich(doc_pre))
                    future.result()
            except Exception as exc:  # noqa: BLE001
                _log.debug("LLM enrichment failed: %s", exc)
            return doc_pre

        return AiSbomDocument(
            generated_at=datetime.now(timezone.utc),
            target=str(path),
            nodes=final_nodes,
            edges=deduped_edges,
            deps=all_deps,
            summary=summary,
        )

    def extract_from_repo(
        self,
        url: str,
        ref: str = "main",
        config: AiSbomConfig | None = None,
    ) -> AiSbomDocument:
        """Clone *url* at *ref* and run :meth:`extract_from_path`.

        Requires ``git`` to be available on the system PATH.

        Args:
            url: Git repository URL.
            ref: Branch or tag to check out.
            config: Extractor configuration.

        Returns:
            Populated :class:`~nuguard.models.sbom.AiSbomDocument`.
        """
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory(prefix="nuguard-repo-") as tmpdir:
            _log.info("Cloning %s@%s into %s", url, ref, tmpdir)
            subprocess.run(
                ["git", "clone", "--depth=1", "--branch", ref, url, tmpdir],
                check=True,
                capture_output=True,
            )
            return self.extract_from_path(Path(tmpdir), config=config)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_iac(self, path: Path) -> tuple[list[Node], list[Edge], list[str]]:
        """Route IaC file to the appropriate dedicated scanner."""
        if path.suffix == ".tf":
            from nuguard.sbom.extractor.iac_scanners.terraform import TerraformScanner
            scanner = TerraformScanner()
            nodes, edges, findings = scanner.scan(path)
            return nodes, edges, findings

        if path.name in ("docker-compose.yml", "docker-compose.yaml"):
            from nuguard.sbom.extractor.iac_scanners.docker_compose import DockerComposeScanner
            scanner_dc = DockerComposeScanner()
            dc_nodes, dc_findings = scanner_dc.scan(path)
            return dc_nodes, [], dc_findings

        if ".github/workflows" in str(path):
            from nuguard.sbom.extractor.iac_scanners.github_actions import GitHubActionsScanner
            scanner_gha = GitHubActionsScanner()
            gha_nodes, gha_findings = scanner_gha.scan(path)
            return gha_nodes, [], gha_findings

        # Kubernetes YAML (k8s/**/*.yaml pattern)
        if path.suffix in (".yaml", ".yml"):
            from nuguard.sbom.extractor.iac_scanners.kubernetes import KubernetesScanner
            scanner_k8s = KubernetesScanner()
            k8s_nodes, k8s_edges, k8s_findings = scanner_k8s.scan(path)
            return k8s_nodes, k8s_edges, k8s_findings

        return [], [], []

    def _collect_security_findings(self, nodes: list[Node]) -> list[str]:
        """Derive security findings from node metadata."""
        findings: list[str] = []
        for node in nodes:
            if node.component_type == NodeType.CONTAINER_IMAGE:
                if node.metadata.runs_as_root:
                    findings.append("container_runs_as_root")
                if node.metadata.has_health_check is False:
                    findings.append("missing_health_check")
                if node.metadata.has_resource_limits is False:
                    findings.append("no_resource_limits")
            if node.component_type == NodeType.IAM:
                perms = node.metadata.permissions
                if any("*" in p for p in perms) or "AdministratorAccess" in str(perms):
                    findings.append("overly_permissive_iam")
        return findings

    def _scan_deps(self, path: Path) -> list[PackageDep]:
        """Extract package dependencies from pyproject.toml / requirements.txt / package.json."""
        deps: list[PackageDep] = []
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return deps

        if path.name == "pyproject.toml":
            deps.extend(self._parse_pyproject(source, str(path)))
        elif path.name == "requirements.txt":
            deps.extend(self._parse_requirements(source, str(path)))
        elif path.name == "package.json":
            deps.extend(self._parse_package_json(source, str(path)))
        return deps

    def _parse_pyproject(self, source: str, source_file: str) -> list[PackageDep]:
        deps: list[PackageDep] = []
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                return deps

        try:
            data = tomllib.loads(source)
        except Exception:
            return deps

        for dep_str in data.get("project", {}).get("dependencies", []):
            # Simple parse: "package>=1.0" → name="package", version_spec=">=1.0"
            m = re.match(r"([A-Za-z0-9_\-]+)(.*)", dep_str.strip())
            if m:
                deps.append(
                    PackageDep(
                        name=m.group(1),
                        version_spec=m.group(2).strip() or None,
                        source_file=source_file,
                        ecosystem="pypi",
                    )
                )
        return deps

    def _parse_requirements(self, source: str, source_file: str) -> list[PackageDep]:
        deps: list[PackageDep] = []
        for line in source.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            m = re.match(r"([A-Za-z0-9_\-]+)(.*)", line)
            if m:
                deps.append(
                    PackageDep(
                        name=m.group(1),
                        version_spec=m.group(2).strip() or None,
                        source_file=source_file,
                        ecosystem="pypi",
                    )
                )
        return deps

    def _parse_package_json(self, source: str, source_file: str) -> list[PackageDep]:
        import json

        deps: list[PackageDep] = []
        try:
            data = json.loads(source)
        except json.JSONDecodeError:
            return deps

        all_deps: dict[str, str] = {}
        all_deps.update(data.get("dependencies", {}))
        all_deps.update(data.get("devDependencies", {}))
        for name, version_spec in all_deps.items():
            deps.append(
                PackageDep(
                    name=name,
                    version_spec=version_spec,
                    source_file=source_file,
                    ecosystem="npm",
                )
            )
        return deps

    def _build_summary(
        self,
        nodes: list[Node],
        deps: list[PackageDep],
        root: Path,
        security_findings: list[str] | None = None,
    ) -> ScanSummary:
        """Build a :class:`~nuguard.models.sbom.ScanSummary` from extracted data."""
        node_counts: dict[str, int] = defaultdict(int)
        for node in nodes:
            node_counts[node.component_type.value] += 1

        frameworks: list[str] = []
        for node in nodes:
            if node.component_type == NodeType.FRAMEWORK and node.name not in frameworks:
                frameworks.append(node.name)

        data_classifications: list[str] = []
        classified_tables: list[str] = []
        api_endpoints: list[str] = []
        deployment_platforms: list[str] = []

        for node in nodes:
            if node.component_type == NodeType.DATASTORE:
                for dc in node.metadata.data_classification:
                    if dc.value not in data_classifications:
                        data_classifications.append(dc.value)
                classified_tables.extend(node.metadata.classified_tables)
                if node.metadata.data_classification:
                    # Add the node name as a classified table if not already there
                    if node.name not in classified_tables:
                        classified_tables.append(node.name)
            elif node.component_type == NodeType.API_ENDPOINT:
                ep = node.metadata.endpoint or node.name
                if ep not in api_endpoints:
                    api_endpoints.append(ep)
            elif node.component_type == NodeType.DEPLOYMENT:
                plat = node.metadata.deployment_target or node.name
                if plat not in deployment_platforms:
                    deployment_platforms.append(plat)

        return ScanSummary(
            frameworks=frameworks,
            node_counts=dict(node_counts),
            security_findings=list(dict.fromkeys(security_findings or [])),
            data_classification=data_classifications,
            classified_tables=list(set(classified_tables)),
            api_endpoints=api_endpoints,
            deployment_platforms=deployment_platforms,
        )
