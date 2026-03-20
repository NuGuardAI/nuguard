"""AI research assistant built with the OpenAI Agents SDK.

A two-agent pipeline:
  researcher  – searches the web, fetches papers, synthesises findings
  writer      – formats findings into a polished report

Handoff: researcher → writer when synthesis is complete.
"""
from __future__ import annotations

from agents import Agent, Runner, handoff
from agents.tool import function_tool


# ── Tools ────────────────────────────────────────────────────────────────────

@function_tool
def web_search(query: str, num_results: int = 10) -> list[dict]:
    """Search the public web and return ranked result snippets."""
    ...


@function_tool
def fetch_arxiv_papers(topic: str, max_results: int = 5) -> list[dict]:
    """Fetch metadata and abstracts for recent arXiv papers on *topic*."""
    ...


@function_tool
def download_and_summarise(url: str) -> str:
    """Download a PDF or HTML page and return a structured summary."""
    ...


@function_tool
def cite_apa(title: str, authors: list[str], year: int, url: str = "") -> str:
    """Format a citation in APA 7th edition style."""
    ...


# ── Agents ───────────────────────────────────────────────────────────────────

writer_agent = Agent(
    name="report_writer",
    instructions="""\
You transform raw research findings into clear, well-structured reports.
Use markdown headings, bullet points, and inline citations.
Conclude each report with a 'Key Takeaways' section.\
""",
    model="gpt-4o-mini",
)

research_agent = Agent(
    name="research_assistant",
    instructions="""\
You are an expert research assistant specialising in AI, ML, and computer science.
When given a topic:
1. Search the web for recent news and discussions.
2. Fetch relevant arXiv papers from the last 12 months.
3. Download and summarise the top 3 papers.
4. Synthesise findings into a structured briefing.
Hand off the briefing to the report_writer when done.\
""",
    model="gpt-4o",
    tools=[web_search, fetch_arxiv_papers, download_and_summarise, cite_apa],
    handoffs=[handoff(writer_agent)],
)


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_research(topic: str) -> str:
    result = await Runner.run(research_agent, f"Research topic: {topic}")
    return str(result.final_output)
