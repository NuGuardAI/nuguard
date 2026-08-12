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


# ---------------------------------------------------------------------------
# Path-only path-hint tests (regression for the Windows path-normalization fix)
# ---------------------------------------------------------------------------
#
# These tests use content that does NOT independently trigger Kubernetes/Azure
# detection — only the path-hint match (e.g. ``docker``, ``infra/``) should
# cause the file's URL to flow into ``deployment_urls``. Without the path
# normalization, the raw backslash-separated path never matches the forward
# slash hint and the URL is silently dropped.


def test_windows_style_kubernetes_path_extracts_url_via_path_hint() -> None:
    """A backslash-separated path containing ``/k8s/`` (with leading slash)
    must extract URLs from content that does NOT independently trigger
    Kubernetes detection.

    The content here contains no ``apiVersion:`` and no other Kubernetes
    marker — the URL is only captured because the path-hint match
    (``/k8s/`` substring) fires after path normalization converts the
    backslashes to forward slashes. Without the normalization, the raw
    path ``myapp\\\\k8s\\\\frontend.yaml`` never matches the ``/k8s/``
    hint (which requires a forward-slash separator before ``k8s``).
    """
    files = [
        (
            "myapp\\k8s\\frontend.yaml",
            "# Plain config with a load balancer URL\n"
            "url: https://k8s-lb.example.com/api\n",
        ),
    ]
    result = extract_deployment_context(files)
    assert "https://k8s-lb.example.com/api" in result["deployment_urls"], (
        f"Expected path-hint to surface URL on Windows k8s path; got "
        f"{result['deployment_urls']!r}"
    )


def test_windows_style_infra_path_extracts_url_via_path_hint() -> None:
    """A backslash-separated ``infra`` path must extract URLs from content
    that does NOT independently trigger Azure/Terraform detection.

    The content here contains no ``azurerm_`` resource and no other Azure
    marker — the URL is only captured because the path-hint match
    (``infra/``) fires after path normalization. Without the normalization,
    ``deployment_urls`` would be empty.
    """
    files = [
        (
            "infra\\README.md",
            "README: deploys to https://infra-deploy.example.com/qa\n",
        ),
    ]
    result = extract_deployment_context(files)
    assert "https://infra-deploy.example.com/qa" in result["deployment_urls"], (
        f"Expected path-hint to surface URL on Windows infra path; got "
        f"{result['deployment_urls']!r}"
    )


def test_windows_style_deployment_path_extracts_url_via_path_hint() -> None:
    """A backslash-separated path containing ``infra/`` (with trailing
    forward slash) must extract URLs from content that does NOT
    independently trigger Azure/Terraform detection.

    Pins the path-hint normalization for hints that require a trailing
    forward slash. The content is a plain text file with a URL but no
    platform-specific markers. Without path normalization, the raw
    backslash path ``app\\\\infra\\\\notes.txt`` never matches the
    ``infra/`` hint and the URL is silently dropped.
    """
    files = [
        (
            "app\\infra\\notes.txt",
            "App is deployed at https://app-infra-deploy.example.com/qa\n",
        ),
    ]
    result = extract_deployment_context(files)
    assert "https://app-infra-deploy.example.com/qa" in result["deployment_urls"], (
        f"Expected path-hint to surface URL on Windows infra path; got "
        f"{result['deployment_urls']!r}"
    )


def test_posix_paths_still_work() -> None:
    """Forward-slash paths continue to work after the normalization change."""
    files = [
        (".github/workflows/deploy.yml", _workflow_content()),
    ]
    result = extract_deployment_context(files)
    assert "https://my-backend.azurewebsites.net" in result["deployment_urls"]
