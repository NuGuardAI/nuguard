"""Tests for application_summary path handling.

Verifies that deployment-context extraction works regardless of the path
separator used by the platform that produced the path. On Windows, ``os.walk``
and ``Path.relative_to`` yield backslash-separated paths, so deployment-file
hints defined with forward slashes (``.github/workflows/``, ``/k8s/``,
``infra/``) would silently fail to match and ``deployment_urls`` would end up
empty even when workflow content clearly contains URLs.

See issue #234.
"""

from __future__ import annotations

from nuguard.sbom.core.application_summary import extract_deployment_context


def _workflow_content(url: str = "https://my-backend.azurewebsites.net") -> str:
    return f"""name: Deploy
on: [push]
jobs:
  deploy:
    steps:
      - run: echo {url}
"""


def _azure_workflow_with_env_var() -> str:
    return """name: Deploy
on: [push]
env:
  AZURE_WEBAPP_NAME: my-backend
jobs:
  deploy:
    steps:
      - run: deploy
"""


def test_windows_style_workflow_path_matches_hint() -> None:
    """A backslash-separated path must still trigger the workflow hint."""
    files = [
        (".github\\workflows\\deploy.yml", _workflow_content()),
    ]
    result = extract_deployment_context(files)
    assert "GitHub Actions" in result["deployment_platforms"], (
        f"Expected GitHub Actions detection on Windows path, got "
        f"{result['deployment_platforms']!r}"
    )
    assert "https://my-backend.azurewebsites.net" in result["deployment_urls"]


def test_windows_style_workflow_path_reconstructs_azure_url() -> None:
    """AZURE_WEBAPP_NAME env-var URL reconstruction must work on Windows paths."""
    files = [
        (".github\\workflows\\deploy.yml", _azure_workflow_with_env_var()),
    ]
    result = extract_deployment_context(files)
    assert "https://my-backend.azurewebsites.net" in result["deployment_urls"]


def test_windows_style_kubernetes_path_matches_hint() -> None:
    """A backslash-separated ``k8s`` path must still trigger the Kubernetes hint."""
    files = [
        ("k8s\\deployment.yaml", "apiVersion: apps/v1\nkind: Deployment\n"),
    ]
    result = extract_deployment_context(files)
    assert "Kubernetes" in result["deployment_platforms"]


def test_windows_style_infra_path_matches_hint() -> None:
    """A backslash-separated ``infra`` path must still trigger the deployment hint."""
    files = [
        ("infra\\main.tf", 'resource "azurerm_app_service" "x" {}'),
    ]
    result = extract_deployment_context(files)
    # Terraform / Azure detection should both fire
    assert "Azure" in result["deployment_platforms"]


def test_posix_paths_still_work() -> None:
    """Forward-slash paths continue to work after the normalization change."""
    files = [
        (".github/workflows/deploy.yml", _workflow_content()),
    ]
    result = extract_deployment_context(files)
    assert "https://my-backend.azurewebsites.net" in result["deployment_urls"]
