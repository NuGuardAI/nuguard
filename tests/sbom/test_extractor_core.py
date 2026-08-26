from __future__ import annotations

import pytest

from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.extractor.core import (
    _dedup_by_location,
    _dedup_by_name_prefix,
    _dedup_deployment_nodes,
    _NodeAccumulator,
)
from nuguard.sbom.models import (
    AiSbomDocument,
    Evidence,
    Node,
    NodeMetadata,
    ScanSummary,
    SourceLocation,
)
from nuguard.sbom.types import ComponentType, RelationshipType


def _accumulator(
    canonical_name: str,
    display_name: str,
    *,
    component_type: ComponentType = ComponentType.MODEL,
    priority: int = 110,
    confidence: float = 0.6,
    path: str = "workflow.yml",
    line: int | None = 10,
    kind: str = "regex",
) -> _NodeAccumulator:
    return _NodeAccumulator(
        component_type=component_type,
        canonical_name=canonical_name,
        display_name=display_name,
        adapter_name="model_generic",
        priority=priority,
        confidence=confidence,
        evidence=[
            Evidence(
                kind=kind,
                confidence=confidence,
                detail=f"model_generic: {display_name}",
                location=SourceLocation(path=path, line=line),
            )
        ],
    )


class _FakeLlmClient:
    def __init__(self) -> None:
        self.prompt = ""
        self.system = ""

    async def complete(self, prompt: str, system: str) -> str:
        self.prompt = prompt
        self.system = system
        return "## Security briefing\n\nstub"


def test_iter_files_skips_versioned_virtualenv_directories(tmp_path) -> None:
    source_dir = tmp_path / "sample-app"
    source_dir.mkdir()
    (source_dir / "app.py").write_text("print('ok')\n", encoding="utf-8")

    versioned_venv = source_dir / ".venv39" / "lib" / "python3.9" / "site-packages"
    versioned_venv.mkdir(parents=True)
    (versioned_venv / "vendored.py").write_text("print('skip')\n", encoding="utf-8")

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)

    files = list(AiSbomExtractor._iter_files(source_dir, config))
    names = {path.name for path, _size in files}

    assert "app.py" in names
    assert "vendored.py" not in names


def test_default_config_has_no_file_count_limit() -> None:
    """Local-folder and GitHub scanning share _iter_files; the default config
    must not cap the number of files walked."""
    assert AiSbomConfig().max_files is None


def test_default_config_max_file_size_is_100mb() -> None:
    assert AiSbomConfig().max_file_size_bytes == 100 * 1024 * 1024


def test_iter_files_scans_more_than_1000_files_by_default(tmp_path) -> None:
    source_dir = tmp_path / "sample-app"
    source_dir.mkdir()
    for i in range(1050):
        (source_dir / f"file_{i}.py").write_text("print('ok')\n", encoding="utf-8")

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False)
    files = list(AiSbomExtractor._iter_files(source_dir, config))

    assert len(files) == 1050


def test_iter_files_skips_file_larger_than_100mb(tmp_path) -> None:
    source_dir = tmp_path / "sample-app"
    source_dir.mkdir()
    (source_dir / "small.py").write_text("print('ok')\n", encoding="utf-8")
    big_file = source_dir / "big.py"
    big_file.write_bytes(b"x" * (100 * 1024 * 1024 + 1))

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False)
    files = list(AiSbomExtractor._iter_files(source_dir, config))
    names = {path.name for path, _size in files}

    assert "small.py" in names
    assert "big.py" not in names


def test_iter_files_includes_file_exactly_at_10mb(tmp_path) -> None:
    source_dir = tmp_path / "sample-app"
    source_dir.mkdir()
    exact_file = source_dir / "exact.py"
    exact_file.write_bytes(b"x" * (10 * 1024 * 1024))

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False)
    files = list(AiSbomExtractor._iter_files(source_dir, config))
    names = {path.name for path, _size in files}

    assert "exact.py" in names


