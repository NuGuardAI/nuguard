"""AWS Security Hub findings push plugin.

Translates Xelo SBOM vulnerability findings (XELO-xxx structural rules and
CVE advisories) into Amazon Security Finding Format (ASFF) and imports them
into AWS Security Hub via ``boto3``.

Findings are produced by the built-in VulnerabilityScannerPlugin using
the provider specified in config (default: ``xelo-rules`` — offline,
structural rules only).

Requires:
    pip install "xelo-toolbox[aws]"   # installs boto3

Credentials are resolved by the standard boto3 chain:
    1. Environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
    2. ~/.aws/credentials named profile (--profile)
    3. EC2 / ECS / Lambda instance role

Config keys
-----------
  region              AWS region                                  (required)
  aws_account_id      12-digit AWS account ID                     (required)
  product_arn_suffix  Product ARN identifier suffix               (default: xelo-toolbox)
  profile             AWS named credential profile                (optional)
  provider            Vulnerability scan provider                 (default: xelo-rules)
  timeout             Network timeout for OSV requests (seconds)  (default: 15.0)
  grype_timeout       Grype subprocess timeout (seconds)          (default: 60.0)
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, cast

from xelo.toolbox.integration_contracts import AwsSecurityHubConfig
from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin

_log = logging.getLogger("toolbox.plugins.aws_security_hub")

# ---------------------------------------------------------------------------
# boto3 — optional; imported at module level for easy test-patching
# ---------------------------------------------------------------------------
try:
    import boto3  # type: ignore[import-not-found]
except ImportError:
    boto3 = None  # noqa: N816

# ---------------------------------------------------------------------------
# ASFF constants
# ---------------------------------------------------------------------------
_SEVERITY_TO_ASFF: dict[str, str] = {
    "CRITICAL": "CRITICAL",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
    "INFO": "INFORMATIONAL",
}

_SEVERITY_TO_NUMERIC: dict[str, int] = {
    "CRITICAL": 90,
    "HIGH": 70,
    "MEDIUM": 40,
    "LOW": 10,
    "INFO": 0,
}

_VLA_ASFF_TYPE = (
    "Software and Configuration Checks"
    "/Industry and Regulatory Standards"
    "/AI-Supply-Chain"
)
_CVE_ASFF_TYPE = "Software and Configuration Checks/Vulnerabilities/CVE"

_BATCH_SIZE = 100  # AWS API limit per batch_import_findings call


class AwsSecurityHubPlugin(ToolPlugin):
    """Push SBOM findings into AWS Security Hub as ASFF records."""

    name = "securityhub_push"

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        if boto3 is None:
            raise ImportError(
                "boto3 is required for the AWS Security Hub plugin. "
                'Install it with: pip install "xelo-toolbox[aws]"'
            )

        cfg = AwsSecurityHubConfig.model_validate(config)
        _log.info(
            "starting Security Hub push (region=%s, account=%s, provider=%s, profile=%s)",
            cfg.region,
            cfg.aws_account_id,
            config.get("provider", "xelo-rules"),
            cfg.profile or "<default chain>",
        )

        # ── Collect findings via the vulnerability scanner ──────────────────
        provider = config.get("provider", "xelo-rules")
        try:
            findings = self._collect_findings(sbom, provider, config)
        except Exception as exc:
            _log.error("vulnerability scan failed before Security Hub push: %s", exc)
            raise RuntimeError(
                f"vulnerability scan failed before Security Hub push: {exc}"
            ) from exc

        _log.info("%d finding(s) collected from provider '%s'", len(findings), provider)

        if not findings:
            _log.info("no findings — skipping Security Hub push")
            return ToolResult(
                status="ok",
                tool=self.name,
                message="No findings to push to AWS Security Hub",
                details={"submitted": 0, "failed": 0, "region": cfg.region},
            )

        # ── Build ASFF payload ───────────────────────────────────────────────
        product_arn = (
            f"arn:aws:securityhub:{cfg.region}:{cfg.aws_account_id}"
            f":product/{cfg.aws_account_id}/{cfg.product_arn_suffix}"
        )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        sbom_id = sbom.get("target", "unknown-sbom")
        _log.debug("product ARN: %s", product_arn)

        asff_list = [
            self._to_asff(f, cfg.aws_account_id, cfg.region, product_arn, now, sbom_id)
            for f in findings
        ]
        _log.debug("built %d ASFF record(s)", len(asff_list))

        # ── Build boto3 session / client ─────────────────────────────────────
        session_kwargs: dict[str, Any] = {"region_name": cfg.region}
        if cfg.profile:
            session_kwargs["profile_name"] = cfg.profile

        try:
            session = boto3.Session(**session_kwargs)
            client = session.client("securityhub")
        except Exception as exc:
            exc_type = type(exc).__name__
            _log.error(
                "failed to create Security Hub client (region=%s, profile=%s): %s: %s",
                cfg.region, cfg.profile or "<default chain>", exc_type, exc,
            )
            raise RuntimeError(
                f"could not create AWS Security Hub client: {exc_type}: {exc}"
            ) from exc

        # ── Push in batches of ≤ 100 (AWS API limit) ─────────────────────────
        total_submitted = 0
        total_failed = 0
        all_failed: list[dict[str, Any]] = []
        num_batches = (len(asff_list) + _BATCH_SIZE - 1) // _BATCH_SIZE

        for i in range(0, len(asff_list), _BATCH_SIZE):
            batch = asff_list[i : i + _BATCH_SIZE]
            batch_num = i // _BATCH_SIZE + 1
            _log.info(
                "batch %d/%d: submitting %d finding(s) (indices %d–%d)",
                batch_num,
                num_batches,
                len(batch),
                i + 1,
                i + len(batch),
            )
            try:
                response = client.batch_import_findings(Findings=batch)
            except Exception as exc:
                exc_type = type(exc).__name__
                _log.error(
                    "Security Hub API error on batch %d/%d: %s: %s",
                    batch_num, num_batches, exc_type, exc,
                )
                raise RuntimeError(
                    f"Security Hub API error on batch {batch_num}/{num_batches}: "
                    f"{exc_type}: {exc}"
                ) from exc

            success = response.get("SuccessCount", 0)
            failed  = response.get("FailedCount", 0)
            total_submitted += success
            total_failed    += failed
            all_failed.extend(response.get("FailedFindings", []))
            _log.debug(
                "batch %d/%d: %d accepted, %d rejected",
                batch_num, num_batches, success, failed,
            )
            if failed:
                for ff in response.get("FailedFindings", []):
                    _log.warning(
                        "finding rejected by Security Hub: id=%s code=%s message=%s",
                        ff.get("Id", "?"),
                        ff.get("ErrorCode", "?"),
                        ff.get("ErrorMessage", "?"),
                    )

        status = "ok" if total_failed == 0 else "warning"
        message = (
            f"Pushed {total_submitted} finding(s) to AWS Security Hub"
            + (f" ({total_failed} rejected by API)" if total_failed else "")
        )
        _log.info(message)

        return ToolResult(
            status=status,
            tool=self.name,
            message=message,
            details={
                "submitted": total_submitted,
                "failed": total_failed,
                "failed_findings": all_failed,
                "product_arn": product_arn,
                "region": cfg.region,
            },
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _collect_findings(
        sbom: dict[str, Any],
        provider: str,
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Run the vulnerability scanner and return its raw findings list."""
        from xelo.toolbox.plugins.vulnerability import VulnerabilityScannerPlugin  # lazy

        scan_config: dict[str, Any] = {
            "provider": provider,
            "timeout": config.get("timeout", 15.0),
            "grype_timeout": config.get("grype_timeout", 60.0),
        }
        result = VulnerabilityScannerPlugin().run(sbom, scan_config)
        return cast(list[dict[str, Any]], result.details.get("findings", []))

    @staticmethod
    def _finding_id(rule_id: str, affected: list[str], account_id: str) -> str:
        """Return a stable, deterministic finding ID (SHA-256 prefix)."""
        key = f"{account_id}/{rule_id}/{','.join(sorted(affected))}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:16]
        return f"{account_id}/xelo-toolbox/{rule_id}/{digest}"

    @classmethod
    def _to_asff(
        cls,
        finding: dict[str, Any],
        account_id: str,
        region: str,
        product_arn: str,
        now: str,
        sbom_id: str,
    ) -> dict[str, Any]:
        """Convert a single Xelo finding dict to an ASFF record."""
        rule_id: str = finding.get("rule_id", "UNKNOWN")
        severity: str = finding.get("severity", "MEDIUM")
        affected: list[str] = finding.get("affected", [])

        is_cve = rule_id.startswith("CVE-") or rule_id.startswith("GHSA-")
        asff_type = _CVE_ASFF_TYPE if is_cve else _VLA_ASFF_TYPE

        return {
            "SchemaVersion": "2018-10-08",
            "Id": cls._finding_id(rule_id, affected, account_id),
            "ProductArn": product_arn,
            "GeneratorId": f"xelo-toolbox/{rule_id}",
            "AwsAccountId": account_id,
            "Types": [asff_type],
            "CreatedAt": now,
            "UpdatedAt": now,
            "Severity": {
                "Label": _SEVERITY_TO_ASFF.get(severity, "MEDIUM"),
                "Normalized": _SEVERITY_TO_NUMERIC.get(severity, 40),
            },
            "Title": finding.get("title", rule_id)[:256],
            "Description": finding.get("description", "")[:1024],
            "Remediation": {
                "Recommendation": {
                    "Text": finding.get("remediation", "")[:512],
                }
            },
            "Resources": [
                {
                    "Type": "Other",
                    "Id": f"xelo-sbom/{sbom_id}",
                    "Region": region,
                    "Details": {
                        "Other": {
                            "affected_components": ", ".join(affected)[:1024],
                        }
                    },
                }
            ],
        }
