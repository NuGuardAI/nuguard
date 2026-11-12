"""Scan orchestration: clone → AI-SBOM → static analysis → result body.

Pure with respect to the service: no database, HTTP or process management.
:func:`run_pipeline` is called inside a worker process (see ``worker.py``) and
can be unit-tested directly.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

from nuguard import __version__
from nuguard.service.models import ScanOptions

if TYPE_CHECKING:
    from nuguard.service.results import ScanBody

EmitFn = Callable[[dict], None]

# Error codes a pipeline can fail with (job-level ProblemDetail.code).
CLONE_CODES = {
    "repo_not_found_or_no_access", "auth_failed", "ref_not_found", "clone_timeout",
    "repo_too_large",
}

_LANGUAGES = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".go": "go",
    ".cs": "csharp", ".java": "java", ".kt": "kotlin", ".tf": "terraform", ".bicep": "bicep",
}
_SCANNABLE_EXTRA = {".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".md", ".txt",
                    ".lock", ".mod", ".sum", ".xml", ".gradle", ".csproj", ".dockerfile"}


class PipelineError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class JobSpec(BaseModel):
    """Everything a worker needs, except secrets (token / LLM key travel separately)."""

    job_id: str
    repo_url: str  # as submitted (sanitized: no credentials)
    clone_url: str  # repository root, https://host/owner/repo
    ref: str | None = None
    subpath: str | None = None
    options: ScanOptions
    tools: list[str]
    allowed_hosts: list[str] | None = Field(
        description="Clone host policy; None disables it (tests against a local server only)"
    )
    clone_timeout_s: float = 120.0
    max_repo_bytes: int = 500 * 1024 * 1024
    llm_model: str | None = None
    llm_api_base: str | None = None
    image_digest: str | None = None
    test_hooks: list[str] = Field(default_factory=list)


def split_repo_url(repo_url: str, ref: str | None) -> tuple[str, str | None, str | None]:
    """Return (clone_url, effective_ref, url_subpath) for ``…/owner/repo[/tree/<ref>/<path>]``.

    With an explicit *ref*, the ``/tree/`` remainder must start with it (so refs
    containing ``/`` work); without one, the first segment is the ref.
    """
    parts = urlsplit(repo_url)
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) < 2:
        raise PipelineError("invalid_request", "repo_url path must be /<owner>/<repo>")
    owner, repo = segments[0], segments[1].removesuffix(".git")
    clone_url = urlunsplit(("https", parts.netloc, f"/{owner}/{repo}", "", ""))
    rest = "/".join(segments[3:]) if len(segments) > 3 and segments[2] == "tree" else ""
    if not rest:
        return clone_url, ref, None
    if ref is not None:
        if rest == ref:
            return clone_url, ref, None
        if rest.startswith(ref + "/"):
            return clone_url, ref, rest[len(ref) + 1:] or None
        raise PipelineError("invalid_request", "ref does not match the /tree/ path in repo_url")
    url_ref, _, sub = rest.partition("/")
    return clone_url, url_ref, sub or None


def combine_subpaths(*parts: str | None) -> str | None:
    joined = "/".join(p.strip("/") for p in parts if p)
    if not joined:
        return None
    path = PurePosixPath(joined)
    if ".." in path.parts or path.is_absolute():
        raise PipelineError("invalid_request", "subpath must stay inside the repository")
    return str(path)


def scan_tree_stats(root: Path) -> tuple[list[str], int, int]:
    """(languages, files_scanned, files_skipped) for the scan root, excluding .git.

    ``files_scanned`` counts source/config files the extractors read;
    ``files_skipped`` counts everything else (binaries, assets, …).
    """
    languages: Counter[str] = Counter()
    scanned = skipped = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            ext = Path(name).suffix.lower()
            if ext in _LANGUAGES:
                languages[_LANGUAGES[ext]] += 1
                scanned += 1
            elif ext in _SCANNABLE_EXTRA or name in ("Dockerfile", "Makefile"):
                scanned += 1
            else:
                skipped += 1
    return [lang for lang, _ in languages.most_common()], scanned, skipped


def run_pipeline(
    spec: JobSpec, token: str | None, scratch: Path, emit: EmitFn
) -> tuple[ScanBody, dict[str, Any]]:
    """Clone, extract and analyze.

    Returns the result body (the API process adds the job envelope and hash)
    and the full AI-SBOM JSON (served by ``/v1/scans/{id}/sbom``).
    """
    from nuguard.analysis.public_api import AnalysisRunRequest, run_analysis  # noqa: PLC0415
    from nuguard.analysis.static_analyzer import _root_prefixes, scrub_paths  # noqa: PLC0415
    from nuguard.sbom.config import AiSbomConfig  # noqa: PLC0415
    from nuguard.sbom.extractor.git_clone import (  # noqa: PLC0415
        CloneError,
        CloneOptions,
        secure_clone,
    )
    from nuguard.sbom.public_api import SbomGenerateRequest, generate_sbom  # noqa: PLC0415
    from nuguard.service import SERVICE_VERSION  # noqa: PLC0415
    from nuguard.service import fingerprint as fp  # noqa: PLC0415
    from nuguard.service.models import Provenance  # noqa: PLC0415
    from nuguard.service.results import (  # noqa: PLC0415
        ScanBody,
        build_coverage,
        inventory_from,
        project_findings,
        rulepack_sha256,
        severity_summary,
        status_for,
        tool_versions,
    )

    # 1. Clone
    emit({"type": "phase", "phase": "cloning"})
    repo_dir = scratch / "repo"
    try:
        commit = secure_clone(
            spec.clone_url,
            spec.ref,
            repo_dir,
            CloneOptions(
                token=token,
                allowed_hosts=tuple(spec.allowed_hosts) if spec.allowed_hosts is not None else None,
                timeout_s=spec.clone_timeout_s,
                max_repo_bytes=spec.max_repo_bytes,
            ),
        )
    except CloneError as exc:
        code = exc.code if exc.code in CLONE_CODES else "clone_failed"
        raise PipelineError(code, _clone_detail(code)) from None
    emit({"type": "commit", "sha": commit})

    scan_root = repo_dir
    if spec.subpath:
        candidate = (repo_dir / spec.subpath).resolve()
        try:
            candidate.relative_to(repo_dir.resolve())
        except ValueError:
            raise PipelineError("subpath_not_found", "subpath is outside the repository") from None
        if not candidate.is_dir():
            raise PipelineError("subpath_not_found", "subpath does not exist in the repository")
        scan_root = candidate

    # 2. AI-SBOM. Config is explicit, never read from the worker's environment.
    emit({"type": "phase", "phase": "sbom"})
    use_llm = bool(spec.options.use_llm and spec.llm_model)
    config = AiSbomConfig(
        enable_llm=use_llm,
        llm_model=spec.llm_model or "gpt-4o-mini",
        llm_api_key=os.environ.get("LITELLM_API_KEY") if use_llm else None,
        llm_api_base=spec.llm_api_base if use_llm else None,
        google_api_key=None,
        allowed_git_hosts=spec.allowed_hosts,
    )
    try:
        sbom_result = asyncio.run(
            generate_sbom(SbomGenerateRequest(source_path=str(scan_root), config=config))
        )
    except Exception:
        raise PipelineError("sbom_failed", "AI-SBOM generation failed") from None
    doc = sbom_result.sbom
    # The SBOM target would otherwise be the absolute checkout path.
    doc.target = spec.repo_url

    # 3. Static analysis
    emit({"type": "phase", "phase": "analyzing"})
    tools = set(spec.tools)
    request = AnalysisRunRequest(
        enable_atlas="atlas" in tools,
        enable_osv="osv" in tools,
        enable_grype="grype" in tools,
        enable_checkov="checkov" in tools,
        enable_trivy="trivy" in tools,
        enable_semgrep="semgrep" in tools,
        enable_supply_chain="supply-chain" in tools,
        source_path=str(scan_root),
        atlas_config={"llm": True, "llm_model": spec.llm_model} if use_llm else None,
        min_severity=spec.options.min_severity,
        offline=not spec.options.network,
        dedupe_posture=True,
    )
    try:
        analysis = asyncio.run(run_analysis(request, sbom=doc, llm_client=None))
    except Exception:
        raise PipelineError("analysis_crashed", "static analysis failed") from None

    # 4. Result body
    emit({"type": "phase", "phase": "finalizing"})
    findings = project_findings(
        analysis.findings,
        repo=fp.normalize_repo(spec.repo_url),
        scan_root=scan_root,
        include_posture=spec.options.include_posture,
    )
    languages, scanned, skipped = scan_tree_stats(scan_root)
    runs = list(analysis.tool_runs)
    if "nga-rules" not in tools:
        # NGA rules always run inside StaticAnalyzer; drop them when not requested.
        findings = [f for f in findings if f.source_tool != "nga-rules"]
    coverage = build_coverage(
        runs, spec.tools, languages=languages, files_scanned=scanned, files_skipped=skipped
    )
    body = ScanBody(
        status=status_for(coverage),
        commit_sha=commit,
        provenance=Provenance(
            nuguard_version=__version__,
            service_version=SERVICE_VERSION,
            image_digest=spec.image_digest,
            rulepack_sha256=rulepack_sha256(),
            tools=tool_versions(runs),
            options_effective=spec.options,
            llm_used=use_llm,
        ),
        coverage=coverage,
        summary=severity_summary(findings),
        findings=findings,
        inventory=inventory_from(doc),
        sbom=doc.model_dump(mode="json") if spec.options.include_sbom else None,
    )
    # Last line of defence: no checkout path may appear anywhere in the output.
    prefixes = _root_prefixes(scratch)
    body = ScanBody.model_validate_json(scrub_paths(body.model_dump_json(), prefixes))
    sbom_json = json.loads(scrub_paths(doc.model_dump_json(), prefixes))
    return body, sbom_json


def _clone_detail(code: str) -> str:
    return {
        "repo_not_found_or_no_access": "Repository not found, or the token cannot access it",
        "auth_failed": "The repository host rejected the credentials",
        "ref_not_found": "The requested ref does not exist",
        "clone_timeout": "Cloning the repository timed out",
        "repo_too_large": "The repository exceeds the size limit",
    }.get(code, "Cloning the repository failed")

