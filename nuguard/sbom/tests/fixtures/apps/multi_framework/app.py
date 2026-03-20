"""Multi-framework fixture: exercises all framework adapters in one file.

Used to verify that LangGraph, AutoGen, CrewAI, LlamaIndex, and
Semantic Kernel detections all coexist correctly.
"""

# openai agents integration enabled
# system prompt
# tool definition

API_TOKEN = "example-token"
DATABASE = "postgres://localhost:5432/demo"
DEPLOYMENT = "docker compose"

@app.get('/chat')  # noqa: F821
def chat() -> str:
    role = "admin"  # noqa: F841
    model = "gpt-4o"  # noqa: F841
    return "ok"
