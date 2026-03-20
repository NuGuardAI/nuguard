"""SQL-injectable / SSRF injection surface detector stub.

TODO: Implement detection logic.
"""

from __future__ import annotations


class InjectionSurfaceDetector:
    """Detect SQL-injectable and SSRF-possible nodes.

    TODO: Implement detection using attack graph traversal.
    """

    def detect(self, graph) -> list:
        """Return a list of injection surface findings.

        TODO: Implement detection.
        """
        raise NotImplementedError("InjectionSurfaceDetector not yet implemented")
