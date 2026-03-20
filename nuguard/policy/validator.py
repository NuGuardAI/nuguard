"""Cognitive Policy linter stub.

TODO: Implement policy completeness and ambiguity checks.
"""

from __future__ import annotations

from nuguard.models.policy import CognitivePolicy


def lint_policy(policy: CognitivePolicy) -> list[str]:
    """Return a list of lint warnings for *policy*.

    TODO: Implement linting rules.
    """
    raise NotImplementedError("lint_policy not yet implemented")