@pytest.mark.asyncio
async def test_iac_summary_prompt_uses_stable_template() -> None:
    doc = AiSbomDocument(
        target=".",
        nodes=[
            Node(
                name="Deploy to Azure",
                component_type=ComponentType.DEPLOYMENT,
                confidence=1.0,
                metadata=NodeMetadata(
                    deployment_target="github-actions",
                    secret_store="github_actions_secret",
                    extras={
                        "workflow_triggers": ["push", "workflow_dispatch"],
                        "runners": ["ubuntu-latest"],
                        "uses_oidc": False,
                    },
                ),
            )
        ],
        summary=ScanSummary(
            secret_stores=["github_actions_secret"],
            encryption_at_rest_coverage=False,
            iam_principals=["${{ secrets.AZURE_CREDENTIALS }}"],
        ),
    )
    client = _FakeLlmClient()

    await AiSbomExtractor()._llm_summarize_iac(doc, client)

    assert doc.summary is not None
    assert doc.summary.iac_security_summary == "## Security briefing\n\nstub"
    assert "Use exactly this Markdown structure and heading text" in client.prompt
    assert "### 1) Deployment posture" in client.prompt
    assert "### 7) Top 3 prioritized actions" in client.prompt
    assert "Treat `${{ secrets.NAME }}` and similar placeholders as secret references" in client.prompt
    assert "When data is missing, write `Not evidenced` rather than guessing" in client.prompt
    assert "Follow the requested template exactly" in client.system


def test_dedup_by_location_keeps_distinct_models_at_same_line() -> None:
    """Two unrelated models on one line (e.g. a fallback/matrix declaration)
    must not collapse into a single node — regression for the openclaw
    claude-sonnet-4-6 / claude-haiku-4-5 bug."""
    node_map = {
        (ComponentType.MODEL, "claude_sonnet_4_6"): _accumulator(
            "claude_sonnet_4_6", "claude-sonnet-4-6", priority=110, confidence=0.8
        ),
        (ComponentType.MODEL, "claude_haiku_4_5"): _accumulator(
            "claude_haiku_4_5", "claude-haiku-4-5", priority=110, confidence=0.7
        ),
    }

    _dedup_by_location(node_map)

    assert set(node_map) == {
        (ComponentType.MODEL, "claude_sonnet_4_6"),
        (ComponentType.MODEL, "claude_haiku_4_5"),
    }


def test_dedup_by_location_merges_truncated_model_name() -> None:
    """A truncated regex hit and a fuller variant of the *same* model at the
    same location must still merge (the legitimate case this pass exists for)."""
    node_map = {
        (ComponentType.MODEL, "gpt_5_4"): _accumulator(
            "gpt_5_4", "gpt-5.4", priority=90, confidence=0.6
        ),
        (ComponentType.MODEL, "openai_gpt_5_4"): _accumulator(
            "openai_gpt_5_4", "openai/gpt-5.4", priority=110, confidence=0.55
        ),
    }

    _dedup_by_location(node_map)

    assert set(node_map) == {(ComponentType.MODEL, "gpt_5_4")}
    winner = node_map[(ComponentType.MODEL, "gpt_5_4")]
    assert len(winner.evidence) == 2


def test_dedup_by_location_keeps_generic_deployment_bucket_separate_from_iac_node() -> None:
    """A generic keyword node (e.g. "docker", accumulated from many unrelated
    files across the repo) must not be absorbed into a specific IaC-detected
    node just because the keyword is a coincidental substring of that node's
    slug (e.g. "docker" in "deployment_github_actions_docker_release") and
    happens to share one source line with it. Regression for the openclaw
    "Docker Release" node silently absorbing Vercel/Kustomize/etc. evidence
    from unrelated files via wholesale evidence absorption."""
    docker_bucket = _accumulator(
        "docker",
        "docker",
        component_type=ComponentType.DEPLOYMENT,
        priority=170,
        confidence=0.55,
        path=".github/workflows/docker-release.yml",
        line=1,
    )
    # Evidence from a completely unrelated file — must not get reattributed.
    docker_bucket.evidence.append(
        Evidence(
            kind="regex",
            confidence=0.55,
            detail="deployment_generic: docker",
            location=SourceLocation(path="render.yaml", line=4),
        )
    )
    node_map = {
        (ComponentType.DEPLOYMENT, "docker"): docker_bucket,
        (ComponentType.DEPLOYMENT, "deployment_github_actions_docker_release"): _accumulator(
            "deployment_github_actions_docker_release",
            "Docker Release",
            component_type=ComponentType.DEPLOYMENT,
            priority=50,
            confidence=0.95,
            path=".github/workflows/docker-release.yml",
            line=1,
        ),
    }

    _dedup_by_location(node_map)

    assert set(node_map) == {
        (ComponentType.DEPLOYMENT, "docker"),
        (ComponentType.DEPLOYMENT, "deployment_github_actions_docker_release"),
    }
    assert len(node_map[(ComponentType.DEPLOYMENT, "docker")].evidence) == 2


