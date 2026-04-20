"""Google Customer Engagement Suite (CES) SBOM scanner.

Detects CES deployment configuration from HTML, JS/TS/Python, JSON, and
plain-text files by searching for CES resource name patterns and API URLs.
This scanner operates independently of the Python/TS AST adapters because
CES configuration typically lives in HTML/JS front-end files rather than
pure Python imports.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nuguard.sbom.models import AiSbomDocument

from nuguard.common.ces_client import (
    CES_FRAMEWORK_NAME,
    CES_REQUEST_SCHEMA,
    CES_RESPONSE_TEXT_KEY,
)
from nuguard.sbom.models import Evidence, Node, NodeMetadata, SourceLocation
from nuguard.sbom.types import ComponentType

_log = logging.getLogger(__name__)

# Extensions to scan for CES patterns
_SCAN_EXTENSIONS = frozenset({
    ".html", ".htm", ".js", ".ts", ".jsx", ".tsx",
    ".py", ".json", ".txt", ".md",
})

# Directories to skip
_SKIP_DIRS = frozenset({"node_modules", ".venv", "venv", "__pycache__", ".git", ".tox"})

# ---------------------------------------------------------------------------
# Regular expressions
# ---------------------------------------------------------------------------

# Segment pattern: real project/app/location IDs — alphanumeric, hyphens, dots, underscores.
# Excludes curly-brace placeholders like {PROJECT} or {_APP_ID} used in f-strings.
_SEG = r"[A-Za-z0-9][A-Za-z0-9._-]*"

_DEPLOYMENT_NAME_RE = re.compile(
    rf"projects/({_SEG})/locations/({_SEG})/apps/({_SEG})/deployments/({_SEG})"
)
_APP_VERSION_RE = re.compile(
    rf"projects/({_SEG})/locations/({_SEG})/apps/({_SEG})/versions/({_SEG})"
)
_CES_URL_RE = re.compile(r"https://ces\.googleapis\.com/[^\s\"'<>]+")
_SESSION_URL_RE = re.compile(
    rf"ces\.googleapis\.com/v1beta/projects/({_SEG})/locations/({_SEG})/apps/({_SEG})"
)

# ---------------------------------------------------------------------------
# Variable extraction and template resolution
# ---------------------------------------------------------------------------

# Match simple string-constant assignments in Python or JS/TS:
#   PROJECT   = "platform-dev-2025"        (Python)
#   const APP_ID = "2f519af5-..."          (JS/TS)
#   _VERSION_ID = "9c99ba4b-..."           (Python, underscore-prefixed)
_PY_VAR_RE = re.compile(
    # Handles plain strings and f-strings: VAR = "..." or VAR = f"..."
    r"(?:^|\n)[ \t]*(_?[A-Za-z][A-Za-z0-9_]*)[ \t]*=[ \t]*f?[\"']([^\"']{4,})[\"']",
    re.MULTILINE,
)
_JS_VAR_RE = re.compile(
    # Handles const/let/var with plain or template strings
    r"(?:const|let|var)[ \t]+(_?[A-Za-z][A-Za-z0-9_]*)[ \t]*=[ \t]*[`\"']([^`\"']{4,})[`\"']",
)


def extract_string_vars(content: str) -> dict[str, str]:
    """Return a mapping of variable-name → raw-string-value from *content*.

    Handles Python bare assignments (``PROJECT = "..."``) and f-string
    assignments (``_APP_RESOURCE = f"projects/{PROJECT}/..."``), plus JS/TS
    ``const``/``let``/``var`` declarations.  Only captures values ≥4 chars.
    """
    raw: dict[str, str] = {}
    for name, value in _JS_VAR_RE.findall(content):
        raw[name] = value
    for name, value in _PY_VAR_RE.findall(content):
        raw[name] = value
    return raw


def resolve_template(content: str, vars: dict[str, str]) -> str:
    """Substitute ``{VAR}`` (Python) and ``${VAR}`` (JS/TS) placeholders.

    Replaces every occurrence of ``{NAME}`` and ``${NAME}`` in *content* with
    the corresponding value from *vars*, enabling regex patterns that require
    literal IDs to match after variable expansion.
    """
    for name, value in vars.items():
        content = content.replace(f"{{{name}}}", value)   # Python f-string: {VAR}
        content = content.replace(f"${{{name}}}", value)  # JS template:    ${VAR}
    return content


def resolve_vars_two_pass(content: str) -> tuple[str, dict[str, str]]:
    """Extract variables then resolve them in two passes.

    Pass 1: extract raw variable values (may contain ``{OTHER_VAR}``).
    Pass 2: resolve variable-to-variable references within the extracted values,
            then substitute everything into the content.

    This handles one level of indirection, e.g.:
        PROJECT      = "platform-dev-2025"
        _APP_RESOURCE = f"projects/{PROJECT}/locations/us/apps/{APP_ID}"
        ... f"{_APP_RESOURCE}/versions/{VERSION}" ...

    Returns the resolved content and the final resolved variable map.
    """
    # Pass 1: raw extraction
    raw = extract_string_vars(content)
    # Pass 2: resolve inter-variable references within the values themselves
    resolved = {k: resolve_template(v, raw) for k, v in raw.items()}
    # Substitute into the full content
    return resolve_template(content, resolved), resolved


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CESDetection:
    """A single detected CES app deployment."""

    project: str
    location: str
    app_id: str
    version_id: str = ""
    deployment_id: str = ""
    source_file: str = ""
    line: int = 0

    # Computed in __post_init__
    ces_base_url: str = field(init=False)
    run_session_url_template: str = field(init=False)

    def __post_init__(self) -> None:
        app_resource = (
            f"projects/{self.project}/locations/{self.location}/apps/{self.app_id}"
        )
        self.ces_base_url = f"https://ces.googleapis.com/v1beta/{app_resource}"
        self.run_session_url_template = (
            f"{self.ces_base_url}/sessions/{{session_id}}:runSession"
        )


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class CESScanner:
    """Scans source files for Google CES deployment configuration.

    Detects:
    - HTML: deploymentName resource strings, ces.googleapis.com URLs
    - JS/TS/Python: ces.googleapis.com URL patterns, runSession calls
    - JSON: app.json, deployment.txt with CES resource names
    """

    def scan_directory(self, source_path: Path) -> list[CESDetection]:
        """Scan a directory tree for CES deployment config.

        Args:
            source_path: Root directory to walk.

        Returns:
            List of unique CES detections (one per app_id).
        """
        raw_detections: list[CESDetection] = []

        for file_path in source_path.rglob("*"):
            # Skip directories
            if not file_path.is_file():
                continue
            # Skip unwanted directories
            parts = set(file_path.relative_to(source_path).parts)
            if parts & _SKIP_DIRS:
                continue
            if file_path.suffix.lower() not in _SCAN_EXTENSIONS:
                continue

            try:
                content = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                _log.debug("CESScanner: cannot read %s: %s", file_path, exc)
                continue

            # Skip nuguard-generated output files (e.g. *.sbom.json with "generator": "nuguard")
            if file_path.suffix.lower() == ".json" and '"generator"' in content[:512]:
                try:
                    import json as _json  # noqa: PLC0415
                    _peek = _json.loads(content)
                    if isinstance(_peek, dict) and str(_peek.get("generator", "")).lower().startswith("nuguard"):
                        _log.debug("CESScanner: skipping nuguard-generated file: %s", file_path)
                        continue
                except Exception:  # noqa: BLE001
                    pass

            rel_path = str(file_path.relative_to(source_path))
            dets = self.scan_file(file_path, content)
            for det in dets:
                det.source_file = rel_path
            raw_detections.extend(dets)

        return _merge_detections(raw_detections)

    def scan_file(self, file_path: Path, content: str) -> list[CESDetection]:
        """Scan a single file's content for CES patterns.

        Variable references (``{PROJECT}``, ``${APP_ID}``, etc.) are resolved
        before pattern matching so that f-string / template-literal URLs are
        expanded to their literal values.

        Args:
            file_path: Path (used only for logging).
            content: File content string.

        Returns:
            List of CES detections found in this file.
        """
        # Resolve variable placeholders (two-pass: handles chained refs like
        # _APP_RESOURCE = f"projects/{PROJECT}/..." used in f"{_APP_RESOURCE}/versions/{VERSION}")
        content, file_vars = resolve_vars_two_pass(content)
        if file_vars:
            _log.debug(
                "CESScanner: resolved %d variable(s) in %s: %s",
                len(file_vars),
                file_path.name,
                ", ".join(f"{k}={v[:20]!r}" for k, v in list(file_vars.items())[:6]),
            )

        detections: list[CESDetection] = []

        def _lineno(match: re.Match) -> int:  # type: ignore[type-arg]
            return content.count("\n", 0, match.start()) + 1

        # Match deployment resource names
        for match in _DEPLOYMENT_NAME_RE.finditer(content):
            project, location, app_id, deployment_id = match.groups()
            detections.append(
                CESDetection(
                    project=project,
                    location=location,
                    app_id=app_id,
                    deployment_id=deployment_id,
                    source_file=str(file_path),
                    line=_lineno(match),
                )
            )

        # Match version resource names
        for match in _APP_VERSION_RE.finditer(content):
            project, location, app_id, version_id = match.groups()
            detections.append(
                CESDetection(
                    project=project,
                    location=location,
                    app_id=app_id,
                    version_id=version_id,
                    source_file=str(file_path),
                    line=_lineno(match),
                )
            )

        # Match session URL patterns (ces.googleapis.com/v1beta/projects/...)
        for match in _SESSION_URL_RE.finditer(content):
            project, location, app_id = match.groups()
            detections.append(
                CESDetection(
                    project=project,
                    location=location,
                    app_id=app_id,
                    source_file=str(file_path),
                    line=_lineno(match),
                )
            )

        return detections


def _merge_detections(raw: list[CESDetection]) -> list[CESDetection]:
    """Merge detections with the same app_id into a single entry.

    A deployment file may carry ``deployment_id`` while a Python file carries
    ``version_id`` — both contribute to the same logical CES app.

    Args:
        raw: Flat list of raw detections (possibly duplicates).

    Returns:
        Deduplicated list, one entry per (project, location, app_id).
    """
    merged: dict[str, CESDetection] = {}
    source_files: dict[str, list[str]] = {}

    for det in raw:
        key = f"{det.project}/{det.location}/{det.app_id}"
        if key not in merged:
            merged[key] = CESDetection(
                project=det.project,
                location=det.location,
                app_id=det.app_id,
                version_id=det.version_id,
                deployment_id=det.deployment_id,
                source_file=det.source_file,
                line=det.line,
            )
            source_files[key] = [det.source_file] if det.source_file else []
        else:
            existing = merged[key]
            if not existing.version_id and det.version_id:
                # Recreate to trigger __post_init__
                merged[key] = CESDetection(
                    project=existing.project,
                    location=existing.location,
                    app_id=existing.app_id,
                    version_id=det.version_id,
                    deployment_id=existing.deployment_id,
                    source_file=existing.source_file,
                    line=existing.line or det.line,
                )
            if not merged[key].deployment_id and det.deployment_id:
                cur = merged[key]
                merged[key] = CESDetection(
                    project=cur.project,
                    location=cur.location,
                    app_id=cur.app_id,
                    version_id=cur.version_id,
                    deployment_id=det.deployment_id,
                    source_file=cur.source_file,
                    line=cur.line or det.line,
                )
            if det.source_file and det.source_file not in source_files[key]:
                source_files[key].append(det.source_file)

    # Attach combined source_file string
    result: list[CESDetection] = []
    for key, det in merged.items():
        files = source_files.get(key, [])
        # Update source_file to be the primary file (first unique one)
        det.source_file = files[0] if files else det.source_file
        result.append(det)
    return result


# ---------------------------------------------------------------------------
# SBOM node builder
# ---------------------------------------------------------------------------

_STABLE_NS = uuid.UUID("a3e4e7c0-4b2a-5f1d-8e3a-7c6b9d2f1a3e")


def _stable_id(key: str) -> uuid.UUID:
    return uuid.uuid5(_STABLE_NS, key)


def build_ces_sbom_nodes(
    detections: list[CESDetection],
    sbom_doc: "AiSbomDocument",
) -> list[Node]:
    """Build SBOM ``API_ENDPOINT`` and ``FRAMEWORK`` nodes for CES detections.

    Args:
        detections: List of CES detections from :class:`CESScanner`.
        sbom_doc: The SBOM document being built (used to check for duplicate nodes).

    Returns:
        List of new nodes to add to the document.
    """
    new_nodes: list[Node] = []

    # Check whether a google-ces FRAMEWORK node already exists
    existing_framework = any(
        n.component_type == ComponentType.FRAMEWORK
        and getattr(n.metadata, "framework", "") == CES_FRAMEWORK_NAME
        for n in sbom_doc.nodes
    )

    if not existing_framework and detections:
        fw_node = Node(
            id=_stable_id(f"framework:{CES_FRAMEWORK_NAME}"),
            name=CES_FRAMEWORK_NAME,
            component_type=ComponentType.FRAMEWORK,
            confidence=0.95,
            metadata=NodeMetadata(framework=CES_FRAMEWORK_NAME),
        )
        new_nodes.append(fw_node)

    for det in detections:
        # Avoid duplicate endpoint nodes for the same app_id
        existing_ep = any(
            n.component_type == ComponentType.API_ENDPOINT
            and getattr(n.metadata, "framework", "") == CES_FRAMEWORK_NAME
            and det.app_id in (getattr(n.metadata, "endpoint", "") or "")
            for n in sbom_doc.nodes
        )
        if existing_ep:
            _log.debug("CESScanner: skipping duplicate endpoint for app_id=%s", det.app_id)
            continue

        primary_loc = SourceLocation(path=det.source_file or "", line=det.line or None)

        evidence_list: list[Evidence] = [
            Evidence(
                kind="config",
                detail=f"Detected in: {det.source_file}" if det.source_file else "CES deployment detected",
                confidence=0.90,
                location=primary_loc,
            )
        ]
        if det.deployment_id:
            evidence_list.append(Evidence(
                kind="config",
                detail=f"Deployment: {det.deployment_id}",
                confidence=0.90,
                location=primary_loc,
            ))
        if det.version_id:
            evidence_list.append(Evidence(
                kind="config",
                detail=f"Version: {det.version_id}",
                confidence=0.90,
                location=primary_loc,
            ))

        ep_node = Node(
            id=_stable_id(f"ces:endpoint:{det.project}/{det.location}/{det.app_id}"),
            name="CES Agent (ces.googleapis.com)",
            component_type=ComponentType.API_ENDPOINT,
            confidence=0.90,
            metadata=NodeMetadata(
                framework=CES_FRAMEWORK_NAME,
                endpoint=det.run_session_url_template,
                method="POST",
                auth_type="gcloud",
                auth_required=True,
                request_body_schema={
                    k: json.dumps(v) if not isinstance(v, str) else v
                    for k, v in CES_REQUEST_SCHEMA.get("properties", {}).items()
                },
                response_text_key=CES_RESPONSE_TEXT_KEY,
                description=(
                    "Google Customer Engagement Suite (CES) agent endpoint. "
                    "Auth: gcloud Bearer token. "
                    "Request: {config, inputs}. Response: {outputs[0].text}."
                ),
            ),
            evidence=evidence_list,
        )
        new_nodes.append(ep_node)

    return new_nodes
