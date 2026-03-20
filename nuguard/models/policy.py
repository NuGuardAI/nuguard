"""Cognitive Policy data models.

TODO: Implement CognitivePolicy Pydantic model parsed from Markdown.
"""

from pydantic import BaseModel


class CognitivePolicy(BaseModel):
    """Parsed cognitive policy for an AI application."""

    allowed_topics: list[str] = []
    restricted_topics: list[str] = []
    restricted_actions: list[str] = []
    hitl_triggers: list[str] = []
    data_classification: list[str] = []
    rate_limits: dict[str, int] = {}
