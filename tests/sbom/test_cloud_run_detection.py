"""Tests for GCP Cloud Run deployment detection (issue #220).

Issue #220 reports that nuguard's discovery under-detects GCP Cloud Run
deployments. This file pins the current Cloud Run-specific markers so
that:

* The Cloud Run Kubernetes-style service YAML (``apiVersion:
  serving.knative.dev/v1``) is recognised as both "GCP" and
  "Cloud Run".
* GitHub Actions workflows that use
  ``google-github-actions/deploy-cloudrun`` are recognised as
  "Cloud Run" + "GCP" + "GitHub Actions".
* Cloud Run runtime URLs (``*.a.run.app``) inside workflow files are
  captured as deployment URLs.
* File names containing ``cloudrun`` (no extension) trigger the
  Cloud Run platform marker.

The platform list is also asserted to remain deduplicated when the
file content triggers both the coarse "GCP" and the specific
"Cloud Run" markers.
"""

from __future__ import annotations

from nuguard.sbom.core.application_summary import extract_deployment_context

# ---------------------------------------------------------------------------
# Cloud Run Kubernetes-style service manifest
# ---------------------------------------------------------------------------


def test_cloudrun_manifest_recognises_cloud_run_platform() -> None:
    """A Knative Service YAML for Cloud Run must surface "Cloud Run"."""
    files = [
        (
            "cloudrun.yaml",
            """\
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: my-ai-service
  annotations:
    run.googleapis.com/launch-stage: BETA
spec:
  template:
    spec:
      containers:
      - image: gcr.io/my-project/my-ai-service:latest
        env:
        - name: GOOGLE_CLOUD_PROJECT
          value: my-project
""",
        ),
    ]
    result = extract_deployment_context(files)
    platforms = result["deployment_platforms"]
    assert "Cloud Run" in platforms, (
        f"Expected 'Cloud Run' platform marker, got {platforms!r}"
    )
    # The coarse GCP marker should also be present.
    assert "GCP" in platforms


def test_cloudrun_manifest_does_not_duplicate_platforms() -> None:
    """Each platform appears at most once, regardless of how many triggers fire."""
    files = [
        (
            "cloudrun.yaml",
            """\
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: x
  annotations:
    run.googleapis.com/launch-stage: BETA
spec:
  template:
    spec:
      containers:
      - image: gcr.io/proj/img
        env:
        - name: GOOGLE_CLOUD_PROJECT
          value: my-project
""",
        ),
    ]
    result = extract_deployment_context(files)
    platforms = result["deployment_platforms"]
    # Each platform appears at most once.
    assert platforms.count("Cloud Run") == 1
    assert platforms.count("GCP") == 1
    assert platforms.count("Kubernetes") == 1


# ---------------------------------------------------------------------------
# GitHub Actions deploy-cloudrun
# ---------------------------------------------------------------------------


def test_deploy_cloudrun_workflow_surfaces_cloud_run_platform() -> None:
    """``google-github-actions/deploy-cloudrun`` must add 'Cloud Run'."""
    files = [
        (
            ".github/workflows/deploy.yml",
            """\
name: Deploy
on: [push]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: google-github-actions/auth@v2
        with:
          service_account_key_file: ${{ secrets.GCP_SA_KEY }}
      - uses: google-github-actions/deploy-cloudrun@v2
        with:
          service: my-ai-service
          image: gcr.io/my-project/my-ai-service:latest
          region: us-central1
""",
        ),
    ]
    result = extract_deployment_context(files)
    platforms = result["deployment_platforms"]
    assert "Cloud Run" in platforms
    assert "GCP" in platforms
    assert "GitHub Actions" in platforms


def test_deploy_cloudrun_workflow_captures_run_app_url() -> None:
    """A ``*.a.run.app`` URL inside the workflow must be a deployment_url."""
    files = [
        (
            ".github/workflows/deploy.yml",
            """\
name: Deploy
on: [push]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: google-github-actions/deploy-cloudrun@v2
        with:
          service: my-ai-service
          image: gcr.io/my-project/my-ai-service:latest
          region: us-central1
      - run: |
          echo "Deployed to https://my-ai-service-abc123-uc.a.run.app"
""",
        ),
    ]
    result = extract_deployment_context(files)
    urls = result["deployment_urls"]
    assert any(u.endswith(".a.run.app") for u in urls), (
        f"Expected a *.a.run.app URL, got {urls!r}"
    )


# ---------------------------------------------------------------------------
# Filename-only trigger
# ---------------------------------------------------------------------------


def test_cloudrun_in_path_triggers_marker() -> None:
    """A file path containing 'cloudrun' triggers the Cloud Run platform."""
    files = [
        (
            "deploy/cloudrun/service.yaml",
            "kind: Service\nmetadata:\n  name: x\n",
        ),
    ]
    result = extract_deployment_context(files)
    platforms = result["deployment_platforms"]
    assert "Cloud Run" in platforms


# ---------------------------------------------------------------------------
# Negative: ensure the marker is NOT added for plain Kubernetes manifests
# ---------------------------------------------------------------------------


def test_plain_kubernetes_manifest_is_not_marked_as_cloud_run() -> None:
    """A non-Cloud-Run Kubernetes manifest must not add 'Cloud Run'."""
    files = [
        (
            "k8s/deployment.yaml",
            """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  replicas: 3
""",
        ),
    ]
    result = extract_deployment_context(files)
    platforms = result["deployment_platforms"]
    assert "Cloud Run" not in platforms, (
        f"Expected plain Kubernetes, not Cloud Run; got {platforms!r}"
    )
    assert "Kubernetes" in platforms