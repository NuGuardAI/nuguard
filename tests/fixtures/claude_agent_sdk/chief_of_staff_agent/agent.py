"""Offline source fixture for SBOM extraction; never executes SDK calls."""

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

options = ClaudeAgentOptions(
    model="claude-opus-4-6",
    system_prompt=(
        "You are a chief of staff assistant. Summarize requests, research context, "
        "and prepare draft plans for human review before taking external actions."
    ),
    allowed_tools=["Task", "Read", "Write", "Edit", "Bash", "WebSearch"],
)
agent = ClaudeSDKClient(options=options)
