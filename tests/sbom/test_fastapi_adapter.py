"""Test the FastAPI framework adapter."""

from __future__ import annotations


from pathlib import Path


from nuguard.models.sbom import EdgeRelationshipType, NodeType
try:
    from nuguard.sbom.extractor.framework_adapters.fastapi import FastApiAdapter
except ImportError:
    import pytest
    pytest.skip("FastApiAdapter not yet ported to nuguard.sbom", allow_module_level=True)


@pytest.fixture
def adapter() -> FastApiAdapter:
    return FastApiAdapter()


FASTAPI_BASIC_SOURCE = """
from fastapi import FastAPI, Depends, APIRouter
from fastapi.security import OAuth2PasswordBearer, HTTPBearer

app = FastAPI()
router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@app.get("/items")
async def list_items():
    return []

@app.post("/items")
async def create_item(item: dict):
    return item

@router.get("/users/{user_id}")
async def get_user(user_id: int, token: str = Depends(oauth2_scheme)):
    return {"user_id": user_id}
"""

FASTAPI_AUTH_SOURCE = """
from fastapi import FastAPI, Security
from fastapi.security import HTTPBearer, APIKeyHeader

app = FastAPI()
bearer = HTTPBearer()
api_key = APIKeyHeader(name="X-API-Key")

@app.get("/protected", dependencies=[Security(bearer)])
async def protected_route():
    return {"status": "ok"}
"""

EMPTY_SOURCE = ""
INVALID_SYNTAX = "def foo(:"


def test_fastapi_app_detected(adapter: FastApiAdapter) -> None:
    """FastAPI() instantiation is detected as an AGENT node."""
    nodes, _ = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    assert len(agent_nodes) >= 1


def test_router_detected(adapter: FastApiAdapter) -> None:
    """APIRouter() instantiation is detected as an AGENT node."""
    nodes, _ = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    names = {n.name for n in agent_nodes}
    assert "router" in names or len(agent_nodes) >= 2


def test_get_endpoint_detected(adapter: FastApiAdapter) -> None:
    """@app.get('/items') is detected as an API_ENDPOINT node."""
    nodes, _ = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    ep_nodes = [n for n in nodes if n.component_type == NodeType.API_ENDPOINT]
    assert len(ep_nodes) >= 1
    paths = [n.metadata.endpoint for n in ep_nodes]
    assert "/items" in paths


def test_post_endpoint_method(adapter: FastApiAdapter) -> None:
    """@app.post('/items') endpoint has method=POST."""
    nodes, _ = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    ep_nodes = [n for n in nodes if n.component_type == NodeType.API_ENDPOINT]
    post_eps = [n for n in ep_nodes if n.metadata.method == "POST"]
    assert len(post_eps) >= 1


def test_auth_detected_from_depends(adapter: FastApiAdapter) -> None:
    """Depends(oauth2_scheme) in function params creates an AUTH node."""
    nodes, _ = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    auth_nodes = [n for n in nodes if n.component_type == NodeType.AUTH]
    assert len(auth_nodes) >= 1


def test_endpoint_auth_type_set(adapter: FastApiAdapter) -> None:
    """Endpoint with Depends(oauth2_scheme) has auth_type set."""
    nodes, _ = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    ep_nodes = [n for n in nodes if n.component_type == NodeType.API_ENDPOINT]
    # The /users/{user_id} endpoint has OAuth2 auth
    auth_eps = [n for n in ep_nodes if n.metadata.auth_type is not None]
    assert len(auth_eps) >= 1


def test_calls_edges_created(adapter: FastApiAdapter) -> None:
    """CALLS edges connect AGENT to API_ENDPOINT nodes."""
    nodes, edges = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    call_edges = [e for e in edges if e.relationship_type == EdgeRelationshipType.CALLS]
    assert len(call_edges) >= 1


def test_empty_source_returns_empty(adapter: FastApiAdapter) -> None:
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_invalid_syntax_returns_empty(adapter: FastApiAdapter) -> None:
    nodes, edges = adapter.extract(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []
    assert edges == []


def test_node_ids_stable(adapter: FastApiAdapter) -> None:
    nodes1, _ = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    nodes2, _ = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    assert {n.id for n in nodes1} == {n.id for n in nodes2}


def test_framework_metadata_set(adapter: FastApiAdapter) -> None:
    """Nodes have framework='fastapi' in metadata."""
    nodes, _ = adapter.extract(Path("main.py"), FASTAPI_BASIC_SOURCE)
    for node in nodes:
        assert node.metadata.framework == "fastapi"