def test_dedup_deployment_nodes_keeps_uncovered_generic_technologies() -> None:
    """_dedup_deployment_nodes must only drop a generic keyword node (e.g.
    "docker") when a specific, non-generic DEPLOYMENT node already names that
    same technology — not just because *some* GitHub Actions workflow node
    exists somewhere in the repo. Regression: this was the actual mechanism
    that wiped out every deployment_generic node (Vercel, Kustomize, Compose,
    ...) from the final openclaw SBOM even after the two dedup passes were
    fixed to keep them separate in node_map."""
    docker_release = Node(
        name="Docker Release",
        component_type=ComponentType.DEPLOYMENT,
        confidence=0.95,
        metadata=NodeMetadata(
            extras={
                "canonical_name": "deployment_github_actions_docker_release",
                "adapter": "github_actions",
                "deployment_target": "github-actions",
            }
        ),
    )
    docker_generic = Node(
        name="docker",
        component_type=ComponentType.DEPLOYMENT,
        confidence=0.55,
        metadata=NodeMetadata(extras={"canonical_name": "docker", "adapter": "deployment_generic"}),
    )
    vercel_generic = Node(
        name="vercel",
        component_type=ComponentType.DEPLOYMENT,
        confidence=0.55,
        metadata=NodeMetadata(extras={"canonical_name": "vercel", "adapter": "deployment_generic"}),
    )
    doc = AiSbomDocument(target=".", nodes=[docker_release, docker_generic, vercel_generic])

    _dedup_deployment_nodes(doc)

    remaining = {n.name for n in doc.nodes}
    assert remaining == {"Docker Release", "vercel"}


def test_dedup_by_name_prefix_keeps_generic_deployment_bucket_separate() -> None:
    """A generic keyword node ("docker") is a word-boundary-respecting prefix
    of a specific IaC workflow name ("Docker Release") but must not merge —
    they are unrelated entities that happen to share a leading word.
    Regression: this was the actual mechanism absorbing the openclaw
    "docker" bucket's cross-repo evidence into the "Docker Release" node
    (the location-based guard alone did not prevent it)."""
    node_map = {
        (ComponentType.DEPLOYMENT, "docker"): _accumulator(
            "docker",
            "docker",
            component_type=ComponentType.DEPLOYMENT,
            priority=170,
            confidence=0.55,
            path=".github/workflows/docker-release.yml",
            line=1,
        ),
        (ComponentType.DEPLOYMENT, "deployment_github_actions_docker_release"): _accumulator(
            "deployment_github_actions_docker_release",
            "Docker Release",
            component_type=ComponentType.DEPLOYMENT,
            priority=50,
            confidence=0.95,
            path=".github/workflows/docker-release.yml",
            line=1,
        ),
    }

    _dedup_by_name_prefix(node_map)

    assert set(node_map) == {
        (ComponentType.DEPLOYMENT, "docker"),
        (ComponentType.DEPLOYMENT, "deployment_github_actions_docker_release"),
    }


def test_dedup_by_name_prefix_merges_legit_truncation() -> None:
    """A regex-truncated name and the fuller AST-derived name of the same
    entity should still merge when the prefix ends on a delimiter boundary."""
    node_map = {
        (ComponentType.MODEL, "gemini_2_0"): _accumulator(
            "gemini_2_0", "gemini-2.0", priority=110, confidence=0.6
        ),
        (ComponentType.MODEL, "gemini_2_0_flash"): _accumulator(
            "gemini_2_0_flash", "gemini-2.0-flash", priority=90, confidence=0.7
        ),
    }

    _dedup_by_name_prefix(node_map)

    assert set(node_map) == {(ComponentType.MODEL, "gemini_2_0_flash")}


def test_dedup_by_name_prefix_rejects_numeric_id_collision() -> None:
    """Auto-generated numeric-ID names must not merge just because one is a
    raw string-prefix of another — regression for the openclaw
    prompt_407 / prompt_4078 bug (two unrelated string literals whose
    line-number-derived names happen to overlap digit-for-digit)."""
    node_map = {
        (ComponentType.PROMPT, "prompt_407"): _accumulator(
            "prompt_407",
            "Prompt 407",
            component_type=ComponentType.PROMPT,
            priority=120,
            confidence=0.55,
            path="a2ui.bundle.js",
            line=407,
        ),
        (ComponentType.PROMPT, "prompt_4078"): _accumulator(
            "prompt_4078",
            "Prompt 4078",
            component_type=ComponentType.PROMPT,
            priority=120,
            confidence=0.55,
            path="a2ui.bundle.js",
            line=4078,
        ),
    }

    _dedup_by_name_prefix(node_map)

    assert set(node_map) == {
        (ComponentType.PROMPT, "prompt_407"),
        (ComponentType.PROMPT, "prompt_4078"),
    }


