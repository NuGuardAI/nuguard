"""Poisoning package — document/data-layer poisoning attack techniques."""
from nuguard.redteam.poisoning.cross_modality import CrossModalitySmuggling
from nuguard.redteam.poisoning.gaslighting import GaslightingAttack
from nuguard.redteam.poisoning.rag_temporal import RAGTemporalPoisoning
from nuguard.redteam.poisoning.tool_state_desync import ToolStateDesync

__all__ = [
    "GaslightingAttack",
    "RAGTemporalPoisoning",
    "ToolStateDesync",
    "CrossModalitySmuggling",
]
