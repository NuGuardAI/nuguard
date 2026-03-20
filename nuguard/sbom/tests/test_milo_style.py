"""Tests for the milo-style fixture.

Validates all new adapters added to improve coverage of:
- OpenAI SDK used as a universal provider proxy (base_url pattern)
- aiosqlite / SQLite detection
- Nginx config: proxy_pass → DEPLOYMENT, ssl → AUTH
- Dockerfile: EXPOSE → API_ENDPOINT, RUN playwright install → TOOL
- Prompt template files in prompts/*.txt
- Generic LLM YAML config (config/llm.yaml providers block)
"""

from __future__ import annotations


import pytest

from xelo.config import AiSbomConfig
from xelo.extractor import AiSbomExtractor
from xelo.models import AiSbomDocument
from xelo.types import ComponentType
from conftest import APPS, nodes

MILO = APPS / "milo_style"

# Config that includes all relevant file types for this fixture
_MILO_CONFIG = AiSbomConfig(
    include_extensions={".py", ".yaml", ".yml", ".txt", ".conf"},
    enable_llm=False,
)


@pytest.fixture(scope="module")
def doc() -> AiSbomDocument:
    return AiSbomExtractor().extract_from_path(MILO, _MILO_CONFIG)


# ---------------------------------------------------------------------------
# LLM provider / model detection
# ---------------------------------------------------------------------------


class TestLLMProviders:
    def test_groq_provider_detected(self, doc: AiSbomDocument) -> None:
        framework_names = {n.name.lower() for n in nodes(doc, ComponentType.FRAMEWORK)}
        model_providers = {
            n.metadata.extras.get("provider", "").lower() for n in nodes(doc, ComponentType.MODEL)
        }
        assert "groq" in framework_names or "groq" in model_providers, (
            f"Expected groq detected. frameworks={framework_names} providers={model_providers}"
        )

    def test_ollama_model_detected(self, doc: AiSbomDocument) -> None:
        model_names = {n.name.lower() for n in nodes(doc, ComponentType.MODEL)}
        # llama3.2:3b comes from YAML config; llama-3.3-70b from YAML + python
        assert any("llama" in m for m in model_names), f"Expected a llama model, got: {model_names}"

    def test_gemini_model_detected(self, doc: AiSbomDocument) -> None:
        model_names = {n.name.lower() for n in nodes(doc, ComponentType.MODEL)}
        assert any("gemini" in m for m in model_names), (
            f"Expected a gemini model, got: {model_names}"
        )

    def test_yaml_config_adapter_fires(self, doc: AiSbomDocument) -> None:
        adapters_used = {n.metadata.extras.get("adapter") for n in doc.nodes}
        assert "llm_yaml_config" in adapters_used, (
            f"LLMYAMLConfigAdapter not in adapters: {adapters_used}"
        )


# ---------------------------------------------------------------------------
# Datastore detection
# ---------------------------------------------------------------------------


class TestDatastore:
    def test_sqlite_detected(self, doc: AiSbomDocument) -> None:
        ds_names = {n.name.lower() for n in nodes(doc, ComponentType.DATASTORE)}
        assert any("sqlite" in n or "aiosqlite" in n for n in ds_names), (
            f"Expected aiosqlite/sqlite datastore. Got: {ds_names}"
        )


# ---------------------------------------------------------------------------
# Nginx adapter
# ---------------------------------------------------------------------------


class TestNginx:
    def test_deployment_from_proxy_pass(self, doc: AiSbomDocument) -> None:
        deployments = nodes(doc, ComponentType.DEPLOYMENT)
        upstream_urls = [n.metadata.extras.get("upstream_url", "") for n in deployments]
        assert any("127.0.0.1:8420" in u or "localhost" in u for u in upstream_urls), (
            f"Expected nginx proxy_pass deployment. upstream_urls={upstream_urls}"
        )

    def test_tls_auth_from_nginx(self, doc: AiSbomDocument) -> None:
        auth_nodes = nodes(doc, ComponentType.AUTH)
        tls_nodes = [
            n
            for n in auth_nodes
            if n.metadata.extras.get("auth_kind") == "tls"
            or "tls" in n.name.lower()
            or "ssl" in n.name.lower()
        ]
        assert tls_nodes, (
            f"Expected TLS AUTH node from nginx. auth_nodes={[n.name for n in auth_nodes]}"
        )

    def test_nginx_adapter_used(self, doc: AiSbomDocument) -> None:
        adapters_used = {n.metadata.extras.get("adapter") for n in doc.nodes}
        assert "nginx" in adapters_used, f"NginxAdapter not in adapters: {adapters_used}"


# ---------------------------------------------------------------------------
# Dockerfile adapter
# ---------------------------------------------------------------------------


class TestDockerfile:
    def test_container_image_detected(self, doc: AiSbomDocument) -> None:
        images = nodes(doc, ComponentType.CONTAINER_IMAGE)
        assert images, "Expected at least one CONTAINER_IMAGE node from Dockerfile"
        image_names = {n.name.lower() for n in images}
        assert any("python" in n for n in image_names), (
            f"Expected python base image. Got: {image_names}"
        )

    def test_expose_port_detected(self, doc: AiSbomDocument) -> None:
        endpoints = nodes(doc, ComponentType.API_ENDPOINT)
        port_nodes = [
            n for n in endpoints if "8420" in n.name or n.metadata.extras.get("port") == 8420
        ]
        assert port_nodes, (
            f"Expected API_ENDPOINT for port 8420 from EXPOSE. "
            f"endpoints={[n.name for n in endpoints]}"
        )

    def test_playwright_tool_from_dockerfile(self, doc: AiSbomDocument) -> None:
        tool_nodes = nodes(doc, ComponentType.TOOL)
        playwright_nodes = [n for n in tool_nodes if "playwright" in n.name.lower()]
        assert playwright_nodes, (
            f"Expected Playwright TOOL node from Dockerfile RUN instruction. "
            f"tools={[n.name for n in tool_nodes]}"
        )


# ---------------------------------------------------------------------------
# Prompt file adapter
# ---------------------------------------------------------------------------


class TestPromptFiles:
    def test_system_prompt_detected(self, doc: AiSbomDocument) -> None:
        prompt_nodes = nodes(doc, ComponentType.PROMPT)
        assert prompt_nodes, "Expected at least one PROMPT node from prompts/*.txt"

    def test_prompt_is_template(self, doc: AiSbomDocument) -> None:
        for pn in nodes(doc, ComponentType.PROMPT):
            if pn.metadata.extras.get("is_template"):
                return  # at least one prompt has template vars — pass
        pytest.fail("Expected at least one prompt node with is_template=True")

    def test_prompt_adapter_used(self, doc: AiSbomDocument) -> None:
        adapters_used = {n.metadata.extras.get("adapter") for n in doc.nodes}
        assert "prompt_file" in adapters_used, f"prompt_file adapter not in {adapters_used}"
