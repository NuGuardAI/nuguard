"""Structure (carrier) decorators — stub (redteam-proposal.md W6).

Populated once the JSON/XML field-smuggling and markdown/HTML carrier logic
currently duplicated as hardcoded payload strings in the C05/C08 builders
(``nuguard/redteam/scenarios/data_exfiltration.py``,
``covert_exfiltration.py``) is refactored into reusable transforms. Empty
for now — catalog behavior for C05/C08 is unchanged in this phase.
"""
from __future__ import annotations

from .base import PayloadDecorator

STRUCTURE_DECORATORS: tuple[PayloadDecorator, ...] = ()
