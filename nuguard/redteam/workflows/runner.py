"""Execute workflow specs against the target and judge the outcome with an oracle."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, Protocol

from nuguard.common.logging import get_logger
from nuguard.models.finding import Finding, Severity
from nuguard.redteam.workflows.models import (
    PAYLOAD_PLACEHOLDER,
    WorkflowOracle,
    WorkflowResult,
    WorkflowSpec,
    WorkflowStep,
)

_log = get_logger(__name__)

_TOKEN_RE = re.compile(r"\{(\w+)\}")
_MAX_RESPONSE_EXCERPT = 400


class _Client(Protocol):
    async def invoke_endpoint(
        self,
        path: str,
        method: str = "POST",
        body: dict | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        strip_auth: bool = False,
    ) -> tuple[int, str, dict]: ...


def render(value: Any, variables: dict[str, Any]) -> Any:
    """Substitute ``{name}`` tokens in strings, recursing into dicts and lists.

    A string that is exactly one known token takes the variable's native type
    (so a captured numeric ``file_id`` stays a number in a JSON body). Unknown
    tokens are left untouched and substituted text is never re-scanned, so a
    payload containing ``{braces}`` cannot inject further substitutions.
    """
    if isinstance(value, str):
        whole = _TOKEN_RE.fullmatch(value)
        if whole and whole.group(1) in variables:
            return variables[whole.group(1)]
        return _TOKEN_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: render(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, variables) for v in value]
    return value


def extract_json_path(data: Any, path: str) -> Any:
    """Return the value at dotted *path* (``a.b.0.c``); ``""`` selects the whole value.

    Returns ``None`` when the path is absent.
    """
    current = data
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def predicate_matches(op: str, observed: Any, expected: Any) -> bool:
    """Evaluate an oracle predicate; any type mismatch is a non-match."""
    if op == "exists":
        return observed is not None
    if observed is None:
        return False
    try:
        if op == "equals":
            return str(observed).lower() == str(expected).lower()
        if op == "in":
            return str(observed).lower() in {str(v).lower() for v in (expected or [])}
        if op == "contains":
            haystack = observed if isinstance(observed, str) else json.dumps(observed, default=str)
            return str(expected).lower() in haystack.lower()
        if op == "matches":
            haystack = observed if isinstance(observed, str) else json.dumps(observed, default=str)
            return re.search(str(expected), haystack) is not None
        if op == "gte":
            return float(observed) >= float(expected)
        if op == "lte":
            return float(observed) <= float(expected)
    except (TypeError, ValueError):
        return False
    return False


class WorkflowRunner:
    """Runs :class:`WorkflowSpec` objects through a target client.

    *tenant_headers* maps a canary ``tenant_id`` to its auth headers; steps with
    ``principal`` set to a tenant id are sent with those headers instead of the
    client's own credentials.
    """

    def __init__(
        self,
        client: _Client,
        tenant_headers: dict[str, dict[str, str]] | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._client = client
        self._tenant_headers = tenant_headers or {}
        self._sleep = sleep

    async def _request(
        self,
        principal: str,
        method: str,
        path: str,
        body: dict | None,
        params: dict[str, str],
    ) -> tuple[int, str, dict]:
        if principal != "primary":
            headers = self._tenant_headers.get(principal)
            if headers is None:
                raise ValueError(f"unknown workflow principal {principal!r}")
            return await self._client.invoke_endpoint(
                path, method=method, body=body, params=params or None,
                extra_headers=headers, strip_auth=True,
            )
        return await self._client.invoke_endpoint(
            path, method=method, body=body, params=params or None
        )

    async def _run_step(
        self, step: WorkflowStep, variables: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """Run one step; return ``(record, ok)`` and update *variables* with captures."""
        if step.wait_for is not None:
            ready, observed = await self._poll_oracle(step.wait_for, variables)
            if not ready:
                return (
                    {"step_id": step.id, "method": step.method, "path": step.path, "status": 0,
                     "response_excerpt": f"wait_for not met (last observed {observed!r})"},
                    False,
                )
        path = render(step.path, variables)
        body = render(step.body, variables) if step.body is not None else None
        params = render(step.params, variables)
        status, text, data = await self._request(step.principal, step.method, path, body, params)
        ok = status in step.expect_status if step.expect_status else 200 <= status < 300
        record: dict[str, Any] = {
            "step_id": step.id,
            "method": step.method,
            "path": path,
            "status": status,
            "response_excerpt": text[:_MAX_RESPONSE_EXCERPT],
        }
        if ok:
            for name, json_path in step.capture.items():
                captured = extract_json_path(data, json_path)
                if captured is not None:
                    variables[name] = captured
        return record, ok

    async def _poll_oracle(
        self, oracle: WorkflowOracle, variables: dict[str, Any]
    ) -> tuple[bool, Any]:
        """Poll the oracle until its predicate matches or the timeout elapses."""
        path = render(oracle.path, variables)
        params = render(oracle.params, variables)
        deadline = time.monotonic() + oracle.poll_timeout_s
        observed: Any = None
        while True:
            status, _text, data = await self._request(
                oracle.principal, oracle.method, path, None, params
            )
            if oracle.json_path == "$status":
                observed = status
                if predicate_matches(oracle.op, observed, oracle.value):
                    return True, observed
            elif 200 <= status < 300:
                observed = extract_json_path(data, oracle.json_path)
                if predicate_matches(oracle.op, observed, oracle.value):
                    return True, observed
            if time.monotonic() >= deadline:
                return False, observed
            await self._sleep(oracle.poll_interval_s)

    async def run_payload(self, spec: WorkflowSpec, payload: str) -> WorkflowResult:
        """Run *spec* once with *payload* substituted for ``{payload}``."""
        # ``run_id`` is unique per run so repeated runs in one session never collide
        # on unique fields (e.g. a vendor email).
        variables: dict[str, Any] = {
            PAYLOAD_PLACEHOLDER.strip("{}"): payload,
            "run_id": uuid.uuid4().hex[:8],
        }
        result = WorkflowResult(spec_id=spec.id, payload=payload)
        try:
            for step in spec.steps:
                record, ok = await self._run_step(step, variables)
                result.steps.append(record)
                if not ok:
                    result.error = f"step {step.id!r} returned HTTP {record['status']}"
                    return result
            result.succeeded, result.observed = await self._poll_oracle(spec.oracle, variables)
        except Exception as exc:  # noqa: BLE001 — a broken workflow must not abort the scan
            _log.warning("Workflow %s failed: %s", spec.id, exc)
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            await self._cleanup(spec, variables, result)
        return result

    async def _cleanup(
        self, spec: WorkflowSpec, variables: dict[str, Any], result: WorkflowResult
    ) -> None:
        """Run cleanup steps (best-effort) so the attack leaves no state behind."""
        for step in spec.cleanup:
            try:
                record, ok = await self._run_step(step, variables)
                record["cleanup"] = True
                result.steps.append(record)
                if not ok:
                    _log.warning("Workflow %s: cleanup step %r returned HTTP %s", spec.id, step.id, record["status"])
            except Exception as exc:  # noqa: BLE001
                _log.warning("Workflow %s: cleanup step %r failed: %s", spec.id, step.id, exc)

    async def run(self, spec: WorkflowSpec, llm: Any = None) -> list[WorkflowResult]:
        """Run controls then attack payloads; return every result.

        Attack payloads come from the spec or are generated (see
        :func:`nuguard.redteam.workflows.payloads.generate_payloads`).

        When a control payload already satisfies the oracle the predicate is not
        attack-specific, so attack payloads are skipped and the control result is
        flagged ``control_matched``.
        """
        results: list[WorkflowResult] = []
        from nuguard.redteam.workflows.payloads import benign_control  # noqa: PLC0415

        for control in benign_control(spec):
            control_result = await self.run_payload(spec, control)
            results.append(control_result)
            if control_result.succeeded:
                control_result.control_matched = True
                _log.warning(
                    "Workflow %s: oracle matched a benign control payload — skipping attacks",
                    spec.id,
                )
                return results
        from nuguard.redteam.workflows.payloads import generate_payloads  # noqa: PLC0415

        for payload in await generate_payloads(spec, llm):
            attack_result = await self.run_payload(spec, payload)
            results.append(attack_result)
            if attack_result.succeeded and spec.stop_on_success:
                break
        return results


def build_workflow_finding(spec: WorkflowSpec, result: WorkflowResult) -> Finding:
    """Convert a successful workflow result into a :class:`Finding`."""
    return Finding(
        finding_id=f"wf-{spec.id}-{uuid.uuid4().hex[:8]}",
        title=spec.title,
        severity=Severity(spec.severity),
        description=(
            (
                spec.description
                or f"Workflow attack {spec.id!r} changed application state: "
                f"{spec.oracle.json_path} {spec.oracle.op} {spec.oracle.value!r}."
            )
            # Marker so reports that omit finding ids still identify the workflow.
            + f" [workflow:{spec.id}]"
        ),
        affected_component=spec.oracle.path,
        remediation=spec.remediation or None,
        goal_type=spec.goal_type,
        scenario_type="WORKFLOW_ATTACK",
        owasp_llm_ref=spec.owasp_llm_ref,
        owasp_asi_ref=spec.owasp_asi_ref,
        evidence=(
            f"oracle {spec.oracle.method} {spec.oracle.path}: "
            f"{spec.oracle.json_path} = {result.observed!r}"
        ),
        reasoning="State change observed through the oracle endpoint after the attack payload was submitted.",
        evidence_quote=str(result.payload)[:_MAX_RESPONSE_EXCERPT],
        success_indicator="WORKFLOW_ORACLE_MATCH",
        attack_steps=result.steps,
        evidence_kind="state_oracle",
        effect_verified=True,
    )
