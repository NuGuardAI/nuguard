"""Automated code review system using CrewAI + AutoGen.

CrewAI drives the high-level review pipeline (three specialised agents working
through a structured task sequence), while AutoGen handles the interactive
back-and-forth between the code execution proxy and the model.
"""
from __future__ import annotations

# ── CrewAI layer ─────────────────────────────────────────────────────────────

from crewai import Agent, Crew, Task
from crewai.tools import BaseTool


class StaticAnalysisTool(BaseTool):
    name: str = "static_analysis"
    description: str = "Run ruff, mypy, and bandit on a file and return findings."

    def _run(self, file_path: str) -> str:
        ...


class DependencyAuditTool(BaseTool):
    name: str = "dependency_audit"
    description: str = "Run pip-audit and return known CVEs for project dependencies."

    def _run(self, project_dir: str) -> list[dict]:
        ...


static_tool = StaticAnalysisTool()
audit_tool  = DependencyAuditTool()

code_reviewer = Agent(
    role="Senior Software Engineer",
    goal="Identify correctness issues, code smells, and maintainability problems.",
    backstory="""\
You have 15 years of Python experience. You focus on readability, SOLID principles,
and catching logical bugs before they reach production.\
""",
    llm="anthropic/claude-3-5-sonnet-20241022",
    tools=[static_tool],
    verbose=True,
)

security_auditor = Agent(
    role="Application Security Engineer",
    goal="Find security vulnerabilities following OWASP Top 10 and SANS CWE Top 25.",
    backstory="""\
You specialise in secure code review. You look for injection flaws, broken auth,
exposed secrets, insecure deserialization, and supply-chain risks.\
""",
    llm="openai/gpt-4o",
    tools=[static_tool, audit_tool],
)

test_engineer = Agent(
    role="QA Engineer",
    goal="Assess test coverage and suggest missing test cases.",
    backstory="""\
You believe untested code is broken code. You write tests for edge cases
that developers typically overlook.\
""",
    llm="openai/gpt-4o-mini",
)

review_task = Task(
    description="Review the submitted diff for correctness and style issues.",
    expected_output="A list of numbered review comments with severity labels.",
    agent=code_reviewer,
)

security_task = Task(
    description="Audit the diff for security vulnerabilities and dependency CVEs.",
    expected_output="Security findings grouped by OWASP category with CVSS scores.",
    agent=security_auditor,
)

testing_task = Task(
    description="Identify missing test cases and propose concrete test stubs.",
    expected_output="A set of pytest test stubs covering uncovered branches.",
    agent=test_engineer,
)

review_crew = Crew(
    agents=[code_reviewer, security_auditor, test_engineer],
    tasks=[review_task, security_task, testing_task],
    verbose=True,
)


# ── AutoGen layer (execution sandbox) ────────────────────────────────────────

from autogen import AssistantAgent, UserProxyAgent  # noqa: E402

_gpt4_cfg = {"config_list": [{"model": "gpt-4o", "api_key": "..."}]}

executor = AssistantAgent(
    name="code_executor",
    system_message="""\
Execute code snippets in an isolated sandbox and report stdout, stderr, and
exit code. Never run destructive commands.\
""",
    llm_config=_gpt4_cfg,
)

proxy = UserProxyAgent(
    name="developer_proxy",
    human_input_mode="NEVER",
    code_execution_config={"work_dir": "/tmp/sandbox", "use_docker": True},
)
