"""Regression test for docs/sbom-accuracy-plan.md #2: PromptTSAdapter must
skip string-literal-based PROMPT detection entirely for files under an
i18n/locale directory convention, since context extraction there routinely
surfaces a "prompt"-suffixed key on plain UI copy (see
test_prompt_false_positives.py::TestI18nUiCopyNotFlaggedAsPrompt for the
narrower heuristic-level regression)."""
from __future__ import annotations

from nuguard.sbom.adapters.typescript.prompts import PromptTSAdapter


def test_i18n_directory_file_yields_no_string_literal_prompts():
    content = (
        'export default { '
        'prompt: "A new version has been downloaded and is ready to install now", '
        'title: "Update available for your application right now" '
        '};'
    )
    detections = PromptTSAdapter().extract(content, "src/i18n/en.ts", None)
    assert detections == []


def test_non_i18n_file_with_same_content_can_still_be_flagged():
    content = (
        'export default { prompt: "Please answer the question and always return '
        'your response in valid JSON format only, with no extra commentary at all." };'
    )
    detections = PromptTSAdapter().extract(content, "src/config/settings.ts", None)
    assert len(detections) >= 1
