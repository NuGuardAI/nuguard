"""Search and calculation tools for the research agent."""
from langchain_core.tools import tool


@tool
def web_search(query: str) -> str:
    """Search the web for information on a given query."""
    # Placeholder implementation
    return f"Search results for: {query}"


@tool
def arxiv_lookup(paper_id: str) -> str:
    """Look up a paper on arXiv by its ID."""
    return f"Abstract for paper {paper_id}"


@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression."""
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as exc:
        return f"Error: {exc}"


TOOLS = [web_search, arxiv_lookup, calculator]
