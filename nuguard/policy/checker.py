"""Cognitive Policy ↔ SBOM static cross-checker stub.

TODO: Implement structural gap detection.
"""

from __future__ import annotations

from nuguard.models.policy import CognitivePolicy
from nuguard.models.sbom import AiSbomDocument


def check_policy_against_sbom(policy: CognitivePolicy, doc: AiSbomDocument) -> list:
    """Return a list of structural gap findings.

    TODO: Implement cross-checking logic (HITL triggers, restricted actions, etc.).
    """
    raise NotImplementedError("check_policy_against_sbom not yet implemented")
