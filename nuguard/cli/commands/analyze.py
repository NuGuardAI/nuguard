"""``nuguard analyze`` — static risk analysis from an AI-SBOM.

Exit codes
----------
0  No findings at or above ``--min-severity``
1  One or more findings at or above ``--min-severity``
2  Analysis error (SBOM could not be read / parsed)
3  Not implemented / reserved
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, NamedTuple, Optional

import typer

from nuguard.cli.common import output_path_for_format, parse_output_formats
from nuguard.common.control_mappings.owasp import owasp_refs_for_rule
from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, Severity

analyze_app = typer.Typer(
    help="Static risk analysis from the AI-SBOM (no running app required).",
    no_args_is_help=True,
)

_log = get_logger("cli.analyze")

_SEV_ORDER: dict[str, int] = {
    "critical": 0,
    "high":     1,
    "medium":   2,
    "low":      3,
    "info":     4,
}

def _clone_remote_source_for_analysis(url: str) -> str | None:
    """Best-effort shallow clone of *url* into a fresh temp dir for local-file scans.

    ``nuguard analyze`` run standalone (the common ``sbom generate`` then
    ``analyze`` two-step pipeline) has no local checkout when ``source:`` in
    nuguard.yaml is a remote GitHub URL — the temp clone ``sbom generate``
    made is deleted before ``analyze`` starts. Without this, supply-chain's
    raw-file fallback (and Checkov/Trivy/Semgrep) silently scan zero files
    instead of the app's actual source. Reuses the same clone helper and
    token resolution as ``nuguard sbom generate --from-repo``.

    Returns the cloned directory path, or ``None`` on any failure (caller
    proceeds with ``source_path=None``, same as before this existed).
    """
    import shutil
    import tempfile

    from nuguard.cli.commands.sbom import _inject_token, _resolve_token  # noqa: PLC0415
    from nuguard.sbom.extractor import AiSbomExtractor  # noqa: PLC0415

    clone_dir = tempfile.mkdtemp(prefix="nuguard_analyze_clone_")
    try:
        token = _resolve_token(None)
        clone_url = _inject_token(url, token) if token else url
        typer.echo(
            f"Cloning {url} for local-file scans (supply-chain/Checkov/Trivy/Semgrep)…"
        )
        repo_dir = Path(clone_dir) / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        AiSbomExtractor._clone_repo(url=clone_url, ref="main", dest=repo_dir)
        return str(repo_dir)
    except Exception as exc:
        _log.warning("_clone_remote_source_for_analysis: clone of %s failed: %s", url, exc)
        typer.echo(
            f"warning: could not clone '{url}' for local-file scans — "
            f"supply-chain/Checkov/Trivy/Semgrep will run without local source ({exc})",
            err=True,
        )
        shutil.rmtree(clone_dir, ignore_errors=True)
        return None


_SEV_EMOJI: dict[str, str] = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "🟢",
    "info":     "ℹ️",
}


@analyze_app.callback(invoke_without_command=True)
def analyze(
    ctx: typer.Context,
    sbom: Optional[str] = typer.Option(None, "--sbom", help="Path to AI-SBOM JSON file. Falls back to 'sbom:' in nuguard.yaml when --config is set."),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c",
        help="Path to nuguard.yaml config file. CLI flags override config values.",
    ),
    nga: bool = typer.Option(
        False, "--nga",
        help="Run NGA structural rules only (NGA-001–018); skip OSV, Grype, Checkov, Trivy, Semgrep, and ATLAS native checks.",
    ),
    format: list[str] | None = typer.Option(
        None,
        "--format",
        "-f",
        help=(
            "Output format(s): markdown | sarif | json. "
            "Repeat --format or pass comma-separated values."
        ),
    ),
    policy: str = typer.Option(
        None, "--policy",
        help="Path to Cognitive Policy Markdown file (policy check not yet implemented).",
    ),
    min_severity: Optional[str] = typer.Option(
        None, "--min-severity",
        help="Minimum severity to report: critical | high | medium | low | info. [default: medium]",
    ),
    atlas: bool = typer.Option(True, "--atlas/--no-atlas", help="Run MITRE ATLAS native graph checks."),  # noqa: E501
    osv: bool = typer.Option(True, "--osv/--no-osv", help="Run OSV dependency CVE scan."),
    grype: bool = typer.Option(True, "--grype/--no-grype",
                               help="Run Grype CVE scan (requires grype on PATH)."),
    grype_timeout: Optional[float] = typer.Option(
        None, "--grype-timeout",
        help="Per-invocation timeout for grype in seconds. [default: 180]",
    ),
    grype_retries: Optional[int] = typer.Option(
        None, "--grype-retries",
        help="Number of retry attempts when grype times out. [default: 3]",
    ),
    checkov: bool = typer.Option(True, "--checkov/--no-checkov",
                                 help="Run Checkov IaC scan (requires checkov on PATH)."),
    trivy: bool = typer.Option(True, "--trivy/--no-trivy",
                               help="Run Trivy container/fs scan (requires trivy on PATH)."),
    semgrep: bool = typer.Option(True, "--semgrep/--no-semgrep",
                                 help="Run Semgrep AI-security rules (requires semgrep on PATH)."),
    source: Optional[str] = typer.Option(
        None, "--source", "-s",
        help="Path to app source directory for supply-chain, Checkov, Trivy, and Semgrep scans. Falls back to 'source:' in nuguard.yaml.",
    ),
    supply_chain: bool = typer.Option(True, "--supply-chain/--no-supply-chain",
                                      help="Run supply-chain threat pack (NGA-SC-001–025)."),
    supply_chain_profile: Optional[str] = typer.Option(
        None, "--supply-chain-profile",
        help="Supply-chain scan profile: ci | standard | full. [default: standard]",
    ),
    supply_chain_verify: Optional[str] = typer.Option(
        None, "--supply-chain-verify",
        help="Artifact registry verification: off | warn | fail. [default: off]",
    ),
    llm: bool = typer.Option(False, "--llm", help="Enable LLM enrichment in ATLAS pass."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Show all 21 NGA rules (pass and fail) with evidence on why each passed.",
    ),
    output: str = typer.Option(
        None, "--output", "-o",
        help="Write report to this file instead of stdout.",
    ),
) -> None:
    """Run static analysis against the AI-SBOM.

    Scans the SBOM for structural security issues using NGA rules (NGA-001 to
    NGA-018), checks dependencies against the OSV CVE database, optionally runs
    Grype for container/package CVEs, and annotates findings with MITRE ATLAS v2
    technique mappings.

    Use ``--config nuguard.yaml`` to load ``analyze.min_severity`` and
    ``analyze.nga_only`` from the project config file. CLI flags always
    override config values.

    Use ``--nga`` to run only NGA structural rules (fastest mode, no external
    tools required). Equivalent to setting ``analyze.nga_only: true`` in
    nuguard.yaml.
    """
    if ctx.invoked_subcommand is not None:
        return

    # ------------------------------------------------------------------
    # Load config and resolve effective flag values
    # ------------------------------------------------------------------
    from nuguard.config import load_config  # noqa: PLC0415
    try:
        cfg = load_config(config)
    except Exception as exc:
        typer.echo(f"error: failed to load config: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    # --nga: CLI flag wins; fall back to config field
    nga = nga or cfg.analyze_nga_only

    # --min-severity: CLI wins when explicitly set (non-None); else use config default
    min_severity = min_severity or cfg.analyze_min_severity

    # --source: CLI wins; fall back to top-level source: in nuguard.yaml.
    # A remote GitHub URL in `source:` can't be passed to local-scan tools
    # (supply-chain, Semgrep, Checkov) as-is — it's cloned to a temp dir below,
    # just before running analysis, so those tools see real files instead of
    # silently scanning nothing (see `_remote_source_url` handling further down).
    _cfg_source = cfg.source_path
    _remote_source_url: str | None = None
    if not source and _cfg_source:
        if _cfg_source.startswith(("http://", "https://", "git://", "git+")):
            _remote_source_url = _cfg_source
        else:
            source = _cfg_source

    # Local source root for manifest line-number lookups in remediation text
    # (best-effort; None for remote URLs, since the clone dir is removed
    # before rendering — see the `finally` block below).
    _manifest_source_root: Path | None = None
    if source and not source.startswith(("http://", "https://", "git://", "git+")):
        _candidate = Path(source)
        if _candidate.is_dir():
            _manifest_source_root = _candidate

    # supply-chain: CLI wins; fall back to analyze: section in nuguard.yaml
    sc_profile = supply_chain_profile or cfg.analyze_supply_chain_profile
    sc_verify = supply_chain_verify or cfg.analyze_supply_chain_verify

    # NGA-only mode: disable all external scans
    if nga:
        osv = grype = checkov = trivy = semgrep = atlas = False

    # ------------------------------------------------------------------
    # Load SBOM
    # ------------------------------------------------------------------
    sbom = sbom or cfg.sbom_path
    if not sbom:
        typer.echo("error: --sbom is required (or set 'sbom:' in nuguard.yaml via --config)", err=True)
        raise typer.Exit(code=2)

    sbom_path = Path(sbom)
    if not sbom_path.exists():
        typer.echo(f"error: SBOM file not found: {sbom_path}", err=True)
        raise typer.Exit(code=2)

    try:
        sbom_data = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        typer.echo(f"error: failed to read SBOM: {exc}", err=True)
        raise typer.Exit(code=2)

    try:
        from nuguard.sbom.models import AiSbomDocument  # noqa: PLC0415
        doc = AiSbomDocument.model_validate(sbom_data)
    except Exception as exc:
        typer.echo(f"error: SBOM validation failed: {exc}", err=True)
        raise typer.Exit(code=2)

    # Re-run topology enrichment in case the SBOM file predates it (idempotent).
    from nuguard.sbom.enricher import enrich as _enrich_topology  # noqa: PLC0415
    _enrich_topology(doc)

    # ------------------------------------------------------------------
    # Run analysis
    # ------------------------------------------------------------------
    min_sev_str = min_severity.lower()
    if min_sev_str not in _SEV_ORDER:
        typer.echo(f"error: unknown --min-severity '{min_severity}'", err=True)
        raise typer.Exit(code=2)
    min_sev = Severity(min_sev_str) if min_sev_str != "info" else Severity.INFO

    try:
        formats = parse_output_formats(
            format,
            default_format="markdown",
            allowed_formats={"markdown", "json", "sarif"},
        )
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2)

    if len(formats) > 1 and not output:
        typer.echo(
            "error: --output is required when multiple --format values are requested",
            err=True,
        )
        raise typer.Exit(code=2)

    atlas_config: dict[str, Any] = {}
    if llm:
        atlas_config["llm"] = True
    if "markdown" in formats:
        atlas_config["format"] = "markdown"

    # LLM client for remediation-plan synthesis (best-effort; deterministic
    # templates are used when no client is available). Uses the shared
    # redteam.llm -> redteam.eval_llm -> llm fallback chain so remediation
    # text is authored by the highest-capability model configured.
    from nuguard.remediation.llm import resolve_remediation_llm_client  # noqa: PLC0415

    llm_client = resolve_remediation_llm_client(cfg)

    # Auto-clone a remote source: URL to a temp dir so supply-chain/Checkov/
    # Trivy/Semgrep get real local files instead of silently scanning nothing
    # (see _clone_remote_source_for_analysis docstring). Cleaned up in the
    # `finally` below regardless of how the analysis attempt below finishes.
    _clone_dir: str | None = None
    if _remote_source_url:
        _clone_dir = _clone_remote_source_for_analysis(_remote_source_url)
        if _clone_dir:
            source = _clone_dir

    try:
        from nuguard.analysis.public_api import AnalysisRunRequest, run_analysis  # noqa: PLC0415
        source_path = Path(source) if source else None
        request = AnalysisRunRequest(
            enable_atlas=atlas,
            enable_osv=osv,
            enable_grype=grype,
            enable_checkov=checkov,
            enable_trivy=trivy,
            enable_semgrep=semgrep,
            enable_supply_chain=supply_chain,
            supply_chain_profile=sc_profile,
            supply_chain_verify_artifacts=sc_verify,
            source_path=str(source_path) if source_path else None,
            atlas_config=atlas_config,
            min_severity=min_sev,
            verbose=verbose,
            grype_timeout=grype_timeout if grype_timeout is not None else 180.0,
            grype_retries=grype_retries if grype_retries is not None else 3,
        )
        result = asyncio.run(run_analysis(request, sbom=doc, llm_client=llm_client))
        findings = result.findings
    except Exception as exc:
        typer.echo(f"error: analysis failed: {exc}", err=True)
        _log.exception("analysis failed")
        raise typer.Exit(code=2)
    finally:
        if _clone_dir:
            import shutil  # noqa: PLC0415
            # _clone_dir is "<mkdtemp_root>/repo" — remove the mkdtemp root,
            # not just the repo subdirectory, so nothing is left behind.
            shutil.rmtree(Path(_clone_dir).parent, ignore_errors=True)

    # ------------------------------------------------------------------
    # Filter to requested minimum severity
    # ------------------------------------------------------------------
    min_rank = _SEV_ORDER.get(min_sev_str, 4)
    visible = [
        f for f in findings
        if _SEV_ORDER.get(f.severity.value, 99) <= min_rank
    ]

    # ------------------------------------------------------------------
    # Render output
    # ------------------------------------------------------------------
    def _render(fmt: str) -> str:
        if fmt == "json":
            return _render_json(
                visible,
                sbom_path,
                tool_status,
                nga_audit,
                sc_audit,
                token_usage=token_usage,
                remediation_plan=remediation_plan,
            )
        if fmt == "sarif":
            return _render_sarif(visible, sbom_path, tool_status)
        return _render_markdown(
            visible,
            sbom_path,
            min_severity,
            tool_status,
            nga_audit,
            sc_audit,
            sbom_deps=doc.deps,
            source_root=_manifest_source_root,
        )

    extension_map = {
        "markdown": ".md",
        "json": ".json",
        "sarif": ".sarif",
    }
    tool_status = result.tool_status
    nga_audit = result.nga_audit
    sc_audit = result.sc_audit
    token_usage = result.token_usage
    remediation_plan = result.remediation_plan

    if output:
        out_base = Path(output)
        for fmt in formats:
            out_path = output_path_for_format(
                out_base,
                fmt=fmt,
                all_formats=formats,
                extension_map=extension_map,
            )
            report_text = _render(fmt)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(report_text, encoding="utf-8")
            typer.echo(f"report written to {out_path}")
    else:
        typer.echo(_render(formats[0]))

    # Exit 1 if any findings at or above threshold
    raise typer.Exit(code=1 if visible else 0)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


# PURL types for OS/distro packages, where the namespace segment is the distro
# name (e.g. "alpine", "debian") rather than part of the package identity.
# Grype/Syft emit these with a namespace + qualifiers (?arch=...&distro=...);
# trivy emits a bare "name@version" for the same package. Stripping the
# namespace and qualifiers here lets both forms normalise to the same label.
_OS_PURL_TYPES = {"apk", "deb", "rpm", "alpm"}


def _component_label(f: Finding) -> str:
    """Return a normalised display label for a finding's component.

    PURLs (pkg:npm/next@15.2.4) are simplified to ``name@version`` so that
    findings from different scanners for the same package are grouped together.
    OS-package PURLs (pkg:apk/alpine/curl@8.5.0-r0?arch=...&distro=...) have
    their distro namespace and qualifiers stripped so they match the bare
    "name@version" label trivy produces for the same package.
    """
    raw = f.affected_component or f.finding_id.rsplit("-", 1)[0]
    if raw.startswith("pkg:"):
        # pkg:<type>/<namespace>/<name>@<version>?<qualifiers>#<subpath>
        body = raw[len("pkg:"):].split("#", 1)[0].split("?", 1)[0]
        parts = body.split("/")
        pkg_type, rest = parts[0], parts[1:]
        if pkg_type in _OS_PURL_TYPES and len(rest) > 1:
            # drop the distro namespace segment, keep only name@version
            rest = rest[1:]
        raw = "/".join(rest)
    else:
        # non-PURL component labels (e.g. trivy's bare "name@version") may
        # still carry qualifiers if a source ever appends them
        raw = raw.split("?", 1)[0]
    return raw


def _source_tool(f: Finding) -> str:
    """Extract the originating scanner name from the finding_id prefix.

    finding_id format: ``<tool>-<rest>``  e.g. ``trivy-CVE-2025-...``,
    ``osv-GHSA-...``, ``nga-NGA-001-...``.
    """
    prefix = f.finding_id.split("-")[0].lower()
    # known prefixes
    if prefix in ("trivy", "osv", "grype", "nga", "checkov", "semgrep", "atlas"):
        return prefix
    return prefix


# Internal tool identifiers → user-facing display names for the report.
# NGA is nuguard's own deterministic rule engine, not a third-party scanner —
# "nga" reads as an unexplained acronym to end users.
_TOOL_DISPLAY_NAMES = {
    "nga": "NuGuard Best Practices",
    "nga-rules": "NuGuard Best Practices",
}


def _display_tool_name(tool: str) -> str:
    return _TOOL_DISPLAY_NAMES.get(tool.lower(), tool)


_HEX8_SUFFIX_RE = re.compile(r"-[0-9a-f]{8}$", re.IGNORECASE)


def _display_rule_id(f: Finding) -> str:
    """Human-friendly rule identifier for structural findings.

    ``finding_id`` is internal — ``"<tool>-<rule_id>-<8-hex-dedup-suffix>"``
    (e.g. ``nga-NGA-006-aacb1fe0``) — where the dedup suffix exists only to
    keep ``finding_id`` unique across repeated runs and the tool prefix
    duplicates information already carried by the rule_id itself (``NGA-006``
    already says "NGA rule"). Strip both so the report shows just ``NGA-006``.
    """
    raw = f.finding_id
    source = _source_tool(f)
    if source and raw.lower().startswith(source + "-"):
        raw = raw[len(source) + 1:]
    raw = _HEX8_SUFFIX_RE.sub("", raw)
    return raw or f.finding_id


_CVE_RE = re.compile(r'\bCVE-\d{4}-\d+\b', re.IGNORECASE)
_GHSA_RE = re.compile(r'\bGHSA-[a-z0-9]+-[a-z0-9]+-[a-z0-9]+\b', re.IGNORECASE)


def _canonical_vuln_id(f: Finding) -> str | None:
    """Extract a canonical CVE or GHSA identifier for cross-tool deduplication.

    Returns the CVE-XXXX-XXXXX id when available (preferred), then GHSA-..., then
    None for structural findings (NGA, ATLAS, semgrep, checkov) that are not
    package vulnerabilities and should never be merged.
    """
    if _source_tool(f) in ("nga", "atlas", "semgrep", "checkov"):
        return None
    # CVE embedded in finding_id (trivy, grype)
    m = _CVE_RE.search(f.finding_id)
    if m:
        return m.group().upper()
    # CVE aliased in title (OSV sometimes adds "[CVE-XXXX-XXXXX]")
    m = _CVE_RE.search(f.title or "")
    if m:
        return m.group().upper()
    # CVE mentioned in description
    m = _CVE_RE.search(f.description or "")
    if m:
        return m.group().upper()
    # GHSA in finding_id (OSV primary identifier)
    m = _GHSA_RE.search(f.finding_id)
    if m:
        return m.group().upper()
    return None


def _dedup_component_findings(
    flist: list[Finding],
) -> list[tuple[Finding, list[str]]]:
    """Deduplicate findings within a component by CVE/GHSA identity.

    Returns a list of (canonical_finding, [source_tool, ...]) tuples.
    When multiple tools report the same CVE:
      - OSV is preferred as canonical (for its osv.dev link and remediation text)
      - All originating tool names are collected for display
    Non-CVE findings (NGA structural rules, ATLAS, …) pass through unchanged.
    """
    groups: dict[str, list[Finding]] = {}
    no_key: list[Finding] = []
    for f in flist:
        key = _canonical_vuln_id(f)
        if key is None:
            no_key.append(f)
        else:
            groups.setdefault(key, []).append(f)

    result: list[tuple[Finding, list[str]]] = []
    for _key, group in groups.items():
        sources = sorted({_source_tool(f) for f in group})
        # Prefer OSV as canonical — it carries osv.dev links and remediation guidance
        osv_findings = [f for f in group if _source_tool(f) == "osv"]
        canonical = osv_findings[0] if osv_findings else group[0]
        # Severity should reflect the worst-case rating across all sources
        # reporting this CVE, not whichever source happened to be canonical —
        # scanners can disagree on severity for the same vulnerability.
        worst = min(group, key=lambda f: _SEV_ORDER.get(f.severity.value, 99))
        if worst.severity != canonical.severity:
            canonical = canonical.model_copy(update={"severity": worst.severity})
        result.append((canonical, sources))

    for f in no_key:
        result.append((f, [_source_tool(f)]))

    result.sort(key=lambda x: _SEV_ORDER.get(x[0].severity.value, 99))
    return result


_FIX_VERSION_RE = re.compile(r"fix available:\s*([^)]+)\)")


def _fixed_version_for_finding(f: Finding) -> str | None:
    """Extract the scanner-reported fixed version from a finding's remediation text."""
    if not f.remediation:
        return None
    m = _FIX_VERSION_RE.search(f.remediation)
    return m.group(1).strip() if m else None


