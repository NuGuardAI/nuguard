"""Imported prompts and datastore clients remain connected to their agent."""

from __future__ import annotations

from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.extractor.config import AiSbomConfig
from nuguard.sbom.types import ComponentType, RelationshipType


def test_agent_uses_imported_prompt_and_datastore(tmp_path) -> None:
    (tmp_path / "prompts.py").write_text(
        'SYSTEM_PROMPT = "You are an analyst. Review account activity carefully, '
        'explain unusual patterns, and give a concise answer grounded in the available data."\n',
        encoding="utf-8",
    )
    (tmp_path / "db.py").write_text(
        "from sqlalchemy import create_engine\n"
        "engine = create_engine('postgresql://localhost/app')\n",
        encoding="utf-8",
    )
    (tmp_path / "agent.py").write_text(
        "from agents import Agent\n"
        "from prompts import SYSTEM_PROMPT\n"
        "from db import engine\n"
        "analyst = Agent(name='Analyst', instructions=SYSTEM_PROMPT, memory=engine)\n",
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(
        tmp_path, AiSbomConfig(include_extensions={".py"}, enable_llm=False)
    )
    agent = next(n for n in doc.nodes if n.component_type == ComponentType.AGENT)
    prompt = next(
        n
        for n in doc.nodes
        if n.component_type == ComponentType.PROMPT
        and n.metadata.extras.get("canonical_name") == "system_prompt"
    )
    datastore = next(n for n in doc.nodes if n.component_type == ComponentType.DATASTORE)

    assert any(
        e.source == agent.id
        and e.target == prompt.id
        and e.relationship_type == RelationshipType.USES
        for e in doc.edges
    )
    assert any(
        e.source == agent.id
        and e.target == datastore.id
        and e.relationship_type == RelationshipType.ACCESSES
        for e in doc.edges
    )


def test_unused_import_does_not_create_datastore_access(tmp_path) -> None:
    (tmp_path / "db.py").write_text(
        "from sqlalchemy import create_engine\n"
        "engine = create_engine('postgresql://localhost/app')\n",
        encoding="utf-8",
    )
    (tmp_path / "agent.py").write_text(
        "from agents import Agent\n"
        "from db import engine\n"
        "analyst = Agent(name='Analyst')\n",
        encoding="utf-8",
    )

    doc = AiSbomExtractor().extract_from_path(
        tmp_path, AiSbomConfig(include_extensions={".py"}, enable_llm=False)
    )
    agent_ids = {n.id for n in doc.nodes if n.component_type == ComponentType.AGENT}
    datastore_ids = {n.id for n in doc.nodes if n.component_type == ComponentType.DATASTORE}
    assert agent_ids and datastore_ids
    assert not any(
        e.source in agent_ids
        and e.target in datastore_ids
        and e.relationship_type == RelationshipType.ACCESSES
        for e in doc.edges
    )
