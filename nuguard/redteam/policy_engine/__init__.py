"""Policy engine for validating agent responses against cognitive policy.

Public API
----------
``PolicyEvaluator``
    Evaluates prompt/response/tool_calls against a CognitivePolicy.

``PolicyViolation``
    Dataclass representing a single policy violation.
"""

from nuguard.redteam.policy_engine.evaluator import PolicyEvaluator, PolicyViolation

__all__ = ["PolicyEvaluator", "PolicyViolation"]