def _numeric_version_key(v: str) -> tuple[int, ...] | None:
    m = re.match(r"^(\d+(?:\.\d+)*)", v)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _best_fix_version(candidates: list[str]) -> tuple[str, list[str]]:
    """Pick the highest-looking fixed version from a set of candidates.

    Returns ``(recommended, all_unique_candidates)``. Falls back to a
    lexicographic pick when versions can't be parsed numerically (e.g. mixed
    distro/semver formats) rather than guessing wrong silently.
    """
    uniq = list(dict.fromkeys(candidates))
    if len(uniq) == 1:
        return uniq[0], uniq
    parseable = [(v, k) for v in uniq if (k := _numeric_version_key(v)) is not None]
    best = max(parseable, key=lambda vk: vk[1])[0] if parseable else sorted(uniq)[-1]
    return best, uniq


def _manifest_line_number(source_root: Path | None, source_file: str, dep_name: str) -> int | None:
    """Best-effort line number of ``dep_name``'s declaration in a manifest file."""
    if not source_root or not source_file:
        return None
    try:
        text = (source_root / source_file).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    pattern = re.compile(re.escape(dep_name), re.IGNORECASE)
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return i
    return None


def _component_remediation_text(
    components: list[str],
    findings: list[Finding],
    deps_by_name: dict[str, Any],
    source_root: Path | None,
) -> str:
    """Build one actionable remediation sentence for a (possibly merged) group
    of sibling components sharing the same finding set.

    Points at the exact manifest file (+ line, when it can be found on disk)
    and the version bump needed for language-ecosystem dependencies; falls
    back to image-rebuild guidance for OS packages that aren't declared in
    any project manifest (grype/trivy scan the container image directly).
    """
    names = [c.rpartition("@")[0] or c for c in components]
    names_str = ", ".join(f"`{n}`" for n in names)
    fix_candidates = [v for v in (_fixed_version_for_finding(f) for f in findings) if v]

    if not fix_candidates:
        return (
            f"No fixed version has been published yet for {names_str} — "
            f"track the upstream advisory and re-scan once a patch ships."
        )

    best, all_candidates = _best_fix_version(fix_candidates)
    candidates_note = (
        f" (advisory fix versions seen: {', '.join(all_candidates)})"
        if len(all_candidates) > 1 else ""
    )

    dep = next((deps_by_name.get(n.lower()) for n in names if deps_by_name.get(n.lower())), None)
    if dep is not None:
        line_no = _manifest_line_number(source_root, dep.source_file, dep.name)
        loc = f"`{dep.source_file}`" + (f" (line {line_no})" if line_no else "")
        current = dep.version_spec or "?"
        return (
            f"In {loc}, bump {names_str} from `{current}` to `>={best}`."
            f"{candidates_note}"
        )

    is_are, pkg_noun = ("is", "an OS-level package") if len(names) == 1 else ("are", "OS-level packages")
    upgrade_cmd = " ".join(names)
    return (
        f"{names_str} {is_are} {pkg_noun} baked into the container "
        f"image (not declared in a project manifest) — rebuild the image after "
        f"upgrading to `{best}` or newer, e.g. `apk upgrade {upgrade_cmd}` / "
        f"`apt-get install --only-upgrade {upgrade_cmd}` in the Dockerfile, or bump "
        f"the base image tag once a patched image is published.{candidates_note}"
    )


