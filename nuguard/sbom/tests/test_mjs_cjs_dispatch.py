"""Test that .mjs and .cjs files are dispatched through the TS adapter pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.extractor.core import _TYPESCRIPT_EXTENSIONS
from nuguard.sbom.types import ComponentType


def _extract_from_tmp(files: dict[str, str], config: AiSbomConfig | None = None):
    """Create a temp dir with the given files and extract from it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        for name, content in files.items():
            path = Path(tmpdir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        cfg = config or AiSbomConfig(enable_llm=False)
        return AiSbomExtractor().extract_from_path(Path(tmpdir), cfg)


class TestMjsCjsInTypescriptExtensions:
    """Verify .mjs and .cjs are included in the TypeScript extension set."""

    def test_mjs_in_typescript_extensions(self) -> None:
        assert ".mjs" in _TYPESCRIPT_EXTENSIONS

    def test_cjs_in_typescript_extensions(self) -> None:
        assert ".cjs" in _TYPESCRIPT_EXTENSIONS

    def test_original_extensions_still_present(self) -> None:
        for ext in (".ts", ".tsx", ".js", ".jsx"):
            assert ext in _TYPESCRIPT_EXTENSIONS


class TestMjsFileProcessedByTsAdapters:
    """Verify .mjs files go through the TS adapter pipeline."""

    def test_mjs_with_openai_client_detected(self) -> None:
        """An .mjs file importing openai should produce a MODEL detection."""
        source = """\
import OpenAI from 'openai';
const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
const response = await client.chat.completions.create({
    model: "gpt-4o",
    messages: [{ role: "user", content: "Hello" }],
});
"""
        cfg = AiSbomConfig(
            include_extensions={".mjs"},
            enable_llm=False,
        )
        doc = _extract_from_tmp({"src/index.mjs": source}, cfg)
        models = [n for n in doc.nodes if n.component_type == ComponentType.MODEL]
        assert len(models) >= 1, "Expected MODEL node from .mjs OpenAI import"

    def test_cjs_with_openai_client_detected(self) -> None:
        """A .cjs file requiring openai should produce a MODEL detection."""
        source = """\
const OpenAI = require('openai');
const client = new OpenAI({ apiKey: process.env.OPENAI_API_KEY });
async function main() {
    const response = await client.chat.completions.create({
        model: "gpt-4o",
        messages: [{ role: "user", content: "Hello" }],
    });
}
"""
        cfg = AiSbomConfig(
            include_extensions={".cjs"},
            enable_llm=False,
        )
        doc = _extract_from_tmp({"src/index.cjs": source}, cfg)
        models = [n for n in doc.nodes if n.component_type == ComponentType.MODEL]
        assert len(models) >= 1, "Expected MODEL node from .cjs OpenAI import"

    def test_mjs_unrelated_not_false_positive(self) -> None:
        """An .mjs file without AI imports should produce no detections."""
        source = """\
import express from 'express';
const app = express();
app.get('/', (req, res) => res.send('Hello World'));
app.listen(3000);
"""
        cfg = AiSbomConfig(
            include_extensions={".mjs"},
            enable_llm=False,
        )
        doc = _extract_from_tmp({"src/server.mjs": source}, cfg)
        ai_nodes = [
            n for n in doc.nodes
            if n.component_type in (
                ComponentType.MODEL, ComponentType.FRAMEWORK,
                ComponentType.TOOL, ComponentType.AGENT,
            )
        ]
        assert len(ai_nodes) == 0, "Unrelated .mjs should produce no AI detections"