def test_deployment_generic_adapter_separates_distinct_technologies(tmp_path) -> None:
    """A file mentioning two unrelated deployment technologies must produce
    two distinct DEPLOYMENT nodes, not one bucket that hides which
    technologies are actually in use — regression for the openclaw
    'Docker Release' node silently absorbing Vercel/Kustomize evidence."""
    source_dir = tmp_path / "sample-app"
    source_dir.mkdir()
    (source_dir / "deploy.py").write_text(
        'DEPLOY_NOTES = "Build with docker compose, then ship the frontend via vercel."\n',
        encoding="utf-8",
    )

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)
    doc = AiSbomExtractor().extract_from_path(source_dir, config)

    deployment_names = {
        node.name.lower() for node in doc.nodes if node.component_type == ComponentType.DEPLOYMENT
    }
    assert "docker" in deployment_names
    assert "vercel" in deployment_names
    assert "generic" not in deployment_names


def test_api_endpoint_generic_adapter_separates_distinct_routes(tmp_path) -> None:
    """A file with several distinct routes not recognized by any framework
    AST adapter must produce one API_ENDPOINT node per route, not a single
    'Generic' bucket node that hides which paths exist and merges unrelated
    evidence (regression: a doc-comment mentioning 'POST /api/chat' was
    getting merged into the same node as an unrelated '@router.get(' hit)."""
    source_dir = tmp_path / "sample-app"
    source_dir.mkdir()
    (source_dir / "routes.js").write_text(
        "app.get('/health', handler);\n"
        "app.post('/webhooks/register', handler);\n",
        encoding="utf-8",
    )

    config = AiSbomConfig(include_extensions={".js"}, enable_llm=False, max_files=20)
    doc = AiSbomExtractor().extract_from_path(source_dir, config)

    endpoint_nodes = [n for n in doc.nodes if n.component_type == ComponentType.API_ENDPOINT]
    paths = {n.metadata.endpoint for n in endpoint_nodes}
    assert paths == {"/health", "/webhooks/register"}
    assert "generic" not in {n.name.lower() for n in endpoint_nodes}


def test_api_endpoint_generic_and_fastapi_adapter_merge_same_route(tmp_path) -> None:
    """The same route detected by both the FastAPI AST adapter (which infers
    request_body_schema) and the generic regex fallback (evidence-only) must
    collapse into a single node, keeping the AST-derived schema."""
    source_dir = tmp_path / "sample-app"
    source_dir.mkdir()
    (source_dir / "main.py").write_text(
        "from fastapi import FastAPI\n"
        "from pydantic import BaseModel\n\n"
        "app = FastAPI()\n\n"
        "class ChatRequest(BaseModel):\n"
        "    message: str\n\n"
        "@app.post('/api/chat')\n"
        "async def chat(body: ChatRequest):\n"
        "    return {'reply': 'hi'}\n",
        encoding="utf-8",
    )

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)
    doc = AiSbomExtractor().extract_from_path(source_dir, config)

    endpoint_nodes = [
        n
        for n in doc.nodes
        if n.component_type == ComponentType.API_ENDPOINT and n.metadata.endpoint == "/api/chat"
    ]
    assert len(endpoint_nodes) == 1
    assert endpoint_nodes[0].metadata.request_body_schema == {"message": "str"}


def test_summary_api_endpoints_all_have_matching_nodes(tmp_path) -> None:
    """Every path nuguard reports in summary.api_endpoints must be backed by
    an API_ENDPOINT node — otherwise downstream consumers of the node graph
    (analyze, redteam scenario generation) silently miss routes the summary
    claims exist."""
    source_dir = tmp_path / "sample-app"
    source_dir.mkdir()
    (source_dir / "main.py").write_text(
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/api/health')\n"
        "async def health():\n"
        "    return {'status': 'ok'}\n",
        encoding="utf-8",
    )
    (source_dir / "auth_routes.py").write_text(
        "from fastapi import APIRouter\n\n"
        "router = APIRouter()\n\n"
        "@router.post('/api/auth/login')\n"
        "async def login():\n"
        "    return {'token': 'x'}\n",
        encoding="utf-8",
    )

    config = AiSbomConfig(include_extensions={".py"}, enable_llm=False, max_files=20)
    doc = AiSbomExtractor().extract_from_path(source_dir, config)

    node_paths = {
        n.metadata.endpoint for n in doc.nodes if n.component_type == ComponentType.API_ENDPOINT
    }
    assert doc.summary is not None
    for path in doc.summary.api_endpoints:
        assert path in node_paths, f"{path} in summary.api_endpoints has no matching node"