def _framework_mapping_text(
    owasp_llm: str | None, owasp_asi: str | None, atlas: str | None
) -> str:
    """Combine OWASP LLM/Agentic Top 10 and MITRE ATLAS refs into one line."""
    parts = []
    if owasp_llm:
        parts.append(f"OWASP LLM Top 10: {owasp_llm}")
    if owasp_asi:
        parts.append(f"OWASP Agentic Top 10: {owasp_asi}")
    if atlas:
        parts.append(f"MITRE ATLAS: {atlas}")
    return " · ".join(parts)


def _group_by_component(findings: list[Finding]) -> list[tuple[str, list[Finding]]]:
    """Group findings by component.

    Components are ordered by highest severity first (critical → high → …),
    then by finding count (desc), then alphabetically.
    Within each component findings are sorted by severity (critical first).
    """
    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        key = _component_label(f)
        grouped.setdefault(key, []).append(f)

    # Sort each component's findings by severity
    for flist in grouped.values():
        flist.sort(key=lambda x: _SEV_ORDER.get(x.severity.value, 99))

    # Sort components: highest severity first, then most findings, then alphabetically
    return sorted(
        grouped.items(),
        key=lambda kv: (_SEV_ORDER.get(kv[1][0].severity.value, 99), -len(kv[1]), kv[0]),
    )


