"""Tests for GCP Cloud Run deployment detection (issue #220).

Issue #220 reports that nuguard's discovery under-detects GCP Cloud Run
deployments. This file pins the current Cloud Run-specific markers so
that:

* The ``google-github-actions/deploy-cloudrun`` GitHub Action,
  ``run.googleapis.com`` annotations, ``*.a.run.app`` URLs, and the
  ``cloudrun`` substring anywhere in a path are standalone Cloud Run
  triggers, and any Cloud Run signal also implies the coarse "GCP"
  marker.
* The Knative service API (``apiVersion: serving.knative.dev/v1``) alone
  is intentionally NOT a Cloud Run trigger — Knative Serving is open
  source and commonly self-hosted, so it requires a Cloud Run co-signal
  (e.g. ``run.googleapis.com`` / ``*.a.run.app``) to be classified as
  Cloud Run.
* GitHub Actions workflows that use
  ``google-github-actions/deploy-cloudrun`` are recognised as
  "Cloud Run" + "GCP" + "GitHub Actions".
* Cloud Run runtime URLs (``*.a.run.app``) inside workflow files are
  captured as deployment URLs.

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


# ---------------------------------------------------------------------------
# Knative co-signal requirement (reviewer nit on PR #249)
# ---------------------------------------------------------------------------


def test_self_hosted_knative_service_is_not_marked_as_cloud_run() -> None:
    """A Knative Service YAML without any Cloud Run co-signal must NOT be marked 'Cloud Run'.

    Knative Serving is open source and commonly self-hosted on plain GKE/EKS/AKS,
    so the Knative service API (``serving.knative.dev/v1``) alone is not
    Cloud-Run-specific. Without a co-signal like a ``run.googleapis.com``
    annotation or a ``*.a.run.app`` runtime URL, the scan must surface
    only ``Kubernetes`` (and ``GCP`` if the cluster is on GCP), not
    ``Cloud Run``.
    """
    files = [
        (
            "k8s/knative-service.yaml",
            """\
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: self-hosted-ai
  namespace: default
spec:
  template:
    spec:
      containers:
      - image: registry.example.com/my-org/ai:latest
""",
        ),
    ]
    result = extract_deployment_context(files)
    platforms = result["deployment_platforms"]
    assert "Cloud Run" not in platforms, (
        f"Self-hosted Knative should not be marked 'Cloud Run'; got {platforms!r}"
    )
    assert "Kubernetes" in platforms


def test_knative_service_with_run_app_url_is_marked_as_cloud_run() -> None:
    """A Knative Service YAML that mentions a ``*.a.run.app`` URL must be marked 'Cloud Run'.

    Pins the positive case where the Knative service API (a weak signal)
    appears in one file and a Cloud Run ``*.a.run.app`` URL (a strong
    Cloud Run-specific marker) appears in a separate file. The URL is a
    standalone Cloud Run trigger, so the marker fires regardless of whether
    the Knative API is present.
    """
    files = [
        (
            "cloudrun/service.yaml",
            """\
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: ai-service
spec:
  template:
    spec:
      containers:
      - image: gcr.io/my-project/ai:latest
""",
        ),
        (
            "docs/url.txt",
            "Service is reachable at https://my-ai-service-xyz.a.run.app",
        ),
    ]
    result = extract_deployment_context(files)
    platforms = result["deployment_platforms"]
    assert "Cloud Run" in platforms, (
        f"Knative + *.a.run.app should mark 'Cloud Run'; got {platforms!r}"
    )

# ---------------------------------------------------------------------------
# Standalone Cloud Run signal triggers (issue #220)
# ---------------------------------------------------------------------------


def test_run_app_url_alone_marks_cloud_run_and_implies_gcp() -> None:
    """A bare ``*.a.run.app`` URL is itself a Cloud Run signal (issue #220).

    Pins the contract that ``run.googleapis.com``, ``*.a.run.app``, and
    the ``deploy-cloudrun`` GH Action are standalone Cloud Run triggers
    (not just co-signals that require the Knative service API alongside).

    A file containing only a ``*.a.run.app`` URL with no other GCP marker
    must still surface both ``Cloud Run`` and ``GCP`` — downstream code
    relying on the coarse ``GCP`` marker keeps working.
    """
    files = [
        (
            "docs/url.txt",
            "Service is reachable at https://my-ai-service-xyz.a.run.app",
        ),
    ]
    result = extract_deployment_context(files)
    platforms = result["deployment_platforms"]
    assert "Cloud Run" in platforms, (
        f"*.a.run.app URL alone should mark 'Cloud Run'; got {platforms!r}"
    )
    assert "GCP" in platforms, (
        f"Cloud Run should imply GCP; got {platforms!r}"
    )


def test_run_googleapis_api_annotation_alone_marks_cloud_run() -> None:
    """A bare ``run.googleapis.com`` annotation is itself a Cloud Run signal.

    The Cloud Run runtime API annotation is specific to Cloud Run (issue
    #220), even when no other GCP/Cloud Run marker is present in the file.
    """
    files = [
        (
            "service.yaml",
            """\
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: ai-service
  annotations:
    run.googleapis.com/launch-stage: BETA
spec:
  template:
    spec:
      containers:
      - image: gcr.io/proj/img:tag
""",
        ),
    ]
    result = extract_deployment_context(files)
    platforms = result["deployment_platforms"]
    assert "Cloud Run" in platforms, (
        f"run.googleapis.com annotation alone should mark 'Cloud Run'; got {platforms!r}"
    )
    assert "GCP" in platforms, (
        f"Cloud Run should imply GCP; got {platforms!r}"
    )
