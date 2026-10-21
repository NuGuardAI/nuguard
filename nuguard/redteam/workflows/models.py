"""Pydantic models for config-driven workflow attacks."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

PAYLOAD_PLACEHOLDER = "{payload}"


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

    @field_validator("method")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


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
    op: Literal["equals", "in", "contains", "gte", "lte", "exists"] = "equals"
    value: Any = None
    # Back-end agents run asynchronously: poll until the predicate matches or time runs out.
    poll_timeout_s: float = 60.0
    poll_interval_s: float = 5.0
    # JSON path of a pre-workflow baseline read is not needed: a negative control
    # is expressed by listing benign payloads in ``WorkflowSpec.control_payloads``.

    @field_validator("method")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()


class WorkflowSpec(BaseModel):
    """A stateful attack: ordered steps, an attack payload, and an outcome oracle."""

    id: str
    title: str
    description: str = ""
    goal_type: str = "POLICY_VIOLATION"
    severity: Literal["critical", "high", "medium", "low"] = "high"
    steps: list[WorkflowStep]
    oracle: WorkflowOracle
    # Attack texts substituted for ``{payload}``; one run per payload.
    payloads: list[str] = Field(min_length=1)
    # Benign texts run first; if the oracle already matches for these the
    # predicate is not attack-specific and no finding is raised (negative control).
    control_payloads: list[str] = Field(default_factory=list)
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
