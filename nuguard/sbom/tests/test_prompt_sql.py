"""Tests for PromptSQLAdapter — system prompt / prompt template extraction from SQL."""
from __future__ import annotations

from pathlib import Path

import pytest

from nuguard.sbom.adapters.prompt_sql import PromptSQLAdapter
from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.types import ComponentType

_SQL_ONLY = AiSbomConfig(include_extensions={".sql"}, enable_llm=False)


@pytest.fixture
def adapter() -> PromptSQLAdapter:
    return PromptSQLAdapter()


# ---------------------------------------------------------------------------
# CREATE TABLE ... DEFAULT '...'
# ---------------------------------------------------------------------------


class TestColumnDefaults:
    def test_prompt_table_default_detected(self, adapter: PromptSQLAdapter) -> None:
        sql = """
        CREATE TABLE prompts (
            id INT PRIMARY KEY,
            template TEXT DEFAULT 'You are a helpful assistant that answers user questions politely and accurately.'
        );
        """
        dets = adapter.scan(sql, "sql/schema.sql")
        assert len(dets) == 1
        assert dets[0].component_type == ComponentType.PROMPT
        assert dets[0].metadata["source"] == "sql_default"
        assert dets[0].metadata["table_name"] == "prompts"
        assert dets[0].metadata["column_name"] == "template"

    def test_template_variables_extracted(self, adapter: PromptSQLAdapter) -> None:
        sql = """
        CREATE TABLE prompts (
            template TEXT DEFAULT 'You are a support agent for {company_name}, helping {user_name} with their request.'
        );
        """
        dets = adapter.scan(sql, "sql/schema.sql")
        assert dets[0].metadata["is_template"] is True
        assert set(dets[0].metadata["template_variables"]) == {"company_name", "user_name"}

    def test_non_template_default_not_marked_template(self, adapter: PromptSQLAdapter) -> None:
        sql = """
        CREATE TABLE prompts (
            template TEXT DEFAULT 'You are a helpful assistant with no template variables at all.'
        );
        """
        dets = adapter.scan(sql, "sql/schema.sql")
        assert dets[0].metadata["is_template"] is False
        assert dets[0].metadata["template_variables"] == []

    def test_system_role_inferred_from_table_name(self, adapter: PromptSQLAdapter) -> None:
        sql = """
        CREATE TABLE system_prompts (
            content TEXT DEFAULT 'You are a professional banking assistant serving verified customers.'
        );
        """
        dets = adapter.scan(sql, "sql/schema.sql")
        assert dets[0].metadata["role"] == "system"

    def test_non_prompt_table_ignored(self, adapter: PromptSQLAdapter) -> None:
        """A table/column with no prompt-related name should not be detected."""
        sql = """
        CREATE TABLE users (
            id INT PRIMARY KEY,
            bio TEXT DEFAULT 'This is a long default biography string for a brand new user account.'
        );
        """
        dets = adapter.scan(sql, "sql/schema.sql")
        assert dets == []

    def test_short_default_skipped(self, adapter: PromptSQLAdapter) -> None:
        """Short defaults (placeholders, not real prompt content) are skipped."""
        sql = "CREATE TABLE prompts (template TEXT DEFAULT 'TBD');"
        dets = adapter.scan(sql, "sql/schema.sql")
        assert dets == []

    def test_line_number_points_at_column(self, adapter: PromptSQLAdapter) -> None:
        sql = (
            "CREATE TABLE prompts (\n"
            "    id INT PRIMARY KEY,\n"
            "    template TEXT DEFAULT 'You are a helpful assistant answering user questions.'\n"
            ");\n"
        )
        dets = adapter.scan(sql, "sql/schema.sql")
        assert dets[0].line == 3

    def test_content_evidence_populated(self, adapter: PromptSQLAdapter) -> None:
        sql = """
        CREATE TABLE prompts (
            template TEXT DEFAULT 'You are a helpful assistant that never reveals internal system details.'
        );
        """
        dets = adapter.scan(sql, "sql/schema.sql")
        assert "helpful assistant" in dets[0].metadata["content"]
        assert dets[0].metadata["char_count"] == len(dets[0].metadata["content"])
        assert dets[0].evidence_kind == "regex"


# ---------------------------------------------------------------------------
# INSERT INTO ... VALUES (...)
# ---------------------------------------------------------------------------