class _ComponentGroup(NamedTuple):
    """A final report section — one or more sibling component labels (e.g.
    ``curl@8.5.0-r0`` and ``libcurl@8.5.0-r0``, binaries from the same source
    package) whose CVE sets fully/subset-overlap, merged into one group so the
    same vulnerability isn't reported twice under different component names.
    """
    components: list[str]                          # merged labels, primary (first-seen) first
    entries: list[tuple[Finding, list[str]]]        # (finding, sources) rows


def _merge_overlapping_components(
    grouped_deduped: list[tuple[str, list[tuple[Finding, list[str]]]]],
) -> list[_ComponentGroup]:
    """Final cross-component dedup pass.

    ``grouped_deduped`` is already per-component, per-CVE deduped and sorted
    high-severity-first. Sibling components (different binaries built from the
    same upstream source, e.g. curl/libcurl or libssl3/libcrypto3) often carry
    an identical or subset CVE set — fold those into the earlier (higher- or
    equal-priority) group instead of listing them as separate sections. A
    component is only ever folded into a group processed *before* it, so the
    result needs no re-sort: it's already high-severity-group-first, and a
    group whose findings are entirely absorbed elsewhere simply never appears.
    """
    groups: list[_ComponentGroup] = []
    for comp, entries in grouped_deduped:
        vuln_ids = {vid for f, _ in entries if (vid := _canonical_vuln_id(f)) is not None}

        target: _ComponentGroup | None = None
        if vuln_ids:
            for g in groups:
                g_vuln_ids = {vid for f, _ in g.entries if (vid := _canonical_vuln_id(f)) is not None}
                if vuln_ids <= g_vuln_ids:
                    target = g
                    break

        if target is None:
            groups.append(_ComponentGroup(components=[comp], entries=list(entries)))
            continue

        target.components.append(comp)
        by_vuln_id = {
            vid: i for i, (f, _) in enumerate(target.entries)
            if (vid := _canonical_vuln_id(f)) is not None
        }
        appended = False
        for f, sources in entries:
            vid = _canonical_vuln_id(f)
            if vid is not None and vid in by_vuln_id:
                # Same CVE already present in the target group — merge sources only.
                i = by_vuln_id[vid]
                ef, esources = target.entries[i]
                target.entries[i] = (ef, sorted(set(esources) | set(sources)))
            else:
                # Structural finding tied to this specific component (or a CVE
                # somehow missing from the target, shouldn't happen given the
                # subset check above) — keep as its own row.
                target.entries.append((f, sources))
                appended = True
        if appended:
            target.entries.sort(key=lambda x: _SEV_ORDER.get(x[0].severity.value, 99))

    return groups


