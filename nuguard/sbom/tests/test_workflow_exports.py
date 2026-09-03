from __future__ import annotations

import json

from nuguard.sbom.adapters.workflow_exports import (
    CopilotStudioYAMLAdapter,
    FlowiseWorkflowAdapter,
    LangflowWorkflowAdapter,
    N8nWorkflowAdapter,
)
from nuguard.sbom.types import ComponentType


def _by_type(detections):
    result = {}

    for detection in detections:
        result.setdefault(
            detection.component_type,
            [],
        ).append(detection)

    return result


def _relationships(detections):
    return [relationship for detection in detections for relationship in detection.relationships]


def _serialized(detections) -> str:
    return json.dumps(
        [
            {
                "display": detection.display_name,
                "metadata": detection.metadata,
                "snippet": detection.snippet,
            }
            for detection in detections
        ],
        default=str,
        sort_keys=True,
    )


def test_n8n_export_normalizes_graph_and_redacts_secrets() -> None:
    secret_path = "A" * 48

    export = {
        "name": "Support triage",
        "active": True,
        "nodes": [
            {
                "id": "webhook-1",
                "name": "Chat webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "parameters": {
                    "path": "/chat",
                    "httpMethod": "POST",
                    "authentication": "headerAuth",
                },
                "credentials": {
                    "httpHeaderAuth": {
                        "id": "cred-1",
                        "name": "Inbound auth",
                    }
                },
            },
            {
                "id": "agent-1",
                "name": "Triage Agent",
                "type": "@n8n/n8n-nodes-langchain.agent",
                "typeVersion": 1,
                "parameters": {
                    "systemMessage": ("Classify the request. api_key=do-not-retain-this"),
                },
            },
            {
                "id": "model-1",
                "name": "OpenAI Chat Model",
                "type": "@n8n/n8n-nodes-langchain.lmChatOpenAi",
                "typeVersion": 1,
                "parameters": {
                    "modelName": "gpt-4o-mini",
                    "apiKey": "sk-do-not-retain-this",
                },
                "credentials": {
                    "openAiApi": {
                        "id": "cred-2",
                        "name": "OpenAI prod",
                    }
                },
            },
            {
                "id": "tool-1",
                "name": "Create ticket HTTP Request",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4,
                "parameters": {
                    "url": (
                        "https://user:password@tickets.example/"
                        f"{secret_path}?token=do-not-retain-this"
                    ),
                    "method": "POST",
                },
                "credentials": {
                    "httpHeaderAuth": {
                        "id": "cred-3",
                        "name": "Ticket API",
                    }
                },
            },
            {
                "id": "db-1",
                "name": "Insert audit Postgres",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2,
                "parameters": {
                    "operation": "insert",
                    "table": "audit_events",
                },
            },
            {
                "id": "guard-1",
                "name": "Content Safety Moderation",
                "type": ("@n8n/n8n-nodes-langchain.outputParserGuardrails"),
                "typeVersion": 1,
                "parameters": {},
            },
        ],
        "connections": {
            "Chat webhook": {
                "main": [
                    [
                        {
                            "node": "Triage Agent",
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ]
            },
            "Triage Agent": {
                "main": [
                    [
                        {
                            "node": ("Create ticket HTTP Request"),
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ],
                "ai_languageModel": [
                    [
                        {
                            "node": "OpenAI Chat Model",
                            "type": "ai_languageModel",
                            "index": 0,
                        }
                    ]
                ],
            },
            "Create ticket HTTP Request": {
                "main": [
                    [
                        {
                            "node": ("Insert audit Postgres"),
                            "type": "main",
                            "index": 0,
                        }
                    ]
                ]
            },
        },
    }

    content = json.dumps(
        export,
        indent=2,
    )

    detections = N8nWorkflowAdapter().scan(
        content,
        "exports/support.json",
    )
    by_type = _by_type(detections)

    assert by_type[ComponentType.FRAMEWORK][0].display_name == "n8n"

    assert {detection.component_type for detection in detections} >= {
        ComponentType.AGENT,
        ComponentType.MODEL,
        ComponentType.TOOL,
        ComponentType.DATASTORE,
        ComponentType.GUARDRAIL,
        ComponentType.AUTH,
        ComponentType.API_ENDPOINT,
        ComponentType.PROMPT,
    }

    endpoint = by_type[ComponentType.API_ENDPOINT][0]

    assert endpoint.metadata["endpoint"] == "/chat"
    assert endpoint.metadata["method"] == "POST"
    assert endpoint.line > 1

    serialized = _serialized(detections)

    assert "do-not-retain-this" not in serialized
    assert "sk-do-not-retain-this" not in serialized
    assert "user:password" not in serialized
    assert secret_path not in serialized
    assert "[REDACTED]" in serialized
    assert "Inbound auth" in serialized

    relationships = _relationships(detections)

    assert any(
        relationship.relationship_type == "CALLS"
        and relationship.source_type == ComponentType.API_ENDPOINT
        and relationship.target_type == ComponentType.AGENT
        for relationship in relationships
    )

    assert any(
        relationship.relationship_type == "PROTECTS"
        and relationship.source_type == ComponentType.AUTH
        and relationship.target_type == ComponentType.API_ENDPOINT
        for relationship in relationships
    )

    assert any(
        relationship.relationship_type == "ACCESSES"
        and relationship.source_type == ComponentType.TOOL
        and relationship.target_type == ComponentType.DATASTORE
        and relationship.access_type == "readwrite"
        for relationship in relationships
    )


def test_n8n_trigger_without_concrete_path_does_not_invent_endpoint() -> None:
    export = {
        "name": "No concrete endpoint",
        "nodes": [
            {
                "id": "trigger",
                "name": "Chat Trigger",
                "type": ("@n8n/n8n-nodes-langchain.chatTrigger"),
                "parameters": {},
            }
        ],
        "connections": {},
    }

    detections = N8nWorkflowAdapter().scan(
        json.dumps(export),
        "exports/no-path.json",
    )

    assert not any(
        detection.component_type == ComponentType.API_ENDPOINT for detection in detections
    )


def test_langflow_export_detects_graph_components() -> None:
    export = {
        "name": "Research assistant",
        "data": {
            "nodes": [
                {
                    "id": "agent",
                    "type": "genericNode",
                    "data": {
                        "type": "Agent",
                        "display_name": ("Research Agent"),
                        "node": {
                            "name": "Agent",
                            "template": {
                                "instructions": {
                                    "value": ("Research carefully and cite every source.")
                                }
                            },
                        },
                    },
                },
                {
                    "id": "model",
                    "type": "genericNode",
                    "data": {
                        "type": "ChatOpenAI",
                        "display_name": "OpenAI",
                        "node": {
                            "name": "ChatOpenAI",
                            "template": {"model_name": {"value": ("gpt-4.1-mini")}},
                        },
                    },
                },
                {
                    "id": "tool",
                    "type": "genericNode",
                    "data": {
                        "type": "PythonREPLTool",
                        "display_name": ("Python Tool"),
                        "node": {
                            "name": ("PythonREPLTool"),
                            "template": {},
                        },
                    },
                },
                {
                    "id": "store",
                    "type": "genericNode",
                    "data": {
                        "type": ("QdrantVectorStore"),
                        "display_name": ("Qdrant Knowledge"),
                        "node": {
                            "name": ("QdrantVectorStore"),
                            "template": {},
                        },
                    },
                },
            ],
            "edges": [
                {
                    "source": "agent",
                    "target": "model",
                },
                {
                    "source": "agent",
                    "target": "tool",
                },
                {
                    "source": "tool",
                    "target": "store",
                },
            ],
        },
    }

    detections = LangflowWorkflowAdapter().scan(
        json.dumps(
            export,
            indent=2,
        ),
        "flows/research.json",
    )
    by_type = _by_type(detections)

    assert by_type[ComponentType.FRAMEWORK][0].display_name == "langflow"

    assert by_type[ComponentType.MODEL][0].metadata["model"] == "gpt-4.1-mini"

    assert by_type[ComponentType.DATASTORE][0].metadata["datastore_type"] == "vector"

    assert "code_execution" in by_type[ComponentType.TOOL][0].metadata["privilege_scopes"]

    assert any(
        "cite every source" in detection.metadata["content"]
        for detection in by_type[ComponentType.PROMPT]
    )


def test_flowise_string_encoded_flowdata_is_supported() -> None:
    graph = {
        "nodes": [
            {
                "id": "agent",
                "type": "customNode",
                "data": {
                    "name": ("conversationalAgent"),
                    "label": "Support Agent",
                    "inputAnchors": [],
                    "outputAnchors": [],
                    "inputs": {"systemMessage": ("Only answer from approved documents.")},
                },
            },
            {
                "id": "model",
                "type": "customNode",
                "data": {
                    "name": "chatOpenAI",
                    "label": "OpenAI Model",
                    "inputAnchors": [],
                    "outputAnchors": [],
                    "inputs": {
                        "modelName": ("gpt-4o-mini"),
                        "openAIApiKey": ("sk-never-copy-this"),
                    },
                },
            },
            {
                "id": "tool",
                "type": "customNode",
                "data": {
                    "name": "customTool",
                    "label": ("JavaScript Code Tool"),
                    "inputAnchors": [],
                    "outputAnchors": [],
                    "inputs": {"javascript": ("return input")},
                },
            },
            {
                "id": "store",
                "type": "customNode",
                "data": {
                    "name": "pinecone",
                    "label": ("Pinecone Vector Store"),
                    "inputAnchors": [],
                    "outputAnchors": [],
                    "inputs": {},
                },
            },
        ],
        "edges": [
            {
                "source": "agent",
                "target": "model",
            },
            {
                "source": "agent",
                "target": "tool",
            },
            {
                "source": "tool",
                "target": "store",
            },
        ],
    }
    export = {
        "name": "Support flow",
        "flowData": json.dumps(graph),
    }

    detections = FlowiseWorkflowAdapter().scan(
        json.dumps(
            export,
            indent=2,
        ),
        "flowise/support.json",
    )
    by_type = _by_type(detections)

    assert by_type[ComponentType.FRAMEWORK][0].display_name == "flowise"

    assert by_type[ComponentType.MODEL][0].metadata["model"] == "gpt-4o-mini"

    assert by_type[ComponentType.TOOL][0].metadata["side_effecting"] is True

    assert by_type[ComponentType.DATASTORE][0].metadata["datastore_type"] == "vector"

    assert "sk-never-copy-this" not in _serialized(detections)


def test_copilot_studio_topic_is_normalized_without_inventing_endpoint() -> None:
    content = """kind: AdaptiveDialog
id: support.topic.Triage
displayName: Support triage
beginDialog:
  kind: OnRecognizedIntent
  id: main
  actions:
    - kind: SendActivity
      id: greeting
      activity: Welcome. Describe the issue you need help with.
    - kind: SearchAndSummarizeContent
      id: answer
      model: gpt-4o
      instructions: Use only approved knowledge sources and include citations.
    - kind: InvokeConnectorAction
      id: createTicket
      displayName: Create ticket connector action
      connectionReference:
        connectionName: shared_servicenow
      parameters:
        password: never-copy-this
        operation: create record
    - kind: ConditionGroup
      id: humanApproval
      displayName: Human approval before sending
"""

    detections = CopilotStudioYAMLAdapter().scan(
        content,
        "topics/Triage.mcs.yml",
    )
    by_type = _by_type(detections)

    assert by_type[ComponentType.FRAMEWORK][0].display_name == "copilot-studio"

    assert by_type[ComponentType.MODEL][0].metadata["model"] == "gpt-4o"

    assert by_type[ComponentType.TOOL][0].display_name == "Create ticket connector action"

    assert by_type[ComponentType.GUARDRAIL][0].metadata["human_approval"] is True

    assert any(detection.metadata.get("hitl") is True for detection in by_type[ComponentType.AGENT])

    assert ComponentType.API_ENDPOINT not in by_type

    serialized = _serialized(detections)

    assert "never-copy-this" not in serialized
    assert "shared_servicenow" in serialized


def test_generic_json_and_yaml_are_not_misidentified() -> None:
    generic_react_flow = {
        "nodes": [
            {
                "id": "1",
                "type": "input",
                "data": {"label": "Start"},
            }
        ],
        "edges": [],
    }
    generic_automation = {
        "nodes": [
            {
                "id": "1",
                "type": "job",
                "typeVersion": 1,
            }
        ],
        "connections": {},
    }
    kubernetes = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec: {}
"""

    assert (
        N8nWorkflowAdapter().scan(
            json.dumps(generic_automation),
            "graph.json",
        )
        == []
    )

    assert (
        LangflowWorkflowAdapter().scan(
            json.dumps(generic_react_flow),
            "graph.json",
        )
        == []
    )

    assert (
        FlowiseWorkflowAdapter().scan(
            json.dumps(generic_react_flow),
            "graph.json",
        )
        == []
    )

    assert (
        CopilotStudioYAMLAdapter().scan(
            kubernetes,
            "deployment.yml",
        )
        == []
    )


def test_malformed_exports_fail_closed() -> None:
    json_adapters = (
        N8nWorkflowAdapter(),
        LangflowWorkflowAdapter(),
        FlowiseWorkflowAdapter(),
    )

    for adapter in json_adapters:
        assert (
            adapter.scan(
                "{not-json",
                "broken.json",
            )
            == []
        )

    assert (
        CopilotStudioYAMLAdapter().scan(
            "kind: [",
            "broken.mcs.yml",
        )
        == []
    )
