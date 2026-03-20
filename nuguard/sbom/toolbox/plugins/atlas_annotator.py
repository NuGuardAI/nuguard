"""MITRE ATLAS annotation plugin for Xelo AI SBOMs.

Runs two passes against the SBOM and annotates each finding with one or more
MITRE ATLAS v2 techniques that an attacker could exploit given the detected
weakness:

Pass 1 — VLA signal mapping
  Runs the VulnerabilityScannerPlugin with ``provider=xelo-rules`` (offline,
  no network required).  Every XELO-xxx finding is enriched with an ``atlas``
  block containing matching techniques from the static VLA → ATLAS mapping
  table in ``_atlas_data.py``.

Pass 2 — Native ATLAS graph checks
  Directly inspects the SBOM graph for additional structural patterns that
  map to ATLAS techniques but are not fully covered by any single VLA rule:

  ATLAS-NC-001  External MODEL without integrity hash         → AML.T0010, T0048
  ATLAS-NC-002  Writable DATASTORE reachable by unguarded model/agent → AML.T0020
  ATLAS-NC-003  MODEL–DEPLOYMENT path without AUTH node       → AML.T0035
  ATLAS-NC-004  AGENT or TOOL with outbound external-API capability → AML.T0036

Pass 3 — LLM enrichment (optional, ``config["llm"] = True``)
  Activated when the caller passes ``config={"llm": True}``.  Three extra
  steps run after the static passes:

  * OSV dependency CVE scan: ``VulnerabilityScannerPlugin(provider=osv)``
    runs against the SBOM deps and the resulting CVE findings are collected.
  * Grype CVE scan: ``VulnerabilityScannerPlugin(provider=grype)`` is run
    in parallel (skipped silently if grype is not on PATH).  Combined with
    OSV results and attached as ``cve_context`` on each ATLAS finding.
  * LLM summarisation: for every ATLAS finding a ``atlas.llm_summary``
    narrative is generated that relates the structural weakness to the
    project's specific CVE exposure.  A top-level ``details.llm_summary``
    executive summary is also produced.

  Supported config keys (all optional):
    ``llm``             – truthy value to activate (required)
    ``llm_model``       – litellm model string (default ``gpt-4o-mini``)
    ``llm_api_key``     – API key for the LLM provider
    ``llm_api_base``    – base URL override (e.g. Azure AI Foundry)
    ``llm_budget_tokens`` – max tokens across all LLM calls (default 50000)

Output ``details`` schema::

    {
      "atlas_version":       "v2",
      "basis":               "static",   # or "llm" when LLM enrichment ran
      "total_findings":      12,
      "techniques_identified": ["AML.T0051", ...],
      "tactics_covered":     ["Defense Evasion", ...],
      "confidence_breakdown": {"HIGH": 5, "MEDIUM": 4, "LOW": 1},
      "llm_summary":         "...executive narrative...",  # only in LLM mode
      "findings": [
        {
          "rule_id":     "XELO-001",
          "severity":    "CRITICAL",
          "title":       "...",
          "description": "...",
          "affected":    [...],
          "remediation": "...",
          "source":      "xelo-rules",
          "atlas": {
            "atlas_version": "v2",
            "techniques": [
              {
                "technique_id":   "AML.T0051",
                "technique_name": "LLM Jailbreak",
                "tactic_id":      "AML.TA0005",
                "tactic_name":    "Defense Evasion",
                "atlas_url":      "https://atlas.mitre.org/techniques/AML.T0051",
                "confidence":     "HIGH",
                "basis":          "static",
                "mitigations": [
                  {
                    "mitigation_id":   "AML.M0015",
                    "mitigation_name": "Adversarial Input Detection",
                    "mitigation_url":  "https://atlas.mitre.org/mitigations/AML.M0015"
                  }
                ]
              }
            ]
          },
          # only present in LLM mode:
          "cve_context": [
            {
              "cve_ids":  ["CVE-2024-xxxxx"],
              "package":  "langchain-core",
              "severity": "HIGH",
              "summary":  "...",
              "url":      "https://osv.dev/..."
            }
          ],
          "atlas": {
            "atlas_version": "v2",
            "llm_summary":   "...per-finding narrative...",  # only in LLM mode
            "techniques": [ ... ]
          }
        },
        ...
      ]
    }
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from xelo.toolbox.models import ToolResult
from xelo.toolbox.plugin_base import ToolPlugin
from xelo.toolbox.plugins._atlas_data import (
    ATLAS_VERSION,
    MITIGATIONS,
    NATIVE_CHECKS,
    OUTBOUND_KEYWORDS,
    TACTICS,
    TECHNIQUES,
    VLA_TO_ATLAS,
    EXTERNAL_PROVIDERS,
)

_log = logging.getLogger("toolbox.plugins.atlas")


class AtlasAnnotatorPlugin(ToolPlugin):
    """Annotate SBOM findings with MITRE ATLAS v2 technique IDs."""

    name = "atlas_annotate"

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def run(self, sbom: dict[str, Any], config: dict[str, Any]) -> ToolResult:
        """Annotate *sbom* with ATLAS technique mappings.

        When ``config["llm"]`` is truthy an additional OSV dependency CVE scan
        runs and the LLM enriches each finding with a ``atlas.llm_summary``
        narrative and produces a top-level ``details.llm_summary`` executive
        summary.  All other analysis is static and offline.
        """
        _log.info("ATLAS annotation starting (atlas_version=%s, basis=static)", ATLAS_VERSION)

        # ------------------------------------------------------------------
        # Pass 1 — run structural VLA rules and annotate findings
        # ------------------------------------------------------------------
        vla_findings = self._run_vla_pass(sbom)
        _log.debug("Pass 1: %d VLA finding(s) produced", len(vla_findings))

        # ------------------------------------------------------------------
        # Pass 2 — native ATLAS graph checks
        # ------------------------------------------------------------------
        native_findings = self._run_native_pass(sbom)
        _log.debug("Pass 2: %d native ATLAS finding(s) produced", len(native_findings))

        all_findings = vla_findings + native_findings
        _log.info("ATLAS annotation complete: %d total finding(s)", len(all_findings))

        # ------------------------------------------------------------------
        # Pass 3 — optional LLM enrichment (CVE correlation + summarisation)
        # ------------------------------------------------------------------
        use_llm = bool(config.get("llm") or config.get("enable_llm"))
        overall_llm_summary: str | None = None
        if use_llm:
            _log.info("Pass 3: LLM enrichment enabled")
            osv_findings = self._run_osv_pass(sbom)
            grype_findings = self._run_grype_pass(sbom)
            cve_findings = osv_findings + grype_findings
            _log.debug(
                "Pass 3: %d CVE finding(s) total (osv=%d grype=%d)",
                len(cve_findings),
                len(osv_findings),
                len(grype_findings),
            )
            all_findings = self._enrich_with_cve_context(all_findings, cve_findings)
            all_findings, overall_llm_summary = self._run_llm_enrichment(
                all_findings, cve_findings, sbom, config
            )

        # ------------------------------------------------------------------
        # Aggregate statistics
        # ------------------------------------------------------------------
        technique_ids: list[str] = []
        tactic_names: list[str] = []
        confidence_breakdown: dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

        for f in all_findings:
            atlas_block = f.get("atlas", {})
            for t in atlas_block.get("techniques", []):
                tid = t.get("technique_id", "")
                if tid and tid not in technique_ids:
                    technique_ids.append(tid)
                tname = t.get("tactic_name", "")
                if tname and tname not in tactic_names:
                    tactic_names.append(tname)
                conf = t.get("confidence", "").upper()
                if conf in confidence_breakdown:
                    confidence_breakdown[conf] += 1

        status = "warning" if all_findings else "ok"
        message = (
            f"{len(all_findings)} ATLAS-annotated finding(s) across "
            f"{len(technique_ids)} unique technique(s)"
            if all_findings
            else "No ATLAS findings detected"
        )

        details: dict[str, Any] = {
            "atlas_version": ATLAS_VERSION,
            "basis": "llm" if use_llm else "static",
            "total_findings": len(all_findings),
            "techniques_identified": technique_ids,
            "tactics_covered": tactic_names,
            "confidence_breakdown": confidence_breakdown,
            "findings": all_findings,
        }
        if overall_llm_summary:
            details["llm_summary"] = overall_llm_summary

        if str(config.get("format", "")).lower() == "markdown":
            details["markdown"] = _render_atlas_markdown(details)

        return ToolResult(
            status=status,
            tool=self.name,
            message=message,
            details=details,
        )

    # ------------------------------------------------------------------ #
    # Pass 1 helpers                                                       #
    # ------------------------------------------------------------------ #

    def _run_vla_pass(self, sbom: dict[str, Any]) -> list[dict[str, Any]]:
        """Run structural VLA rules then annotate each finding with ATLAS techniques."""
        # Lazy import to avoid circular dependency at module level
        from xelo.toolbox.plugins.vulnerability import VulnerabilityScannerPlugin  # noqa: PLC0415

        scanner = VulnerabilityScannerPlugin()
        result = scanner.run(sbom, {"provider": "xelo-rules"})

        raw_findings: list[dict[str, Any]] = list(result.details.get("findings", []) or [])
        annotated: list[dict[str, Any]] = []

        for finding in raw_findings:
            rule_id = finding.get("rule_id", "")
            technique_tuples = VLA_TO_ATLAS.get(rule_id, [])
            if technique_tuples:
                finding["atlas"] = _build_atlas_block(technique_tuples)
                _log.debug(
                    "annotated %s → %d ATLAS technique(s)",
                    rule_id,
                    len(technique_tuples),
                )
            else:
                # finding has no mapping; include without an atlas block
                _log.debug("no ATLAS mapping for rule_id=%r", rule_id)
            annotated.append(finding)

        return annotated

    # ------------------------------------------------------------------ #
    # Pass 3 helpers — OSV + Grype CVE scans + LLM enrichment            #
    # ------------------------------------------------------------------ #

    def _run_osv_pass(self, sbom: dict[str, Any]) -> list[dict[str, Any]]:
        """Run OSV dependency CVE scan; return findings (empty list on network error)."""
        from xelo.toolbox.plugins.vulnerability import VulnerabilityScannerPlugin  # noqa: PLC0415

        try:
            result = VulnerabilityScannerPlugin().run(sbom, {"provider": "osv"})
            return [f for f in (result.details.get("findings") or []) if f.get("source") == "osv"]
        except Exception as exc:  # network errors, etc.
            _log.warning("OSV scan skipped during ATLAS LLM enrichment: %s", exc)
            return []

    def _run_grype_pass(self, sbom: dict[str, Any]) -> list[dict[str, Any]]:
        """Run Grype CVE scan; return findings (empty list when grype is not on PATH)."""
        from xelo.toolbox.plugins.vulnerability import VulnerabilityScannerPlugin  # noqa: PLC0415

        try:
            result = VulnerabilityScannerPlugin().run(sbom, {"provider": "grype"})
            return [f for f in (result.details.get("findings") or []) if f.get("source") == "grype"]
        except Exception as exc:  # grype not installed, etc.
            _log.warning("Grype scan skipped during ATLAS LLM enrichment: %s", exc)
            return []

    def _enrich_with_cve_context(
        self,
        findings: list[dict[str, Any]],
        cve_findings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Attach the top CVE findings as ``cve_context`` on every ATLAS finding."""
        if not cve_findings:
            return findings

        _sev = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        top_cves = sorted(cve_findings, key=lambda f: _sev.get(f.get("severity", "LOW"), 99))[:10]
        cve_context = [
            {
                "cve_ids": f.get("cve_ids") or [f.get("rule_id", "")],
                "package": (f.get("affected") or ["?"])[0],
                "severity": f.get("severity", "UNKNOWN"),
                "summary": f.get("title", ""),
                "url": f.get("advisory_url", ""),
            }
            for f in top_cves
        ]

        return [{**f, "cve_context": cve_context} for f in findings]

    def _run_llm_enrichment(
        self,
        findings: list[dict[str, Any]],
        cve_findings: list[dict[str, Any]],
        sbom: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str]:
        """Synchronous entry point for LLM enrichment; falls back gracefully."""
        try:
            return asyncio.run(self._async_llm_enrichment(findings, cve_findings, sbom, config))
        except Exception as exc:
            _log.warning("LLM enrichment failed, falling back to static output: %s", exc)
            return findings, ""

    async def _async_llm_enrichment(
        self,
        findings: list[dict[str, Any]],
        cve_findings: list[dict[str, Any]],
        sbom: dict[str, Any],
        config: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], str]:
        """Build per-finding llm_summary + overall executive summary."""
        import os  # noqa: PLC0415

        from xelo.llm_client import LLMClient  # noqa: PLC0415

        model = config.get("llm_model") or "gpt-4o-mini"
        # For vertex_ai/* models the LLMClient uses a dedicated google_api_key
        # parameter to bypass litellm and call the Vertex AI REST API directly.
        # Fall back to GEMINI_API_KEY env var (set by xelo's .env convention).
        is_vertex = str(model).startswith("vertex_ai/")
        raw_key = config.get("llm_api_key") or None
        google_api_key: str | None = (
            (raw_key or os.environ.get("GEMINI_API_KEY") or None) if is_vertex else None
        )
        api_key: str | None = None if is_vertex else raw_key

        client = LLMClient(
            model=model,
            api_key=api_key,
            api_base=config.get("llm_api_base") or None,
            budget_tokens=int(config.get("llm_budget_tokens") or 50_000),
            google_api_key=google_api_key,
        )

        enriched: list[dict[str, Any]] = []
        for finding in findings:
            try:
                summary = await self._summarize_finding(client, finding, cve_findings)
                atlas = {**finding.get("atlas", {}), "llm_summary": summary}
                finding = {**finding, "atlas": atlas}
            except Exception as exc:
                _log.debug("Per-finding LLM summary failed for %s: %s", finding.get("rule_id"), exc)
            enriched.append(finding)

        overall = ""
        try:
            overall = await self._summarize_overall(client, enriched, cve_findings, sbom)
        except Exception as exc:
            _log.debug("Overall LLM summary failed: %s", exc)

        return enriched, overall

    async def _summarize_finding(
        self,
        client: Any,
        finding: dict[str, Any],
        cve_findings: list[dict[str, Any]],
    ) -> str:
        """Generate a concise LLM narrative for one ATLAS finding."""
        technique_ids = [
            t.get("technique_id") for t in (finding.get("atlas", {}).get("techniques") or [])
        ]
        _sev = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        top_cves = sorted(cve_findings, key=lambda f: _sev.get(f.get("severity", "LOW"), 99))[:5]
        cve_lines = "\n".join(
            f"- {(f.get('cve_ids') or [f.get('rule_id', '')])[:1][0]}: "
            f"{f.get('title', '')} ({f.get('severity', '')})"
            for f in top_cves
        )

        system = (
            "You are a security analyst specializing in AI/ML system risk assessment. "
            "Summarize a single MITRE ATLAS finding in 2-4 plain sentences. "
            "Mention specific CVEs only when they directly amplify the stated risk. "
            "Focus on attacker impact and remediation priority. No markdown."
        )
        user = (
            f"ATLAS Finding:\n"
            f"  ID: {finding.get('rule_id')}\n"
            f"  Title: {finding.get('title')}\n"
            f"  Techniques: {', '.join(str(t) for t in technique_ids)}\n"
            f"  Affected components: {', '.join(finding.get('affected', []))}\n"
            f"  Description: {finding.get('description', '')}\n"
        )
        if cve_lines:
            user += f"\nKnown CVEs in project dependencies:\n{cve_lines}\n"
        user += (
            "\nProvide a concise security summary connecting this ATLAS finding "
            "to the project's specific risk posture."
        )
        text: str
        text, _ = await client.complete_text(system, user)
        return text.strip()

    async def _summarize_overall(
        self,
        client: Any,
        findings: list[dict[str, Any]],
        cve_findings: list[dict[str, Any]],
        sbom: dict[str, Any],
    ) -> str:
        """Generate an executive summary across all ATLAS findings and CVEs."""
        finding_lines = (
            "\n".join(
                f"- {f.get('rule_id')}: {f.get('title')} (severity={f.get('severity')})"
                for f in findings
            )
            or "(none)"
        )
        _sev = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        top_cves = sorted(cve_findings, key=lambda f: _sev.get(f.get("severity", "LOW"), 99))[:8]
        cve_lines = (
            "\n".join(
                f"- {(f.get('cve_ids') or [f.get('rule_id', '')])[:1][0]}: "
                f"{f.get('title', '')} ({f.get('severity', '')})"
                for f in top_cves
            )
            or "(none)"
        )

        sbom_summary = sbom.get("summary") or {}
        frameworks = ", ".join(sbom_summary.get("frameworks") or []) or "unknown"

        system = (
            "You are a senior AI security analyst. Write a concise executive summary "
            "of MITRE ATLAS findings for an AI system. Highlight the highest-impact "
            "risks, reference relevant CVEs where they compound the risk, and close "
            "with the top 3 remediation priorities. 3-5 sentences, no markdown headers."
        )
        user = (
            f"AI system frameworks: {frameworks}\n\n"
            f"ATLAS Findings ({len(findings)} total):\n{finding_lines}\n\n"
            f"Known vulnerable dependencies ({len(cve_findings)} CVEs found):\n{cve_lines}\n\n"
            "Write a concise executive summary of the AI security posture and top priorities."
        )
        text: str
        text, _ = await client.complete_text(system, user)
        return text.strip()

    # ------------------------------------------------------------------ #
    # Pass 2 helpers                                                       #
    # ------------------------------------------------------------------ #

    def _run_native_pass(self, sbom: dict[str, Any]) -> list[dict[str, Any]]:
        """Run native ATLAS graph checks against the raw SBOM."""
        nodes: list[dict[str, Any]] = list(sbom.get("nodes") or [])
        edges: list[dict[str, Any]] = list(sbom.get("edges") or [])

        findings: list[dict[str, Any]] = []

        # Build fast lookup structures
        nodes_by_id = {n.get("id", ""): n for n in nodes}
        node_types_by_id: dict[str, str] = {
            n.get("id", ""): (n.get("component_type") or "").upper() for n in nodes
        }
        # adjacency: source → set of target ids (directed)
        adjacency: dict[str, set[str]] = {}
        for edge in edges:
            src = edge.get("source") or edge.get("from") or ""
            tgt = edge.get("target") or edge.get("to") or ""
            if src and tgt:
                adjacency.setdefault(src, set()).add(tgt)

        type_sets: dict[str, set[str]] = {}
        for nid, ntype in node_types_by_id.items():
            type_sets.setdefault(ntype, set()).add(nid)

        findings += self._check_nc001_external_model_no_hash(nodes, type_sets)
        findings += self._check_nc002_unguarded_datastore(type_sets, adjacency, node_types_by_id)
        findings += self._check_nc003_model_deployment_no_auth(
            type_sets, adjacency, node_types_by_id, nodes_by_id
        )
        findings += self._check_nc004_outbound_agent_tool(nodes, type_sets)

        return findings

    # NC-001 ----------------------------------------------------------------

    def _check_nc001_external_model_no_hash(
        self,
        nodes: list[dict[str, Any]],
        type_sets: dict[str, set[str]],
    ) -> list[dict[str, Any]]:
        check = NATIVE_CHECKS[0]  # ATLAS-NC-001
        affected: list[str] = []

        for nid in type_sets.get("MODEL", set()):
            node = next((n for n in nodes if n.get("id") == nid), {})
            name = node.get("name", nid)
            provider = (
                node.get("provider") or node.get("metadata", {}).get("provider") or ""
            ).lower()
            extras = (node.get("metadata") or {}).get("extras") or {}
            has_external = any(p in provider for p in EXTERNAL_PROVIDERS)
            has_hash = bool(extras.get("integrity_hash"))
            if has_external and not has_hash:
                affected.append(name)
                _log.debug("NC-001: external model '%s' has no integrity_hash", name)

        if not affected:
            return []

        return [_native_finding(check, affected)]

    # NC-002 ----------------------------------------------------------------

    def _check_nc002_unguarded_datastore(
        self,
        type_sets: dict[str, set[str]],
        adjacency: dict[str, set[str]],
        node_types_by_id: dict[str, str],
    ) -> list[dict[str, Any]]:
        check = NATIVE_CHECKS[1]  # ATLAS-NC-002
        affected: list[str] = []

        agent_model_ids = type_sets.get("AGENT", set()) | type_sets.get("MODEL", set())
        datastore_ids = type_sets.get("DATASTORE", set())

        if not datastore_ids or not agent_model_ids:
            return []

        # For each agent/model, check if it can reach a datastore WITHOUT
        # passing through any guardrail node
        for src in agent_model_ids:
            # BFS: can we reach a datastore?
            visited: set[str] = {src}
            queue = list(adjacency.get(src, set()))
            reached_ds: set[str] = set()
            guarded = False

            while queue:
                nid = queue.pop()
                if nid in visited:
                    continue
                visited.add(nid)
                ntype = node_types_by_id.get(nid, "")
                if ntype == "GUARDRAIL":
                    guarded = True
                    break
                if nid in datastore_ids:
                    reached_ds.add(nid)
                queue.extend(adjacency.get(nid, set()) - visited)

            if reached_ds and not guarded:
                affected.extend(reached_ds)
                _log.debug(
                    "NC-002: %s can reach datastore(s) %s without guardrail",
                    src,
                    reached_ds,
                )

        affected = list(dict.fromkeys(affected))  # deduplicate, preserve order
        if not affected:
            return []
        return [_native_finding(check, affected)]

    # NC-003 ----------------------------------------------------------------

    def _check_nc003_model_deployment_no_auth(
        self,
        type_sets: dict[str, set[str]],
        adjacency: dict[str, set[str]],
        node_types_by_id: dict[str, str],
        nodes_by_id: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        check = NATIVE_CHECKS[2]  # ATLAS-NC-003
        affected: list[str] = []

        model_ids = type_sets.get("MODEL", set())
        deploy_ids = type_sets.get("DEPLOYMENT", set())
        auth_ids = type_sets.get("AUTH", set())

        if not model_ids or not deploy_ids:
            return []

        # If there are no AUTH nodes at all, flag all MODEL nodes
        if not auth_ids:
            for mid in model_ids:
                name = nodes_by_id.get(mid, {}).get("name", mid)
                affected.append(name)
                _log.debug("NC-003: model '%s' has no AUTH node in SBOM", name)
        else:
            # Check if any path from a MODEL reaches DEPLOYMENT without AUTH
            for mid in model_ids:
                visited: set[str] = {mid}
                queue = list(adjacency.get(mid, set()))
                reached_deploy = False
                passed_auth = False

                while queue:
                    nid = queue.pop()
                    if nid in visited:
                        continue
                    visited.add(nid)
                    ntype = node_types_by_id.get(nid, "")
                    if ntype == "AUTH":
                        passed_auth = True
                        break
                    if nid in deploy_ids:
                        reached_deploy = True
                    queue.extend(adjacency.get(nid, set()) - visited)

                if reached_deploy and not passed_auth:
                    name = nodes_by_id.get(mid, {}).get("name", mid)
                    affected.append(name)
                    _log.debug("NC-003: model '%s' reaches DEPLOYMENT without AUTH", name)

        affected = list(dict.fromkeys(affected))
        if not affected:
            return []
        return [_native_finding(check, affected)]

    # NC-004 ----------------------------------------------------------------

    def _check_nc004_outbound_agent_tool(
        self,
        nodes: list[dict[str, Any]],
        type_sets: dict[str, set[str]],
    ) -> list[dict[str, Any]]:
        check = NATIVE_CHECKS[3]  # ATLAS-NC-004
        affected: list[str] = []

        candidate_ids = type_sets.get("AGENT", set()) | type_sets.get("TOOL", set())

        for nid in candidate_ids:
            node = next((n for n in nodes if n.get("id") == nid), {})
            name = (node.get("name") or nid).lower()
            description = (node.get("description") or "").lower()
            combined = name + " " + description
            if any(kw in combined for kw in OUTBOUND_KEYWORDS):
                display = node.get("name", nid)
                affected.append(display)
                _log.debug("NC-004: outbound-capable node '%s'", display)

        if not affected:
            return []
        return [_native_finding(check, affected)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_atlas_block(
    technique_tuples: list[tuple[str, str]],
) -> dict[str, Any]:
    """Construct the ``atlas`` annotation block for a finding."""
    techniques: list[dict[str, Any]] = []
    for tid, confidence in technique_tuples:
        tech = TECHNIQUES.get(tid)
        if tech is None:
            _log.warning("unknown technique ID '%s' in VLA_TO_ATLAS mapping", tid)
            continue
        tactic_id = str(tech["tactic_id"])
        tactic = TACTICS.get(tactic_id, {})
        mitigation_list = [
            MITIGATIONS[mid]
            for mid in cast(list[str], tech.get("mitigation_ids") or [])
            if mid in MITIGATIONS
        ]
        techniques.append(
            {
                "technique_id": tid,
                "technique_name": tech["technique_name"],
                "tactic_id": tactic_id,
                "tactic_name": tactic.get("tactic_name", ""),
                "atlas_url": tech["technique_url"],
                "confidence": confidence,
                "basis": "static",
                "mitigations": mitigation_list,
            }
        )
    return {"atlas_version": ATLAS_VERSION, "techniques": techniques}


def _native_finding(
    check: dict[str, object],
    affected: list[str],
) -> dict[str, Any]:
    """Build an annotated finding dict for a native ATLAS check."""
    technique_tuples: list[tuple[str, str]] = [
        (tid, conf) for tid, conf in cast(list[tuple[str, str]], check.get("techniques") or [])
    ]
    return {
        "rule_id": check["check_id"],
        "severity": _max_severity(technique_tuples),
        "title": check["title"],
        "description": check["description"],
        "affected": affected,
        "remediation": check["remediation"],
        "source": "atlas-native",
        "atlas": _build_atlas_block(technique_tuples),
    }


def _max_severity(technique_tuples: list[tuple[str, str]]) -> str:
    """Return the highest severity implied by the technique confidences."""
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    if not technique_tuples:
        return "LOW"
    best = min(technique_tuples, key=lambda t: order.get(t[1], 99))
    # Map confidence to a finding severity
    return {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(best[1], "LOW")


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------

_SEV_EMOJI = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}


def _render_atlas_markdown(details: dict[str, Any]) -> str:
    """Render ATLAS annotation details as a human-readable Markdown report."""
    lines: list[str] = []
    basis = details.get("basis", "static")
    total = details.get("total_findings", 0)
    techniques = details.get("techniques_identified") or []
    tactics = details.get("tactics_covered") or []
    cb = details.get("confidence_breakdown") or {}

    # ── Header ──────────────────────────────────────────────────────────
    lines += [
        "# MITRE ATLAS Annotation Report",
        "",
        f"**ATLAS version:** {details.get('atlas_version', 'v2')}  ",
        f"**Basis:** {basis}  ",
        "",
    ]

    # ── Executive summary (LLM mode) ────────────────────────────────────
    if details.get("llm_summary"):
        lines += [
            "## Executive Summary",
            "",
            details["llm_summary"],
            "",
        ]

    # ── Summary table ───────────────────────────────────────────────────
    lines += ["## Summary", ""]
    summary_rows = [
        ["Total findings", str(total)],
        ["Techniques identified", ", ".join(techniques) if techniques else "\u2014"],
        ["Tactics covered", ", ".join(tactics) if tactics else "\u2014"],
        ["Confidence — HIGH", str(cb.get("HIGH", 0))],
        ["Confidence — MEDIUM", str(cb.get("MEDIUM", 0))],
        ["Confidence — LOW", str(cb.get("LOW", 0))],
    ]
    lines += [
        "| Field | Value |",
        "| --- | --- |",
    ]
    for k, v in summary_rows:
        lines.append(f"| {k} | {v} |")
    lines.append("")

    # ── Findings ────────────────────────────────────────────────────────
    findings: list[dict[str, Any]] = list(details.get("findings") or [])
    if not findings:
        lines += ["## Findings", "", "_No ATLAS findings detected._", ""]
        return "\n".join(lines)

    lines += ["## Findings", ""]

    for finding in findings:
        rule_id = finding.get("rule_id", "")
        sev = finding.get("severity", "")
        title = finding.get("title", "")
        emoji = _SEV_EMOJI.get(sev, "")
        affected = finding.get("affected") or []
        description = finding.get("description", "")
        remediation = finding.get("remediation", "")
        atlas_block = finding.get("atlas") or {}
        cve_context = finding.get("cve_context") or []

        lines += [
            f"### {rule_id} {emoji} {sev} — {title}",
            "",
        ]
        if affected:
            affected_str = ", ".join(f"`{a}`" for a in affected)
            lines += [f"**Affected:** {affected_str}  ", ""]
        if description:
            lines += [f"**Description:** {description}", ""]

        # ATLAS techniques table
        techniques_list: list[dict[str, Any]] = list(atlas_block.get("techniques") or [])
        if techniques_list:
            lines += [
                "#### ATLAS Techniques",
                "",
                "| Technique | Name | Tactic | Confidence |",
                "| --- | --- | --- | --- |",
            ]
            for tech in techniques_list:
                tid = tech.get("technique_id", "")
                tname = tech.get("technique_name", "")
                tactic = tech.get("tactic_name", "")
                conf = tech.get("confidence", "")
                url = tech.get("atlas_url", "")
                tid_link = f"[{tid}]({url})" if url else tid
                lines.append(f"| {tid_link} | {tname} | {tactic} | {conf} |")
            lines.append("")

            # Mitigations
            mitigations: list[dict[str, Any]] = []
            for tech in techniques_list:
                for m in tech.get("mitigations") or []:
                    if m not in mitigations:
                        mitigations.append(m)
            if mitigations:
                lines += ["**Mitigations:**", ""]
                for m in mitigations:
                    mid = m.get("mitigation_id", "")
                    mname = m.get("mitigation_name", "")
                    murl = m.get("mitigation_url", "")
                    mid_link = f"[{mid}]({murl})" if murl else mid
                    lines.append(f"- {mid_link} {mname}")
                lines.append("")

        # CVE context (LLM mode)
        if cve_context:
            lines += [
                "#### CVE Context",
                "",
                "| Package | CVE(s) | Severity |",
                "| --- | --- | --- |",
            ]
            for cve in cve_context:
                pkg = cve.get("package", "")
                cve_ids = ", ".join(cve.get("cve_ids") or [])
                sev_cve = cve.get("severity", "")
                url_cve = cve.get("url", "")
                cve_link = f"[{cve_ids}]({url_cve})" if url_cve and cve_ids else cve_ids
                lines.append(f"| `{pkg}` | {cve_link} | {sev_cve} |")
            lines.append("")

        # LLM per-finding summary
        if atlas_block.get("llm_summary"):
            lines += [
                "#### Analysis",
                "",
                atlas_block["llm_summary"],
                "",
            ]

        if remediation:
            lines += ["**Remediation:** " + remediation, ""]

        lines.append("---")
        lines.append("")

    return "\n".join(lines)