def _render_rule_audit_section(
    audit: list[dict[str, Any]],
    section_title: str,
    description: str,
) -> list[str]:
    """Render a pass/fail rule audit table + per-passing-rule detail block."""
    _AUDIT_SEV_EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}
    _AUDIT_STATUS_ICON = {"PASS": "✅", "FAIL": "❌", "ERROR": "⚠️", "SKIPPED": "⏭️"}
    lines: list[str] = [
        f"## {section_title}", "",
        description,
        "",
        "| Rule | Severity | Status | Evidence |",
        "|------|----------|--------|----------|",
    ]
    for entry in audit:
        rid = entry.get("rule_id", "")
        title = entry.get("title", "")
        sev = entry.get("severity", "")
        status = entry.get("status", "")
        sev_em = _AUDIT_SEV_EMOJI.get(sev, "")
        st_icon = _AUDIT_STATUS_ICON.get(status, "❓")
        if status == "FAIL":
            count = entry.get("finding_count", 0)
            affected = entry.get("affected", [])
            detail = f"{count} finding(s)"
            if affected:
                detail += f" — `{'`, `'.join(str(a) for a in affected[:3])}`"
                if len(affected) > 3:
                    detail += f" +{len(affected) - 3} more"
        elif status == "SKIPPED":
            detail = entry.get("pass_reason", "not in profile")
        else:
            detail = entry.get("pass_reason", "")
        lines.append(
            f"| **{rid}** {title} | {sev_em} {sev} | {st_icon} {status} | {detail} |"
        )
    lines.append("")
    # Per-rule detail for passing rules
    pass_rules = [e for e in audit if e.get("status") == "PASS"]
    if pass_rules:
        lines += ["### Passing Rule Details", ""]
        for entry in pass_rules:
            rid = entry.get("rule_id", "")
            title = entry.get("title", "")
            checks = entry.get("checks", "")
            pass_reason = entry.get("pass_reason", "")
            lines += [
                f"**{rid} — {title}**  ",
                f"- Examined: {checks}  ",
                f"- Result: {pass_reason}  ",
            ]
            evidence: dict[str, Any] = entry.get("pass_evidence") or {}
            if evidence:
                lines.append("- Evidence:  ")
                for key, val in evidence.items():
                    label = key.replace("_", " ")
                    if isinstance(val, list):
                        val_str = ", ".join(f"`{x}`" for x in val) if val else "none"
                    elif isinstance(val, bool):
                        val_str = str(val).lower()
                    else:
                        val_str = str(val)
                    lines.append(f"  - {label}: {val_str}  ")
            lines.append("")
    return lines


