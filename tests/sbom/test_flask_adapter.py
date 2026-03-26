"""Test the Flask framework adapter."""

from __future__ import annotations

import pytest
from pathlib import Path


from nuguard.sbom.models import EdgeRelationshipType, NodeType
try:
    from nuguard.sbom.extractor.framework_adapters.flask import FlaskAdapter
except ImportError:
    pytest.skip("FlaskAdapter not yet ported to nuguard.sbom", allow_module_level=True)


@pytest.fixture
def adapter() -> FlaskAdapter:
    return FlaskAdapter()


FLASK_BASIC_SOURCE = """
from flask import Flask, Blueprint

app = Flask(__name__)
bp = Blueprint("api", __name__)

@app.route("/items", methods=["GET", "POST"])
def items():
    return []

@bp.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    return {"user_id": user_id}
"""

FLASK_AUTH_SOURCE = """
from flask import Flask
from flask_login import login_required
from flask_jwt_extended import jwt_required

app = Flask(__name__)

@app.route("/dashboard")
@login_required
def dashboard():
    return "secret"

@app.route("/api/data")
@jwt_required()
def api_data():
    return {"data": []}
"""

FLASK_FORM_SOURCE = """
from flask import Flask, request

app = Flask(__name__)

@app.route("/api/chat/queue", methods=["POST"])
def queue_chat():
    message = request.form.get("message")
    return {"message": message}
"""

EMPTY_SOURCE = ""
INVALID_SYNTAX = "def foo(:"


def test_flask_app_detected(adapter: FlaskAdapter) -> None:
    """Flask(__name__) is detected as an AGENT node."""
    nodes, _ = adapter.extract(Path("app.py"), FLASK_BASIC_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    assert len(agent_nodes) >= 1
    names = {n.name for n in agent_nodes}
    assert "app" in names


def test_blueprint_detected(adapter: FlaskAdapter) -> None:
    """Blueprint(name, ...) is detected as an AGENT node."""
    nodes, _ = adapter.extract(Path("app.py"), FLASK_BASIC_SOURCE)
    agent_nodes = [n for n in nodes if n.component_type == NodeType.AGENT]
    names = {n.name for n in agent_nodes}
    assert "api" in names or len(agent_nodes) >= 2


def test_route_detected(adapter: FlaskAdapter) -> None:
    """@app.route('/items') is detected as an API_ENDPOINT node."""
    nodes, _ = adapter.extract(Path("app.py"), FLASK_BASIC_SOURCE)
    ep_nodes = [n for n in nodes if n.component_type == NodeType.API_ENDPOINT]
    assert len(ep_nodes) >= 1
    paths = [n.metadata.endpoint for n in ep_nodes]
    assert "/items" in paths


def test_route_methods_extracted(adapter: FlaskAdapter) -> None:
    """methods=['GET', 'POST'] are captured in the endpoint metadata."""
    nodes, _ = adapter.extract(Path("app.py"), FLASK_BASIC_SOURCE)
    ep_nodes = [n for n in nodes if n.component_type == NodeType.API_ENDPOINT]
    items_ep = next((n for n in ep_nodes if n.metadata.endpoint == "/items"), None)
    assert items_ep is not None
    assert items_ep.metadata.method is not None


def test_login_required_decorator_creates_auth_node(adapter: FlaskAdapter) -> None:
    """@login_required decorator creates an AUTH node."""
    nodes, _ = adapter.extract(Path("auth.py"), FLASK_AUTH_SOURCE)
    auth_nodes = [n for n in nodes if n.component_type == NodeType.AUTH]
    assert len(auth_nodes) >= 1


def test_jwt_required_decorator_creates_auth_node(adapter: FlaskAdapter) -> None:
    """@jwt_required() decorator creates an AUTH node."""
    nodes, _ = adapter.extract(Path("auth.py"), FLASK_AUTH_SOURCE)
    auth_nodes = [n for n in nodes if n.component_type == NodeType.AUTH]
    jwt_auth = [n for n in auth_nodes if "jwt" in n.name.lower() or n.metadata.auth_type == "jwt"]
    assert len(jwt_auth) >= 1


def test_calls_edges_created(adapter: FlaskAdapter) -> None:
    """CALLS edges connect AGENT to API_ENDPOINT nodes."""
    nodes, edges = adapter.extract(Path("app.py"), FLASK_BASIC_SOURCE)
    call_edges = [e for e in edges if e.relationship_type == EdgeRelationshipType.CALLS]
    assert len(call_edges) >= 1


def test_empty_source_returns_empty(adapter: FlaskAdapter) -> None:
    nodes, edges = adapter.extract(Path("empty.py"), EMPTY_SOURCE)
    assert nodes == []
    assert edges == []


def test_invalid_syntax_returns_empty(adapter: FlaskAdapter) -> None:
    nodes, edges = adapter.extract(Path("broken.py"), INVALID_SYNTAX)
    assert nodes == []
    assert edges == []


def test_node_ids_stable(adapter: FlaskAdapter) -> None:
    nodes1, _ = adapter.extract(Path("app.py"), FLASK_BASIC_SOURCE)
    nodes2, _ = adapter.extract(Path("app.py"), FLASK_BASIC_SOURCE)
    assert {n.id for n in nodes1} == {n.id for n in nodes2}


def test_framework_metadata_set(adapter: FlaskAdapter) -> None:
    """Flask nodes have framework='flask' in metadata."""
    nodes, _ = adapter.extract(Path("app.py"), FLASK_BASIC_SOURCE)
    for node in nodes:
        assert node.metadata.framework == "flask"


def test_form_payload_key_detected(adapter: FlaskAdapter) -> None:
    """request.form.get('message') is captured as chat_payload_key."""
    nodes, _ = adapter.extract(Path("app.py"), FLASK_FORM_SOURCE)
    endpoint = next(n for n in nodes if n.component_type == NodeType.API_ENDPOINT)
    assert endpoint.metadata.endpoint == "/api/chat/queue"
    assert endpoint.metadata.chat_payload_key == "message"
