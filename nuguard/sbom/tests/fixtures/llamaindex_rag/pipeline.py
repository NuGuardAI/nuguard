"""RAG pipeline built with LlamaIndex."""
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader, Settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.llms.openai import OpenAI
from llama_index.llms.anthropic import Anthropic
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.chroma import ChromaVectorStore

# Configure global settings
Settings.llm = OpenAI(model="gpt-4o", temperature=0.1)
Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-large")
Settings.node_parser = SentenceSplitter(chunk_size=1024)

# Load and index documents
documents = SimpleDirectoryReader("data/").load_data()
vector_store = ChromaVectorStore(collection_name="research_docs")
index = VectorStoreIndex.from_documents(
    documents,
    vector_store=vector_store,
    show_progress=True,
)

# Build query engine
query_engine = RetrieverQueryEngine.from_args(
    retriever=index.as_retriever(similarity_top_k=5),
    llm=Anthropic(model="claude-3-5-haiku-20241022"),
)
