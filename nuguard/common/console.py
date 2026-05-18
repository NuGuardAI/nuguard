"""Shared Rich console helpers used by behavior and redteam modules."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

# Write to stderr so turn traces appear in the same stream as DEBUG log lines.
# This ensures they are captured by log redirects like `exec > >(tee) 2>&1`
# and interleaved naturally with the structured log output.
_console = Console(stderr=True)


def print_turn(
    module: str,
    scenario_name: str,
    turn_idx: int,
    url: str,
    request: str,
    response: str,
    tool_calls: list[dict] | None = None,
    result_lines: list[str] | None = None,
) -> None:
    """Print a single turn's request/response to the console.

    Parameters
    ----------
    module:
        Short label shown in the rule separator, such as ``"behavior"`` or
        ``"redteam"``.
    scenario_name:
        Human-readable scenario/scenario title.
    turn_idx:
        1-based turn number within the scenario.
    url:
        Full URL of the request (target_url + endpoint path).
    request:
        The outgoing request payload / prompt text.
    response:
        The response body / agent reply.
    tool_calls:
        Optional list of OpenAI-style tool-call dicts to display between the
        request and response panels.
    result_lines:
        Optional list of pre-formatted Rich markup strings printed after the
        response panel (verdict scores, step outcome, etc.).
    """
    _console.rule(
        f"[bold cyan]{module}[/bold cyan] · {scenario_name} · turn {turn_idx}",
        style="cyan",
    )
    _console.print(
        Panel(
            request,
            title=f"[bold]→ REQUEST[/bold]  {url}",
            title_align="left",
            border_style="blue",
            expand=True,
        )
    )
    if tool_calls:
        tool_str = "  ".join(
            tc.get("name") or tc.get("function", {}).get("name", "?")
            for tc in tool_calls
        )
        _console.print(f"  [dim]tool_calls:[/dim] [yellow]{tool_str}[/yellow]")
    _console.print(
        Panel(
            response or "[dim](empty)[/dim]",
            title="[bold]← RESPONSE[/bold]",
            title_align="left",
            border_style="green",
            expand=True,
        )
    )
    for line in result_lines or []:
        _console.print(line)