def _render_markdown(
    findings: list[Finding],
    sbom_path: Path,
    min_severity: str,
    tool_status: dict[str, Any] | None = None,
    nga_audit: list[dict[str, Any]] | None = None,
    sc_audit: list[dict[str, Any]] | None = None,
    sbom_deps: list[Any] | None = None,
    source_root: Path | None = None,
) -> str:
    # Pre-compute the deduplicated, component-grouped view used throughout the
    # report: per-component CVE dedup, then a final cross-component pass that
    # folds sibling components (curl/libcurl, libssl3/libcrypto3, ...) whose
    # CVE sets subset/fully overlap into one group instead of listing the same
    # vulnerability twice under different component names.
    deps_by_name: dict[str, Any] = {dep.name.lower(): dep for dep in (sbom_deps or [])}
    grouped_raw = _group_by_component(findings)
    grouped_deduped = [
        (comp, _dedup_component_findings(flist))
        for comp, flist in grouped_raw
    ]
    component_groups = _merge_overlapping_components(grouped_deduped)
    # Flat list of canonical findings (one per unique finding, post cross-component merge)
    deduped_all: list[Finding] = [f for g in component_groups for f, _ in g.entries]

    lines: list[str] = [
        "# NuGuard Static Analysis Report",
        "",
        f"**SBOM:** `{sbom_path}`  ",
        f"**Minimum severity:** {min_severity}  ",
        f"**Total findings:** {len(deduped_all)} unique",
        f"*(from {len(findings)} raw tool findings — duplicates across scanners and sibling components merged)*  ",
        "",
    ]

    # ------------------------------------------------------------------
    # Severity summary (based on deduplicated findings)
    # ------------------------------------------------------------------
    if deduped_all:
        # Map each finding back to its group's primary component label, so the
        # per-severity component count reflects post-merge groups, not raw labels.
        primary_component_of: dict[str, str] = {
            f.finding_id: g.components[0] for g in component_groups for f, _ in g.entries
        }

        by_sev: dict[str, list[Finding]] = {}
        for f in deduped_all:
            by_sev.setdefault(f.severity.value, []).append(f)

        summary_parts: list[str] = []
        for sev in ("critical", "high", "medium", "low", "info"):
            grp = by_sev.get(sev, [])
            if grp:
                emoji = _SEV_EMOJI.get(sev, "")
                comps = len({primary_component_of[f.finding_id] for f in grp})
                summary_parts.append(
                    f"{emoji} **{sev.upper()}:** {len(grp)} finding(s) across {comps} component(s)"
                )
        lines += ["## Summary", ""] + [f"- {p}" for p in summary_parts] + [""]

        # Top component groups by unique finding count
        top_n = component_groups[:5]
        lines += ["### Components with Most Findings", ""]
        lines += ["| Component | Unique Findings | Highest Severity |",
                  "|-----------|-----------------|-----------------|"]
        for g in top_n:
            top_sev = g.entries[0][0].severity.value  # sorted by severity
            emoji = _SEV_EMOJI.get(top_sev, "")
            lines.append(f"| `{g.components[0]}` | {len(g.entries)} | {emoji} {top_sev.upper()} |")
        lines.append("")

    # ------------------------------------------------------------------
    # Tool coverage table
    # ------------------------------------------------------------------
    if tool_status:
        _STATUS_ICON = {"ok": "✅", "skipped": "⏭️", "disabled": "🔕", "error": "❌"}
        lines += [
            "## Tool Coverage", "",
            "| Tool | Status | Findings |",
            "|------|--------|----------|",
        ]
        for tool, info in tool_status.items():
            st = info.get("status", "?")
            icon = _STATUS_ICON.get(st, "❓")
            count = info.get("findings", "—")
            reason = info.get("reason", "")
            note = f" ({reason})" if reason and st in ("skipped", "error") else ""
            lines.append(f"| {_display_tool_name(tool)} | {icon} {st}{note} | {count} |")
        lines.append("")

    if not deduped_all:
        lines += ["_No findings at or above the requested severity threshold._", ""]
    else:
        # ------------------------------------------------------------------
        # Findings grouped by component, highest-severity group first. Every
        # finding — a CVE/GHSA library group or an individual structural
        # finding — follows the same field order: Summary, Remediation,
        # Affected Components, Source, Framework Mapping.
        # ------------------------------------------------------------------
        lines += ["## Findings", ""]

        for g in component_groups:
            top_sev = g.entries[0][0].severity.value
            emoji = _SEV_EMOJI.get(top_sev, "")
            lines += [f"### {emoji} `{g.components[0]}` ({len(g.entries)} finding(s))", ""]

            dep_entries = [(f, s) for f, s in g.entries if _canonical_vuln_id(f) is not None]
            struct_entries = [(f, s) for f, s in g.entries if _canonical_vuln_id(f) is None]

            # CVE/GHSA dependency findings stay grouped per library: one common
            # 5-field block for the whole group, then a compact table listing
            # every CVE it resolves.
            if dep_entries:
                n = len(dep_entries)
                names = [c.rpartition("@")[0] or c for c in g.components]
                summary = f"{n} known vulnerabilit{'y' if n == 1 else 'ies'} in {', '.join(names)}."
                remediation = _component_remediation_text(
                    g.components, [f for f, _ in dep_entries], deps_by_name, source_root
                )
                affected = ", ".join(f"`{c}`" for c in g.components)
                all_sources = sorted({s for _, sources in dep_entries for s in sources})
                sources_str = ", ".join(_display_tool_name(s) for s in all_sources)
                supply_chain = owasp_refs_for_rule("NGA-008")
                framework = _framework_mapping_text(
                    ", ".join(supply_chain.owasp_llm),
                    ", ".join(supply_chain.owasp_agentic),
                    None,
                )

                lines += [f"**Summary:** {summary}  ", ""]
                lines += [f"**Remediation:** {remediation}  ", ""]
                lines += [f"**Affected Components:** {affected}  ", ""]
                lines += [f"**Source:** {sources_str}  ", ""]
                if framework:
                    lines += [f"**Framework Mapping:** {framework}  ", ""]

                lines += [
                    "| Severity | ID | Title | Sources |",
                    "|----------|----|-------|---------|",
                ]
                for f, sources in dep_entries:
                    sev_emoji = _SEV_EMOJI.get(f.severity.value, "")
                    sev_label = f.severity.value.upper()
                    vuln_id = _canonical_vuln_id(f)  # not None by construction
                    safe_title = f.title.replace("|", "\\|")
                    row_sources = ", ".join(_display_tool_name(s) for s in sources)
                    lines.append(
                        f"| {sev_emoji} {sev_label} | `{vuln_id}` | {safe_title} | {row_sources} |"
                    )
                lines.append("")

            # Structural findings (NGA, ATLAS) → one block per finding, same field order.
            for f, sources in struct_entries:
                sev_emoji = _SEV_EMOJI.get(f.severity.value, "")
                sources_str = ", ".join(_display_tool_name(s) for s in sources)
                lines += [f"#### {sev_emoji} {_display_rule_id(f)} — {f.title}", ""]
                lines += [f"**Summary:** {f.description or f.title}  ", ""]
                if f.remediation:
                    lines += [f"**Remediation:** {f.remediation}  ", ""]
                if f.affected_component:
                    lines += [f"**Affected Components:** `{_component_label(f)}`  ", ""]
                lines += [f"**Source:** {sources_str}  ", ""]
                framework = _framework_mapping_text(
                    f.owasp_llm_ref, f.owasp_asi_ref, f.mitre_atlas_technique
                )
                if framework:
                    lines += [f"**Framework Mapping:** {framework}  ", ""]
                if f.references:
                    lines += ["**References:**  ", ""]
                    for ref in f.references:
                        lines.append(f"- {ref}")
                    lines.append("")

    # ------------------------------------------------------------------
    # NGA Rule Audit (verbose mode only)
    # ------------------------------------------------------------------
    if nga_audit:
        lines += _render_rule_audit_section(
            nga_audit,
            "NGA Rule Audit",
            "All 26 NGA structural rules — pass/fail status with evidence.",
        )

    # ------------------------------------------------------------------
    # Supply Chain Rule Audit
    # ------------------------------------------------------------------
    if sc_audit:
        lines += _render_rule_audit_section(
            sc_audit,
            "Supply Chain Rule Audit",
            "All 25 NGA-SC supply-chain rules — pass/fail/skipped status with evidence.",
        )

    return "\n".join(lines)


