"""Test the Docker Compose scanner."""

from __future__ import annotations


import tempfile
from pathlib import Path


from nuguard.models.sbom import NodeType
try:
    from nuguard.sbom.extractor.iac_scanners.docker_compose import DockerComposeScanner
except ImportError:
    import pytest
    pytest.skip("DockerComposeScanner not yet ported to nuguard.sbom", allow_module_level=True)

DOCKER_COMPOSE_BASIC = """
version: "3.8"
services:
  web:
    image: nginx:1.24
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost"]
    deploy:
      resources:
        limits:
          cpus: "0.5"
          memory: 256M
  db:
    image: postgres:16
"""

DOCKER_COMPOSE_ROOT = """
version: "3.8"
services:
  app:
    image: myapp:latest
    user: root
"""

DOCKER_COMPOSE_NO_HEALTHCHECK = """
version: "3.8"
services:
  app:
    image: myapp:latest
"""

DOCKER_COMPOSE_SECRETS_ENV = """
version: "3.8"
services:
  app:
    image: myapp:latest
    environment:
      DB_PASSWORD: "supersecret"
      SECRET_KEY: "my-secret-key"
      API_TOKEN: "some-token"
"""

DOCKER_COMPOSE_LOCAL_BUILD = """
version: "3.8"
services:
  myservice:
    build: .
"""


@pytest.fixture
def scanner() -> DockerComposeScanner:
    return DockerComposeScanner()


def _write_yaml(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".yml", delete=False, mode="w", prefix="docker-compose-")
    f.write(content)
    f.close()
    return Path(f.name)


def test_container_image_node_created(scanner: DockerComposeScanner) -> None:
    """docker-compose service with image: creates a CONTAINER_IMAGE node."""
    path = _write_yaml(DOCKER_COMPOSE_BASIC)
    nodes, findings = scanner.scan(path)
    img_nodes = [n for n in nodes if n.component_type == NodeType.CONTAINER_IMAGE]
    assert len(img_nodes) >= 2


def test_image_name_tag_extracted(scanner: DockerComposeScanner) -> None:
    """image: nginx:1.24 → image_name='nginx', image_tag='1.24'."""
    path = _write_yaml(DOCKER_COMPOSE_BASIC)
    nodes, _ = scanner.scan(path)
    nginx_node = next((n for n in nodes if n.name == "nginx"), None)
    assert nginx_node is not None
    assert nginx_node.metadata.image_tag == "1.24"


def test_runs_as_root_finding(scanner: DockerComposeScanner) -> None:
    """user: root → 'container_runs_as_root' finding."""
    path = _write_yaml(DOCKER_COMPOSE_ROOT)
    nodes, findings = scanner.scan(path)
    assert "container_runs_as_root" in findings


def test_missing_healthcheck_finding(scanner: DockerComposeScanner) -> None:
    """No healthcheck → 'missing_health_check' finding."""
    path = _write_yaml(DOCKER_COMPOSE_NO_HEALTHCHECK)
    nodes, findings = scanner.scan(path)
    assert "missing_health_check" in findings


def test_secrets_in_env_finding(scanner: DockerComposeScanner) -> None:
    """Env var with PASSWORD/SECRET/TOKEN name → 'secrets_in_env_vars' finding."""
    path = _write_yaml(DOCKER_COMPOSE_SECRETS_ENV)
    nodes, findings = scanner.scan(path)
    assert "secrets_in_env_vars" in findings


def test_local_build_creates_node(scanner: DockerComposeScanner) -> None:
    """build: . service creates a CONTAINER_IMAGE node with service name."""
    path = _write_yaml(DOCKER_COMPOSE_LOCAL_BUILD)
    nodes, _ = scanner.scan(path)
    img_nodes = [n for n in nodes if n.component_type == NodeType.CONTAINER_IMAGE]
    assert len(img_nodes) >= 1
    assert any(n.name == "myservice" for n in img_nodes)


def test_no_resource_limits_finding(scanner: DockerComposeScanner) -> None:
    """Service without resource limits → 'no_resource_limits' finding."""
    path = _write_yaml(DOCKER_COMPOSE_NO_HEALTHCHECK)
    nodes, findings = scanner.scan(path)
    assert "no_resource_limits" in findings


def test_healthy_service_no_findings(scanner: DockerComposeScanner) -> None:
    """Well-configured service should not have container_runs_as_root."""
    # The nginx service in BASIC has healthcheck and resource limits
    path = _write_yaml(DOCKER_COMPOSE_BASIC)
    nodes, findings = scanner.scan(path)
    assert "container_runs_as_root" not in findings


def test_findings_deduplicated(scanner: DockerComposeScanner) -> None:
    """Findings list should not contain duplicates."""
    path = _write_yaml(DOCKER_COMPOSE_SECRETS_ENV)
    _, findings = scanner.scan(path)
    assert len(findings) == len(set(findings))
