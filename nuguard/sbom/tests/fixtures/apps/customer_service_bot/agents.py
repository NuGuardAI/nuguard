"""Customer service routing bot using LangGraph.

Routes incoming support requests through specialised agents:
  triage  → determine intent (billing / technical / general)
  billing → handle payment, invoice, subscription questions
  tech    → handle product / technical issues
  tools   → execute tool calls (account lookup, ticket creation)
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, create_react_agent


# ── State ────────────────────────────────────────────────────────────────────

class ConversationState(TypedDict):
    messages: Annotated[list, operator.add]
    intent: str | None
    resolved: bool


# ── Models ───────────────────────────────────────────────────────────────────

triage_model = ChatOpenAI(model="gpt-4o-mini", temperature=0)
smart_model   = ChatAnthropic(model="claude-3-5-sonnet-20241022", temperature=0)


# ── System prompts ───────────────────────────────────────────────────────────

TRIAGE_PROMPT = SystemMessage(content="""\
You are a customer-service triage agent.
Classify the incoming request as one of: BILLING, TECHNICAL, GENERAL, ESCALATE.
Return only the classification label with no extra text.\
""")

BILLING_PROMPT = SystemMessage(content="""\
You are a billing specialist for a SaaS company.
You can look up invoices, process refunds, and update payment methods.
Always verify customer identity before discussing account details.\
""")

TECHNICAL_PROMPT = SystemMessage(content="""\
You are a technical support engineer.
Help customers debug issues, interpret error messages, and escalate bugs.
Provide step-by-step guidance and include relevant documentation links.\
""")


# ── Tools ────────────────────────────────────────────────────────────────────

def lookup_account(customer_id: str) -> dict:
    """Return account metadata for the given customer ID."""
    ...


def get_invoice(invoice_id: str) -> dict:
    """Fetch a single invoice by ID including line items and status."""
    ...


def create_support_ticket(
    customer_id: str,
    category: str,
    description: str,
    priority: str = "medium",
) -> str:
    """Open a support ticket and return the ticket ID."""
    ...


def search_knowledge_base(query: str, max_results: int = 5) -> list[dict]:
    """Full-text search the internal knowledge base."""
    ...


tools = [lookup_account, get_invoice, create_support_ticket, search_knowledge_base]
tool_node = ToolNode(tools)


# ── Sub-agents ───────────────────────────────────────────────────────────────

billing_agent  = create_react_agent(smart_model, [lookup_account, get_invoice])
technical_agent = create_react_agent(smart_model, [search_knowledge_base, create_support_ticket])


# ── Graph ────────────────────────────────────────────────────────────────────

workflow = StateGraph(ConversationState)

workflow.add_node("triage",    lambda state: {"intent": "BILLING"})
workflow.add_node("billing",   billing_agent)
workflow.add_node("technical", technical_agent)
workflow.add_node("tools",     tool_node)

workflow.add_edge(START,       "triage")
workflow.add_edge("triage",    "billing")
workflow.add_edge("triage",    "technical")
workflow.add_edge("billing",   "tools")
workflow.add_edge("technical", "tools")
workflow.add_edge("tools",     END)

graph = workflow.compile()
