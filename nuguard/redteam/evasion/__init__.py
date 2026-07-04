"""Evasion package — payload obfuscation and classifier-bypass techniques."""
from nuguard.redteam.evasion.context_flood import ContextFlood
from nuguard.redteam.evasion.cross_turn_smuggler import CrossTurnSmuggler
from nuguard.redteam.evasion.polyglot import PolyglotEvasion

__all__ = ["PolyglotEvasion", "CrossTurnSmuggler", "ContextFlood"]