def _render_json(
    findings: list[Finding],
    sbom_path: Path,
    tool_status: dict[str, Any] | None = None,
    nga_audit: list[dict[str, Any]] | None = None,
    sc_audit: list[dict[str, Any]] | None = None,
    token_usage: "Any | None" = None,
    remediation_plan: "list[Any] | None" = None,
) -> str:
    # Severity counts
    sev_counts: dict[str, int] = {}
    for f in findings:
        sev_counts[f.severity.value] = sev_counts.get(f.severity.value, 0) + 1

    # Findings grouped by component, sorted by severity within each group
    by_component: dict[str, list[dict[str, Any]]] = {}
    for comp, flist in _group_by_component(findings):
        by_component[comp] = [f.model_dump() for f in flist]

    data: dict[str, Any] = {
        "sbom": str(sbom_path),
        "total": len(findings),
        "severity_counts": sev_counts,
        "tool_status": tool_status or {},
        "token_usage": token_usage.model_dump() if token_usage is not None else {},
        "by_component": by_component,
        "findings": [f.model_dump() for f in findings],
        "remediation_plan": [a.model_dump() for a in (remediation_plan or [])],
        **({"nga_rule_audit": nga_audit} if nga_audit else {}),
        **({"sc_rule_audit": sc_audit} if sc_audit else {}),
    }
    return json.dumps(data, indent=2, default=str)


