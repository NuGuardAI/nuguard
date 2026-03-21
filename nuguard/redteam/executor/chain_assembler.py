"""Assembles ExploitStep lists into validated ExploitChain DAGs."""
from __future__ import annotations

from nuguard.models.exploit_chain import ExploitChain, ExploitStep


class ChainAssembler:
    """Validates and topologically sorts exploit chain steps."""

    @staticmethod
    def sort_steps(chain: ExploitChain) -> list[ExploitStep]:
        """Return steps in topological order (dependencies first)."""
        step_map = {s.step_id: s for s in chain.steps}
        visited: set[str] = set()
        result: list[ExploitStep] = []

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            step = step_map.get(step_id)
            if step is None:
                return
            for dep in step.depends_on:
                visit(dep)
            visited.add(step_id)
            result.append(step)

        for s in chain.steps:
            visit(s.step_id)
        return result
