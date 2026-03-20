"""Orchestrate all static SBOM detectors.

TODO: Implement StaticAnalyzer that runs all detectors and aggregates findings.
"""

from __future__ import annotations

from nuguard.models.sbom import AiSbomDocument


class StaticAnalyzer:
    """Orchestrate all static analysis detectors against an AI-SBOM.

    TODO: Implement full detector pipeline.
    """

    def analyze(self, doc: AiSbomDocument) -> list:
        """Run all detectors and return a list of Finding objects.

        TODO: Wire all detectors in nuguard.analysis.detectors.
        """
        raise NotImplementedError("StaticAnalyzer.analyze not yet implemented")
