from __future__ import annotations

import pytest

from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata, ScanSummary
from nuguard.sbom.types import ComponentType


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