def _node(
    name: str,
    component_type: ComponentType,
    *,
    evidence_paths: list[str] | None = None,
    extras: dict | None = None,
) -> Node:
    evidence = [
        Evidence(kind="regex", confidence=0.6, detail=name, location=SourceLocation(path=p, line=1))
        for p in (evidence_paths or [])
    ]
    return Node(
        name=name,
        component_type=component_type,
        confidence=0.6,
        metadata=NodeMetadata(extras=extras or {}),
        evidence=evidence,
    )


def test_deployment_container_image_edges_gated_by_file_relatedness() -> None:
    """Regression for the "N*M cross join" bug: a scan with several
    keyword-derived DEPLOYMENT nodes (Compose, Ci, Docker — all cited from
    unrelated root-level files) and multiple CONTAINER_IMAGE nodes must not
    connect every DEPLOYMENT to every CONTAINER_IMAGE. Only pairs sharing an
    evidence file (or non-root directory) should get a DEPLOYS edge."""
    compose = _node("Compose", ComponentType.DEPLOYMENT, evidence_paths=["docker-compose.yml"])
    ci = _node("Ci", ComponentType.DEPLOYMENT, evidence_paths=["ci.yml"])
    docker = _node("Docker", ComponentType.DEPLOYMENT, evidence_paths=["Dockerfile"])
    backend_image = _node("backend-image", ComponentType.CONTAINER_IMAGE, evidence_paths=["Dockerfile"])
    frontend_image = _node(
        "frontend-image", ComponentType.CONTAINER_IMAGE, evidence_paths=["frontend/Dockerfile"]
    )

    doc = AiSbomDocument(
        target=".",
        nodes=[compose, ci, docker, backend_image, frontend_image],
        summary=ScanSummary(),
    )
    AiSbomExtractor()._resolve_edges(doc, {})

    deploys_edges = [e for e in doc.edges if e.relationship_type == RelationshipType.DEPLOYS]
    # Only "Docker" (evidenced at Dockerfile) shares a file with backend_image
    # (also evidenced at Dockerfile) — no pair should relate to frontend_image
    # (a different, unrelated Dockerfile), and "Compose"/"Ci" (root-level,
    # no shared file/dir/name token with either image) should get none.
    assert len(deploys_edges) < 3 * 2
    assert {(e.source, e.target) for e in deploys_edges} == {(docker.id, backend_image.id)}


def test_auth_protects_endpoint_prefers_matching_auth_type() -> None:
    """AUTH -> API_ENDPOINT PROTECTS edges should prefer the precise
    metadata.extras["auth_type"] signal (populated by fastapi_adapter on
    both the AUTH node and the endpoints it guards) over the old broad
    "every AUTH node protects the first 10 endpoints alphabetically"
    fallback, which wired unrelated auth schemes to unrelated endpoints."""
    oauth = _node("OAuth2", ComponentType.AUTH, extras={"auth_type": "oauth2"})
    bearer = _node("Bearer", ComponentType.AUTH, extras={"auth_type": "http_bearer"})
    oauth_endpoint = _node(
        "GET /profile", ComponentType.API_ENDPOINT, extras={"auth_type": "oauth2"}
    )
    bearer_endpoint = _node(
        "GET /admin", ComponentType.API_ENDPOINT, extras={"auth_type": "http_bearer"}
    )

    doc = AiSbomDocument(
        target=".",
        nodes=[oauth, bearer, oauth_endpoint, bearer_endpoint],
        summary=ScanSummary(),
    )
    AiSbomExtractor()._resolve_edges(doc, {})

    protects = {
        (e.source, e.target) for e in doc.edges if e.relationship_type == RelationshipType.PROTECTS
    }
    assert protects == {
        (oauth.id, oauth_endpoint.id),
        (bearer.id, bearer_endpoint.id),
    }


def test_auth_protects_endpoint_falls_back_when_no_auth_type_signal() -> None:
    """When endpoints carry no auth_type metadata at all (e.g. a framework
    without fastapi_adapter's per-endpoint auth-scheme detection), fall back
    to the broad top-10 behavior rather than producing zero edges."""
    auth = _node("JWT Auth", ComponentType.AUTH, extras={})
    endpoints = [_node(f"GET /r{i}", ComponentType.API_ENDPOINT) for i in range(3)]

    doc = AiSbomDocument(target=".", nodes=[auth, *endpoints], summary=ScanSummary())
    AiSbomExtractor()._resolve_edges(doc, {})

    protects = [e for e in doc.edges if e.relationship_type == RelationshipType.PROTECTS]
    assert len(protects) == 3
