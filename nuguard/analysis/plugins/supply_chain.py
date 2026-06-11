"""SupplyChainPlugin — supply-chain threat detection for AI SBOMs.

Wraps ``SupplyChainScanner`` (offline) and optionally ``ArtifactVerifier``
(network, opt-in) inside the standard ``AnalysisPlugin`` interface.

Config keys consumed from the config dict:
  source_path                    Required. Filesystem path to the source directory.
  supply_chain_profile           "ci" | "standard" | "full"  (default: "standard")
  supply_chain_verify_artifacts  "off" | "warn" | "fail"     (default: "off")
  supply_chain_threat_intel_feeds  list[str] | None          (default: all built-ins)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nuguard.analysis.models import AnalysisResult
from nuguard.analysis.plugin_base import AnalysisPlugin
from nuguard.common.logging import get_logger

_log = get_logger("analysis.plugins.supply_chain")


class SupplyChainPlugin(AnalysisPlugin):
    """Supply-chain threat scanner: lifecycle scripts, AI-agent configs, workflows."""

    name = "supply-chain"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> AnalysisResult:
        source_path_str = config.get("source_path") or ""

        # Reject remote URLs — they are never valid local scan targets.
        # The `source:` field in nuguard.yaml can hold a git clone URL used by
        # `sbom generate`; that URL must not be forwarded to local-scan tools.
        if source_path_str.startswith(("http://", "https://", "git://", "git+")):
            source_path_str = ""

        # Use the provided path when it exists; fall back to a non-existent sentinel
        # so the scanner still runs SBOM-native rules (SC-011..014 use pre-computed
        # booleans; SC-024 falls back to SBOM summary fields).
        source_path = Path(source_path_str) if source_path_str else Path("/dev/null")

        profile = str(config.get("supply_chain_profile") or "standard")
        feed_ids: list[str] | None = config.get("supply_chain_threat_intel_feeds")
        verify_mode = str(config.get("supply_chain_verify_artifacts") or "off")

        try:
            from nuguard.analysis.supply_chain_scanner import SupplyChainScanner  # noqa: PLC0415

            scanner = SupplyChainScanner(profile=profile, threat_intel_feeds=feed_ids)
            raw = scanner.scan(
                source_path,
                sbom_nodes=list(sbom.get("nodes") or []),
                sbom_deps=list(sbom.get("deps") or []),
                sbom_summary=dict(sbom.get("summary") or {}),
            )
        except Exception as exc:
            _log.warning("Supply-chain offline scan failed: %s", exc)
            return AnalysisResult(
                status="error",
                plugin=self.name,
                message=f"offline scan failed: {exc}",
                findings=[],
                details={},
            )

        # Phase 3: optional registry artifact verification (requires real source dir)
        if verify_mode != "off" and source_path.exists() and source_path.is_dir():
            raw.extend(self._run_artifact_verification(sbom, source_path, verify_mode))

        _log.info("supply-chain scan: %d finding(s) (profile=%s)", len(raw), profile)
        return AnalysisResult(
            status="ok",
            plugin=self.name,
            message=f"{len(raw)} finding(s)",
            findings=raw,
            details={
                "profile": profile,
                "verify_mode": verify_mode,
                "sc_audit": scanner.last_audit,
            },
        )

    def _run_artifact_verification(
        self,
        sbom: dict[str, Any],
        source_path: Path,
        verify_mode: str,
    ) -> list[dict[str, Any]]:
        try:
            import asyncio  # noqa: PLC0415

            from nuguard.analysis.artifact_verifier import ArtifactVerifier  # noqa: PLC0415

            verifier = ArtifactVerifier(mode=verify_mode)
            deps = list(sbom.get("deps") or [])

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                import concurrent.futures  # noqa: PLC0415
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(
                        asyncio.run,
                        verifier.verify_packages(deps, source_path),
                    ).result()
            else:
                return asyncio.run(verifier.verify_packages(deps, source_path))
        except Exception as exc:
            _log.warning("Artifact verification failed: %s", exc)
            return []
