"""Pydantic models for config-driven workflow attacks."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PAYLOAD_PLACEHOLDER = "{payload}"


class WorkflowOracle(BaseModel):
    """Reads back state after the workflow and decides whether the attack worked.

    The predicate describes the *bad* outcome: when it matches, the attack
    succeeded and a finding is raised.
    """

    method: str = "GET"
    path: str
    params: dict[str, str] = Field(default_factory=dict)
    principal: str = "primary"
    json_path: str
    # ``json_path``: dotted path into the JSON response; ``""`` selects the whole
    # response (use with ``contains``/``matches``) and ``"$status"`` the HTTP status
    # (so a 404 after a deletion is a readable outcome).
    op: Literal["equals", "in", "contains", "matches", "gte", "lte", "exists"] = "equals"
    value: Any = None
    # Back-end agents run asynchronously: poll until the predicate matches or time runs out.
    poll_timeout_s: float = 60.0
    poll_interval_s: float = 5.0

    @field_validator("method")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class WorkflowStep(BaseModel):
    """One HTTP request in a workflow.

    ``path``, ``body`` string values and ``params`` may reference ``{payload}``
    (the attack text under test) and any name captured by an earlier step
    (e.g. ``{vendor_id}``).
    """

    id: str
    method: str = "POST"
    path: str
    body: dict[str, Any] | None = None
    params: dict[str, str] = Field(default_factory=dict)
    # name -> dotted JSON path in the response (e.g. ``invoice.id``).
    capture: dict[str, str] = Field(default_factory=dict)
    # Accepted statuses; empty means any 2xx.
    expect_status: list[int] = Field(default_factory=list)
    # ``primary`` (the run's own credentials) or a canary ``tenant_id``.
    principal: str = "primary"
    # Poll this condition before running the step (e.g. wait for a vendor to be
    # rejected before requesting a re-review); the step fails if it never holds.
    wait_for: WorkflowOracle | None = None

    @field_validator("method")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class WorkflowRequirements(BaseModel):
    """When a bundled workflow template applies to a target.

    All listed conditions must hold. Empty means "always applicable".
    """

    # Every path must exist as an SBOM API_ENDPOINT (``{param}`` segments are wildcards).
    sbom_paths: list[str] = Field(default_factory=list)
    # Every path must answer a GET with a non-404 status on the live target.
    probe_paths: list[str] = Field(default_factory=list)
    # Minimum number of canary tenants with credentials (cross-tenant workflows).
    min_tenants: int = 0


class WorkflowSpec(BaseModel):
    """A stateful attack: ordered steps, an attack payload, and an outcome oracle."""

    id: str
    title: str
    description: str = ""
    goal_type: str = "POLICY_VIOLATION"
    severity: Literal["critical", "high", "medium", "low"] = "high"
    steps: list[WorkflowStep]
    oracle: WorkflowOracle
    # Attack texts substituted for ``{payload}``; one run per payload. Leave empty
    # to have them generated from ``payload_goal`` (LLM when configured, built-in
    # pretext templates otherwise) — the user never has to write an attack.
    payloads: list[str] = Field(default_factory=list)
    payload_goal: str = ""
    payload_style: Literal["pretext", "html_document", "text_document", "tool_description"] = "pretext"
    payload_count: int = Field(default=2, ge=1, le=6)
    # Benign texts run first; if the oracle already matches for these the
    # predicate is not attack-specific and no finding is raised (negative control).
    control_payloads: list[str] = Field(default_factory=list)
    # With no explicit controls, run one benign payload of the same style first.
    # Disable where a benign input legitimately reaches the oracle state too
    # (e.g. any ordinary vendor is activated, so activation proves nothing alone).
    auto_control: bool = True
    # Steps that always run afterwards (even on failure) to undo state the attack
    # changed, e.g. resetting a poisoned MCP tool description.
    cleanup: list[WorkflowStep] = Field(default_factory=list)
    # Conditions for a bundled template to apply to this target (see library).
    requires: WorkflowRequirements = Field(default_factory=WorkflowRequirements)
    remediation: str = ""
    owasp_llm_ref: str | None = None
    owasp_asi_ref: str | None = None
    # Stop after the first successful payload.
    stop_on_success: bool = True


class WorkflowResult(BaseModel):
    """Outcome of running one payload through a workflow."""

    spec_id: str
    payload: str
    succeeded: bool = False
    # True when the predicate also matched for a control payload (not attack-specific).
    control_matched: bool = False
    steps: list[dict[str, Any]] = Field(default_factory=list)
    observed: Any = None
    error: str = ""
