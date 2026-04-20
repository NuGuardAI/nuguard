"""Tests for deployment_generic adapter and shell-script scanning.

Covers:
- .sh files are scanned by regex adapters (not silently skipped as docs)
- gcloud, kubectl, az, ansible, pulumi patterns detected in shell scripts
- PaaS / serverless platform patterns (fly.io, vercel, netlify, etc.)
- Existing docker/kubernetes/terraform patterns still work
- Markdown exporter _deployment_details section renders evidence correctly
"""

from __future__ import annotations

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.types import ComponentType

_SH = AiSbomConfig(include_extensions={".sh", ".bash"}, enable_llm=False)
_PY = AiSbomConfig(include_extensions={".py"}, enable_llm=False)
_ALL = AiSbomConfig(
    include_extensions={".py", ".sh", ".bash", ".yaml", ".yml", ".tf"},
    enable_llm=False,
)


def _deployment_adapters(doc) -> set[str]:
    return {
        n.metadata.extras.get("adapter", "")
        for n in doc.nodes
        if n.component_type == ComponentType.DEPLOYMENT
    }


def _has_deployment(doc) -> bool:
    return any(n.component_type == ComponentType.DEPLOYMENT for n in doc.nodes)


def _deployment_evidence_details(doc) -> set[str]:
    details: set[str] = set()
    for n in doc.nodes:
        if n.component_type == ComponentType.DEPLOYMENT:
            for ev in n.evidence:
                detail = ev.detail or ""
                matched = detail.split(": ", 1)[-1] if ": " in detail else detail
                details.add(matched.lower())
    return details


# ── Shell scripts are now scanned by regex adapters ──────────────────────────


def test_sh_file_scanned_for_deployment(tmp_path):
    """.sh files must be processed by regex adapters (not skipped as docs)."""
    (tmp_path / "deploy.sh").write_text(
        "#!/bin/bash\ndocker build -t myapp . && docker push myapp\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected docker detection in .sh file; got no DEPLOYMENT node"


def test_bash_file_scanned_for_deployment(tmp_path):
    """.bash files must also be processed by regex adapters."""
    (tmp_path / "setup.bash").write_text(
        "#!/usr/bin/env bash\nkubectl apply -f manifests/\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected kubectl detection in .bash file"


# ── Cloud CLI patterns ────────────────────────────────────────────────────────


def test_gcloud_in_sh_detected(tmp_path):
    """gcloud CLI in a shell script must produce a DEPLOYMENT node."""
    (tmp_path / "deploy.sh").write_text(
        "#!/bin/bash\ngcloud run deploy myservice --image gcr.io/proj/img\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected gcloud DEPLOYMENT node"
    assert "gcloud" in _deployment_evidence_details(doc)


def test_kubectl_in_sh_detected(tmp_path):
    """kubectl apply in a shell script must produce a DEPLOYMENT node."""
    (tmp_path / "k8s_deploy.sh").write_text(
        "#!/bin/bash\nkubectl apply -f manifests/\nkubectl rollout status app\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected kubectl DEPLOYMENT node"
    assert "kubectl" in _deployment_evidence_details(doc)


def test_azure_cli_in_sh_detected(tmp_path):
    """az webapp create in a shell script must produce a DEPLOYMENT node."""
    (tmp_path / "azure_deploy.sh").write_text(
        "#!/bin/bash\naz login --service-principal\naz webapp create -n myapp -g rg\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected Azure CLI DEPLOYMENT node"


def test_ansible_in_sh_detected(tmp_path):
    """ansible-playbook in a shell script must produce a DEPLOYMENT node."""
    (tmp_path / "run.sh").write_text(
        "#!/bin/bash\nansible-playbook -i inventory/prod site.yml\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected ansible DEPLOYMENT node"
    assert any("ansible" in d for d in _deployment_evidence_details(doc))


def test_pulumi_in_sh_detected(tmp_path):
    """pulumi up in a shell script must produce a DEPLOYMENT node."""
    (tmp_path / "infra.sh").write_text(
        "#!/bin/bash\npulumi up --yes --stack prod\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected pulumi DEPLOYMENT node"
    assert "pulumi" in _deployment_evidence_details(doc)


# ── PaaS / serverless platforms ───────────────────────────────────────────────


def test_fly_io_detected(tmp_path):
    """flyctl in a deploy script must produce a DEPLOYMENT node."""
    (tmp_path / "deploy.sh").write_text(
        "#!/bin/bash\nflyctl deploy --remote-only\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected flyctl DEPLOYMENT node"


def test_vercel_detected(tmp_path):
    """vercel CLI must produce a DEPLOYMENT node."""
    (tmp_path / "deploy.sh").write_text(
        "#!/bin/bash\nvercel --prod\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected vercel DEPLOYMENT node"


def test_heroku_detected(tmp_path):
    """heroku CLI must produce a DEPLOYMENT node."""
    (tmp_path / "deploy.sh").write_text(
        "git push heroku main\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _SH)
    assert _has_deployment(doc), "Expected heroku DEPLOYMENT node"


# ── Existing patterns unaffected ─────────────────────────────────────────────


def test_docker_in_py_still_detected(tmp_path):
    """Existing docker pattern in Python files still works."""
    (tmp_path / "app.py").write_text(
        "import subprocess\nsubprocess.run(['docker', 'build', '.'])\n",
        encoding="utf-8",
    )
    doc = AiSbomExtractor().extract_from_path(tmp_path, _PY)
    assert _has_deployment(doc), "Expected docker DEPLOYMENT node in .py file"


# ── Markdown deployment details section ──────────────────────────────────────


def test_markdown_deployment_details_section(tmp_path):
    """_deployment_details must render a section with evidence locations."""
    from nuguard.sbom.toolbox.plugins.markdown_exporter import MarkdownExporterPlugin

    sbom = {
        "target": "test-app",
        "nodes": [
            {
                "name": "generic",
                "component_type": "DEPLOYMENT",
                "confidence": 0.63,
                "metadata": {
                    "extras": {
                        "canonical_name": "deployment_generic",
                        "adapter": "deployment_generic",
                        "detected_by_tiers": ["iac"],
                    }
                },
                "evidence": [
                    {
                        "kind": "regex",
                        "confidence": 0.6,
                        "detail": "deployment_generic: gcloud",
                        "location": {"path": "deploy.sh", "line": 3},
                    },
                    {
                        "kind": "regex",
                        "confidence": 0.6,
                        "detail": "deployment_generic: kubectl",
                        "location": {"path": "k8s/deploy.sh", "line": 7},
                    },
                ],
            }
        ],
        "deps": [],
    }
    result = MarkdownExporterPlugin().run(sbom, {})
    md = result.details["markdown"]
    assert "### Deployment Details" in md
    assert "gcloud" in md
    assert "kubectl" in md
    assert "deploy.sh" in md
    assert "Source tiers" in md
