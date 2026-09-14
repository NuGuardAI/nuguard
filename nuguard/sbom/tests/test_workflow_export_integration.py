from __future__ import annotations

import json

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor.core import (
    AiSbomExtractor,
)
from nuguard.sbom.types import (
    ComponentType,
    RelationshipType,
)


def test_default_extractor_registers_workflow_export_adapters() -> None:
    extractor = AiSbomExtractor(
        framework_adapters=(),
        regex_adapters=(),
        sql_adapters=(),
        iac_adapters=(),
    )

    assert {adapter.name for adapter in extractor.yaml_adapters} >= {
        "copilot_studio_export",
    }

    assert {adapter.name for adapter in extractor.json_adapters} >= {
        "n8n_workflow_export",
        "langflow_workflow_export",
        "flowise_workflow_export",
    }


def test_extractor_merges_n8n_graph_and_redacts_metadata(
    tmp_path,
) -> None:
    export = {
        "name": "Customer support",
        "nodes": [
            {
                "id": "webhook",
                "name": "Chat webhook",
                "type": ("n8n-nodes-base.webhook"),
                "typeVersion": 2,
                "parameters": {
                    "path": "/chat",
                    "httpMethod": "POST",
                },
            },
            {
                "id": "agent",
                "name": "Support Agent",
                "type": ("@n8n/n8n-nodes-langchain.agent"),
                "typeVersion": 1,
                "parameters": {"systemMessage": ("Never expose api_key=integration-secret")},
            },
            {
                "id": "model",
                "name": ("OpenAI Chat Model"),
                "type": ("@n8n/n8n-nodes-langchain.lmChatOpenAi"),
                "typeVersion": 1,
                "parameters": {"modelName": ("gpt-4o-mini")},
            },
        ],
        "connections": {
            "Chat webhook": {
                "main": [
                    [
                        {
                            "node": ("Support Agent"),
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ]
            },
            "Support Agent": {
                "ai_languageModel": [
                    [
                        {
                            "node": ("OpenAI Chat Model"),
                            "type": ("ai_languageModel"),
                            "index": 0,
                        }
                    ]
                ]
            },
        },
    }

    workflow_path = tmp_path / "support-workflow.json"
    workflow_path.write_text(
        json.dumps(
            export,
            indent=2,
        ),
        encoding="utf-8",
    )

    extractor = AiSbomExtractor(
        framework_adapters=(),
        regex_adapters=(),
        sql_adapters=(),
        iac_adapters=(),
    )

    document = extractor.extract_from_path(
        tmp_path,
        AiSbomConfig(
            enable_llm=False,
            supply_chain_scan=False,
        ),
    )

    platform_nodes = [
        node for node in document.nodes if node.metadata.extras.get("platform") == "n8n"
    ]

    assert {node.component_type for node in platform_nodes} >= {
        ComponentType.AGENT,
        ComponentType.FRAMEWORK,
        ComponentType.MODEL,
        ComponentType.API_ENDPOINT,
        ComponentType.PROMPT,
    }

    endpoint = next(
        node for node in platform_nodes if node.component_type == ComponentType.API_ENDPOINT
    )

    assert endpoint.metadata.endpoint == "/chat"
    assert endpoint.metadata.method == "POST"

    assert any(
        edge.relationship_type == RelationshipType.CALLS and edge.source == endpoint.id
        for edge in document.edges
    )

    serialized = document.model_dump_json()

    assert "integration-secret" not in serialized
    assert "[REDACTED]" in serialized
