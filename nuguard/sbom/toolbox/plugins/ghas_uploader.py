"""GitHub Advanced Security (GHAS) Code Scanning upload plugin.

Builds a SARIF 2.1.0 document from Xelo SBOM findings and uploads it to
GitHub Code Scanning via the REST API.  After upload, findings appear in
the repository's **Security → Code scanning** tab and are annotated on
pull requests and commits automatically by GitHub.

API reference:
  POST /repos/{owner}/{repo}/code-scanning/sarifs
  https://docs.github.com/en/rest/code-scanning/code-scanning#upload-an-analysis-as-sarif-data

Auth:
  A GitHub token with the ``security_events: write`` scope (classic token
  ``security_events``, or fine-grained token with that permission).
  For private repos the token must also have ``repo`` scope.

Config keys
-----------
  token           GitHub token (``ghp_…`` or fine-grained pat)    (required)
  github_repo     Repository slug  ``owner/repo``                  (required)
  ref             Git ref  e.g. ``refs/heads/main``                (required)
  commit_sha      40-character hexadecimal commit SHA              (required)
  github_api_url  Base URL for the API                             (default: https://api.github.com)
  provider        Vulnerability scan provider                      (default: xelo-rules)
  timeout         HTTP timeout in seconds                          (default: 15.0)
  retries         Number of retry attempts                         (default: 2)
"""
from __future__ import annotations

import base64
import gzip
import json
import logging
from typing import Any

from xelo.toolbox.http_utils import post_json
from xelo.toolbox.integration_contracts import GhasConfig
from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin

_log = logging.getLogger("toolbox.plugins.ghas")

_GITHUB_API_DEFAULT = "https://api.github.com"
_API_VERSION_HEADER = "2022-11-28"


class GhasUploaderPlugin(ToolPlugin):
    """Upload SBOM findings to GitHub Code Scanning as a SARIF report."""

    name = "ghas_upload"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        cfg = GhasConfig.model_validate(config)
        _log.info(
            "starting GHAS upload (repo=%s, ref=%s, sha=%s, provider=%s)",
            cfg.github_repo,
            cfg.ref,
            cfg.commit_sha[:8] + "...",
            config.get("provider", "xelo-rules"),
        )

        # ── Build SARIF from the vulnerability scanner ──────────────────
        provider = config.get("provider", "xelo-rules")
        try:
            sarif_doc = self._build_sarif(sbom, provider, config)
        except Exception as exc:
            _log.error("SARIF generation failed: %s", exc)
            raise RuntimeError(f"SARIF generation failed: {exc}") from exc

        finding_count = len(
            (sarif_doc.get("runs") or [{}])[0].get("results") or []
        )
        _log.info("SARIF built: %d finding(s)", finding_count)

        # ── Encode: gzip → base64 ───────────────────────────────────────
        sarif_bytes = json.dumps(sarif_doc, separators=(",", ":")).encode("utf-8")
        compressed  = gzip.compress(sarif_bytes)
        encoded     = base64.b64encode(compressed).decode("ascii")
        _log.debug(
            "SARIF encoded: raw=%d bytes, gzipped=%d bytes, b64=%d chars",
            len(sarif_bytes), len(compressed), len(encoded),
        )

        # ── POST to GitHub Code Scanning API ───────────────────────────
        api_base = str(cfg.github_api_url).rstrip("/")
        url = f"{api_base}/repos/{cfg.github_repo}/code-scanning/sarifs"
        headers = {
            "Authorization":       f"Bearer {cfg.token}",
            "X-GitHub-Api-Version": _API_VERSION_HEADER,
            "Accept":              "application/vnd.github+json",
        }
        payload: dict[str, Any] = {
            "commit_sha": cfg.commit_sha,
            "ref":        cfg.ref,
            "sarif":      encoded,
            "tool_name":  "xelo-toolbox",
        }

        _log.info("uploading SARIF to %s", url)
        try:
            response = post_json(
                url=url,
                payload=payload,
                headers=headers,
                timeout=cfg.timeout,
                retries=cfg.retries,
            )
        except RuntimeError as exc:
            _log.error("GitHub Code Scanning API error: %s", exc)
            raise RuntimeError(f"GitHub Code Scanning API error: {exc}") from exc

        analysis_url = response.get("url", "")
        analysis_id  = response.get("id", "")
        _log.info(
            "SARIF accepted by GitHub (id=%s, findings=%d)", analysis_id, finding_count
        )

        message = (
            f"Uploaded {finding_count} finding(s) to GitHub Code Scanning "
            f"for {cfg.github_repo}"
        )
        status = "warning" if finding_count > 0 else "ok"

        return ToolResult(
            status=status,
            tool=self.name,
            message=message,
            details={
                "repo":          cfg.github_repo,
                "ref":           cfg.ref,
                "commit_sha":    cfg.commit_sha,
                "finding_count": finding_count,
                "analysis_id":   analysis_id,
                "analysis_url":  analysis_url,
                "sarif_size_bytes": len(sarif_bytes),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_sarif(
        sbom: dict[str, Any],
        provider: str,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the SARIF exporter and return the raw SARIF dict."""
        from xelo.toolbox.plugins.sarif_exporter import SarifExporterPlugin  # lazy

        sarif_config: dict[str, Any] = {
            "provider":      provider,
            "timeout":       float(config.get("timeout", 15.0)),
            "grype_timeout": float(config.get("grype_timeout", 60.0)),
            "artifact_uri":  config.get("artifact_uri") or sbom.get("target") or "sbom.json",
        }
        result = SarifExporterPlugin().run(sbom, sarif_config)
        return dict(result.details)
