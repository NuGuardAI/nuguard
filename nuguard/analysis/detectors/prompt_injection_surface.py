"""External data feeding into system prompt (injection surface) detector stub.

TODO: Implement detection logic.
"""

from __future__ import annotations


class PromptInjectionSurfaceDetector:
    """Detect external data sources feeding into PROMPT nodes.

    TODO: Implement detection using attack graph traversal.
    """

    def detect(self, graph) -> list:
        """Return a list of prompt injection surface findings.

        TODO: Implement detection.
        """
        raise NotImplementedError("PromptInjectionSurfaceDetector not yet implemented")
