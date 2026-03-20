"""Document Q&A pipeline using LlamaIndex RAG + ReAct agent.

Architecture:
  VectorStoreIndex (Chroma)  ← documents ingested offline
  QueryEngineTool            ← wraps the index for semantic search
  FunctionTool               ← wraps a metrics calculator
  ReActAgent (Anthropic)     ← orchestrates tool calls

The agent answers natural-language questions by searching the indexed
documents and optionally computing derived metrics.
"""
from __future__ import annotations

from llama_index.core import Settings, VectorStoreIndex
from llama_index.core.agent import ReActAgent
from llama_index.core.tools import FunctionTool, QueryEngineTool
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.anthropic import Anthropic
from llama_index.vector_stores.chroma import ChromaVectorStore


# ── Model / embedding configuration ─────────────────────────────────────────

Settings.llm = Anthropic(
    model="claude-3-5-sonnet-20241022",
    max_tokens=4096,
)
Settings.embed_model = OpenAIEmbedding(
    model="text-embedding-3-small",
    dimensions=1536,
)


# ── Vector store ─────────────────────────────────────────────────────────────

chroma_store = ChromaVectorStore(collection_name="company_docs")
index = VectorStoreIndex.from_vector_store(chroma_store)
query_engine = index.as_query_engine(similarity_top_k=5)


# ── Tools ────────────────────────────────────────────────────────────────────

doc_search_tool = QueryEngineTool.from_defaults(
    query_engine=query_engine,
    name="document_search",
    description=(
        "Search company policy documents, runbooks, and product guides. "
        "Use this for factual questions about internal procedures."
    ),
)


def calculate_kpis(revenue: float, cost: float, users: int) -> dict:
    """Compute gross margin, ARPU, and LTV from raw business metrics."""
    gross_margin = (revenue - cost) / revenue if revenue else 0.0
    arpu = revenue / users if users else 0.0
    return {"gross_margin": gross_margin, "arpu": arpu, "ltv_estimate": arpu * 24}


kpi_tool = FunctionTool.from_defaults(
    fn=calculate_kpis,
    name="calculate_kpis",
    description="Calculate gross margin, ARPU, and estimated LTV from revenue/cost/user data.",
)


# ── Agent ─────────────────────────────────────────────────────────────────────

agent = ReActAgent.from_tools(
    tools=[doc_search_tool, kpi_tool],
    verbose=True,
    max_iterations=10,
    system_prompt="""\
You are an analytical assistant for a SaaS company.
Answer questions using company documents and compute metrics when needed.
Always cite your document sources and show calculation steps.\
""",
)