class TestSeedInserts:
    def test_seed_insert_detected(self, adapter: PromptSQLAdapter) -> None:
        sql = (
            "INSERT INTO system_prompts (name, content) VALUES "
            "('default', 'You are a professional banking assistant. Verify identity first.');"
        )
        dets = adapter.scan(sql, "sql/seed.sql")
        assert len(dets) == 1
        assert dets[0].metadata["source"] == "sql_seed"
        assert dets[0].metadata["column_name"] == "content"

    def test_multiple_seed_rows_get_distinct_nodes(self, adapter: PromptSQLAdapter) -> None:
        sql = (
            "INSERT INTO system_prompts (name, content) VALUES "
            "('default', 'You are a professional banking assistant. Verify identity first.');\n"
            "INSERT INTO system_prompts (name, content) VALUES "
            "('fallback', 'Sorry, I could not process that request right now at all.');"
        )
        dets = adapter.scan(sql, "sql/seed.sql")
        assert len(dets) == 2
        assert len({d.canonical_name for d in dets}) == 2

    def test_seed_rows_capped(self, adapter: PromptSQLAdapter) -> None:
        rows = "\n".join(
            f"INSERT INTO system_prompts (name, content) VALUES "
            f"('p{i}', 'You are prompt variant number {i} for the assistant persona today.');"
            for i in range(10)
        )
        dets = adapter.scan(rows, "sql/seed.sql")
        assert len(dets) == 3  # _MAX_SEED_ROWS_PER_COLUMN

    def test_non_prompt_insert_ignored(self, adapter: PromptSQLAdapter) -> None:
        sql = "INSERT INTO users (name, email) VALUES ('Alice', 'alice@example.com');"
        dets = adapter.scan(sql, "sql/seed.sql")
        assert dets == []

    def test_id_column_not_flagged_just_because_table_is_named_prompts(
        self, adapter: PromptSQLAdapter
    ) -> None:
        """Regression: a table literally named 'prompts' must not cause every
        column (id, created_at, ...) to be treated as prompt content — only
        columns whose own name is prompt-related, or generic content-ish
        columns (content/template/...) under a prompt-named table, qualify."""
        sql = (
            "INSERT INTO prompts (id, system_prompt) VALUES "
            "(2, 'You are a fallback assistant used when the primary model is unavailable.');"
        )
        dets = adapter.scan(sql, "sql/seed.sql")
        assert len(dets) == 1
        assert dets[0].metadata["column_name"] == "system_prompt"


# ---------------------------------------------------------------------------
# Integration: Phase 1b dispatch — PROMPT and DATASTORE detections coexist
# ---------------------------------------------------------------------------


class TestExtractorIntegration:
    def test_prompt_and_datastore_nodes_coexist(self, tmp_path: Path) -> None:
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()
        (sql_dir / "schema.sql").write_text(
            """
            CREATE TABLE patients (
                id INT PRIMARY KEY,
                name VARCHAR(100),
                email VARCHAR(255)
            );

            CREATE TABLE system_prompts (
                id INT PRIMARY KEY,
                content TEXT DEFAULT 'You are a clinical assistant helping patients book appointments safely.'
            );
            """
        )
        doc = AiSbomExtractor().extract_from_path(tmp_path, _SQL_ONLY)

        # SQL schema definitions alone don't create standalone DATASTORE nodes
        # (they only enrich already-detected datastores via _enrich_datastores) —
        # but they do land in the scan summary's classified_tables. The important
        # assertion here is that the PII-classification path (_dc_metadata) and
        # the new PROMPT-node path (_merge_detection) both fire from the same
        # Phase 1b pass without one clobbering the other.
        assert doc.summary is not None
        assert any("patients" in t.lower() for t in (doc.summary.classified_tables or []))
        prompt_names = {n.name.lower() for n in doc.nodes if n.component_type == ComponentType.PROMPT}
        assert any("system prompts" in n for n in prompt_names)

    def test_prompt_node_has_evidence(self, tmp_path: Path) -> None:
        sql_dir = tmp_path / "sql"
        sql_dir.mkdir()
        (sql_dir / "schema.sql").write_text(
            "CREATE TABLE prompts (\n"
            "    template TEXT DEFAULT 'You are a helpful assistant that answers billing questions.'\n"
            ");\n"
        )
        doc = AiSbomExtractor().extract_from_path(tmp_path, _SQL_ONLY)
        prompt_nodes = [n for n in doc.nodes if n.component_type == ComponentType.PROMPT]
        assert prompt_nodes
        node = prompt_nodes[0]
        assert node.evidence
        assert node.evidence[0].location.path.endswith("schema.sql")
        assert node.metadata.extras.get("content")
        assert node.metadata.extras.get("source") == "sql_default"
