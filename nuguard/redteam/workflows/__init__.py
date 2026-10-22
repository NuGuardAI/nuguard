"""Config-driven stateful workflow attacks with an outcome oracle.

Some attacks cannot be judged from a chat reply: the attacker submits input
through a business workflow (register a vendor, submit an invoice, upload a
document) and an *asynchronous* back-end agent acts on it. Success is a state
change — an invoice approved, a vendor activated — that is read back through
an oracle endpoint. See :mod:`nuguard.redteam.workflows.runner`.
"""
from nuguard.redteam.workflows.models import (
    WorkflowOracle,
    WorkflowResult,
    WorkflowSpec,
    WorkflowStep,
)
from nuguard.redteam.workflows.runner import WorkflowRunner, build_workflow_finding

__all__ = [
    "WorkflowOracle",
    "WorkflowResult",
    "WorkflowRunner",
    "WorkflowSpec",
    "WorkflowStep",
    "build_workflow_finding",
]
