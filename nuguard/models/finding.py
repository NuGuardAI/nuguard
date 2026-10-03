"""Finding model for static analysis and redteam results."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Finding(BaseModel):
    finding_id: str
    title: str
    severity: Severity
    description: str
    affected_component: str | None = None
    remediation: str | None = None
    references: list[str] = Field(default_factory=list)
    # Set only for OS-package findings from a container-image scan (grype/trivy
    # scanning a CONTAINER_IMAGE node) — the image ref and, when the SBOM has
    # Dockerfile evidence for it, where that image is declared in the repo.
    container_image: str | None = None
    container_image_locations: list[str] = Field(default_factory=list)
    # Redteam-specific fields
    goal_type: str | None = None
    scenario_type: str | None = None
    sbom_path: list[str] = Field(default_factory=list)
    sbom_path_descriptions: list[str] = Field(default_factory=list)
    policy_clauses_violated: list[str] = Field(default_factory=list)
    chain_id: str | None = None
    owasp_asi_ref: str | None = None
    owasp_llm_ref: str | None = None
    owasp_cicd_ref: str | None = None
    mitre_atlas_technique: str | None = None
    evidence: str | None = None
    log_correlation_status: str | None = None
    # Redteam evidence fields — mirrors behavior/judge.py output for unified reporting
    reasoning: str = ""                          # one-sentence explanation of the finding
    evidence_quote: str = ""                     # exact substring of agent response proving the breach
    success_indicator: str | None = None         # named success indicator (e.g. "PII_disclosed")
    scores: dict[str, int | None] = Field(default_factory=dict)  # 1-5 rubric scores
    # Redteam step-level detail — each entry describes one executed attack step
    attack_steps: list[dict] = Field(default_factory=list)
    # Golden-data baseline — the authenticated test account's own data, captured via
    # a DISCOVER step and used to distinguish expected self-returns from genuine
    # cross-account leakage in IDOR/data-exfiltration scenarios. Populated only when
    # the scenario's chain used golden-data comparison.
    golden_ids: list[str] = Field(default_factory=list)
    golden_name: str | None = None
    golden_data_excerpt: str | None = None
    # Post-finding verification probe result (None = not run, True = reproduced, False = unconfirmed)
    verified: bool | None = None
    # NGRS (NuGuard Risk Score) — see nuguard.redteam.risk_engine.ngrs. 0-100
    # Impact x Likelihood score that `severity` above is banded from; `ngrs_vector`
    # is the human-readable factor breakdown (e.g. "DC:4/VOL:2/SC:1/ACT:3/EV:3/PRE:2/T:2/PM:+1")
    # so "why is this CRITICAL" is auditable from the finding alone.
    ngrs_score: int | None = None
    ngrs_vector: str | None = None
    # Progressive-methodology evidence fields (docs/claude-redteam-3.md) — "allow"
    # when the attack got through (a Finding was raised), "deny" when blocked;
    # guardrail_control mirrors LLMResponseEvaluator's closed-taxonomy refusal_reason
    # (content_filter, hitl_check, topic_guardrail, ...), i.e. "which control triggered".
    authorization_decision: str = ""
    guardrail_control: str = ""
    # ── redteam-proposal.md evidence fields (additive; all optional/defaulted) ──
    # True when the undecorated payload was refused but a W6 payload decorator
    # (encoding/framing/structure) succeeded on retry — a direct measure that
    # the control is a string filter rather than a policy ("plaintext refused,
    # base64 succeeded").
    evasion_differential: bool = False
    # Name of the PayloadDecorator that produced the success recorded above
    # (e.g. "base64", "rot13"); None when no decorator was involved.
    decorator_name: str | None = None
    # Structured hit metadata from the W8 egress-callback canary server
    # (source_ip, headers, decoded_payload, role) — proof of an SSRF/exfil
    # primitive rather than an inference from the chat answer alone.
    callback_evidence: dict | None = None
    # Which defence-regression paraphrase variant kind triggered this finding
    # (e.g. "roleplay", "audit_evidence", "encoded") — set only for
    # category="REGRESSION" findings produced by the paraphrase evaluator.
    regression_paraphrase_kind: str | None = None
    # W10 dual-path tool exposure verdict ("gate_bypass") — set only on
    # findings produced by nuguard.redteam.scenarios.dual_path's comparison
    # of a chat-mediated call against the same capability's direct-HTTP
    # invocation. Always "gate_bypass" when set; the other two
    # compare_dual_path outcomes never produce a finding.
    dual_path_verdict: str | None = None
    # W9 state-differential verification outcome ("hallucinated_action" |
    # "verified_mutation" | "silent_mutation") — set only on findings built
    # from nuguard.redteam.executor.state_diff.classify_state_outcome(). No
    # catalog builder wires this automatically yet (see state_diff.py's
    # module docstring); it's a primitive future write-path extensions call
    # directly once they have a concrete read-back path for their resource.
    state_diff_outcome: str | None = None
