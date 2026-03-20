"""Research agent built with LangGraph."""
from typing import Annotated, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

from .tools import TOOLS

SYSTEM_PROMPT = """You are an expert research assistant. Your task is to:
1. Search for relevant information on the topic.
2. Synthesize findings into a concise, accurate summary.
3. Always cite your sources and flag uncertain claims.

You have access to web search, arXiv paper lookup, and a calculator.
"""


class ResearchState(TypedDict):
    topic: str
    messages: Annotated[list, "messages"]
    draft: str
    sources: list[str]


llm = ChatAnthropic(model="claude-3-5-sonnet-20241022", temperature=0)
llm_with_tools = llm.bind_tools(TOOLS)


def researcher_node(state: ResearchState) -> ResearchState:
    system = SystemMessage(content=SYSTEM_PROMPT)
    response = llm_with_tools.invoke([system] + state["messages"])
    return {"messages": [response]}


def writer_node(state: ResearchState) -> ResearchState:
    prompt = f"Synthesize the research into a clear summary: {state['messages']}"
    response = llm.invoke(prompt)
    return {"draft": response.content}


tool_node = ToolNode(TOOLS)

workflow = StateGraph(ResearchState)
workflow.add_node("researcher", researcher_node)
workflow.add_node("tools", tool_node)
workflow.add_node("writer", writer_node)

workflow.add_edge("researcher", "tools")
workflow.add_edge("tools", "researcher")
workflow.add_edge("researcher", "writer")
workflow.add_edge("writer", END)

graph = workflow.compile()