def _render_sarif(
    findings: list[Finding],
    sbom_path: Path,
    tool_status: dict[str, Any] | None = None,
) -> str:
    """SARIF 2.1.0 output with tool coverage in the run's properties."""
    _sev_to_sarif = {
        "critical": "error",
        "high":     "error",
        "medium":   "warning",
        "low":      "note",
        "info":     "none",
    }
    rules: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    seen_rules: set[str] = set()

    for f in findings:
        rule_id = f.finding_id.rsplit("-", 1)[0]  # strip uuid suffix
        if rule_id not in seen_rules:
            seen_rules.add(rule_id)
            rules.append({
                "id": rule_id,
                "name": f.title,
                "shortDescription": {"text": f.description or f.title},
                "helpUri": f.references[0] if f.references else "",
            })
        results.append({
            "ruleId": rule_id,
            "level": _sev_to_sarif.get(f.severity.value, "warning"),
            "message": {"text": f.description or f.title},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": str(sbom_path)},
                    }
                }
            ],
        })

    sarif: dict[str, Any] = {
        "version": "2.1.0",
        "$schema": "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0.json",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "nuguard",
                        "informationUri": "https://github.com/anthropics/nuguard",
                        "rules": rules,
                    }
                },
                "properties": {
                    "toolCoverage": tool_status or {},
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)
