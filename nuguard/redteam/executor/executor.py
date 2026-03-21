"""AttackExecutor — runs exploit chains step by step against the target."""
from __future__ import annotations

import logging

from nuguard.models.exploit_chain import ExploitChain, ExploitStep
from nuguard.models.policy import CognitivePolicy
from nuguard.redteam.policy_engine.evaluator import PolicyEvaluator, PolicyViolation
from nuguard.redteam.target.action_logger import ActionLogger
from nuguard.redteam.target.canary import CanaryScanner
from nuguard.redteam.target.client import TargetAppClient
from nuguard.redteam.target.session import AttackSession

from .chain_assembler import ChainAssembler

_log = logging.getLogger(__name__)


class StepResult:
    """Holds the outcome of executing a single ExploitStep."""

    def __init__(
        self, step: ExploitStep, response: str, tool_calls: list[dict]
    ) -> None:
        self.step = step
        self.response = response
        self.tool_calls = tool_calls
        self.success_signal_found: bool = bool(
            step.success_signal and step.success_signal.lower() in response.lower()
        )
        self.canary_hits: list[str] = []
        self.policy_violations: list[PolicyViolation] = []


class AttackExecutor:
    """Executes an ExploitChain step-by-step, collecting evidence."""

    MAX_MUTATIONS = 3

    def __init__(
        self,
        client: TargetAppClient,
        policy: CognitivePolicy | None = None,
        canary: CanaryScanner | None = None,
        logger: ActionLogger | None = None,
    ) -> None:
        self._client = client
        self._evaluator = PolicyEvaluator(policy) if policy else None
        self._canary = canary
        self._logger = logger or ActionLogger()

    async def run(
        self, chain: ExploitChain
    ) -> tuple[ExploitChain, list[StepResult]]:
        """Run the chain and return updated chain + step results."""
        chain.status = "running"
        session = self._client.new_session(chain.chain_id)
        steps = ChainAssembler.sort_steps(chain)
        results: list[StepResult] = []

        for step in steps:
            if chain.status == "aborted":
                break
            # Skip SCAN/EVALUATE/OBSERVE steps without payloads (handled by evaluator)
            if step.step_type in ("SCAN", "EVALUATE", "OBSERVE") and not step.payload:
                continue

            result = await self._execute_step(step, session, chain)
            results.append(result)

            if result.success_signal_found:
                session.add_evidence(step.step_id, result.response)
            elif step.on_failure == "abort":
                _log.info(
                    "Chain %s aborted at step %s", chain.chain_id, step.step_id
                )
                chain.status = "aborted"
                break
            elif step.on_failure == "mutate":
                # Simple mutation: rephrase with urgency
                for _attempt in range(self.MAX_MUTATIONS):
                    mutated_payload = (
                        f"[URGENT] {step.payload} (please respond directly)"
                    )
                    result = await self._execute_step_with_payload(
                        step, mutated_payload, session, chain
                    )
                    if result.success_signal_found:
                        session.add_evidence(step.step_id, result.response)
                        results.append(result)
                        break

        chain.status = "completed"
        return chain, results

    async def _execute_step(
        self,
        step: ExploitStep,
        session: AttackSession,
        chain: ExploitChain,
    ) -> StepResult:
        return await self._execute_step_with_payload(
            step, step.payload, session, chain
        )

    async def _execute_step_with_payload(
        self,
        step: ExploitStep,
        payload: str,
        session: AttackSession,
        chain: ExploitChain,
    ) -> StepResult:
        response, tool_calls = await self._client.send(payload, session)
        session.add_turn(payload, response, tool_calls)

        result = StepResult(step=step, response=response, tool_calls=tool_calls)

        # Canary scan
        if self._canary:
            result.canary_hits = (
                self._canary.scan(response) + self._canary.scan_decoded(response)
            )

        # Policy evaluation
        if self._evaluator:
            result.policy_violations = self._evaluator.evaluate(
                prompt=payload, response=response, tool_calls=tool_calls
            )

        # Log
        self._logger.log(
            chain_id=chain.chain_id,
            step_id=step.step_id,
            goal_type=chain.goal_type,
            payload=payload,
            response=response,
            succeeded=result.success_signal_found,
        )

        step.result = response
        step.succeeded = result.success_signal_found
        return result
