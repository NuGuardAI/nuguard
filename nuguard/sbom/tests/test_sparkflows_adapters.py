"""Tests for sparkflows.io no-code/low-code JSON adapters."""

from __future__ import annotations

import json

from nuguard.sbom.adapters.base import ComponentDetection
from nuguard.sbom.adapters.sparkflows import (
    SparkflowsAgentAdapter,
    SparkflowsAnalyticsAppAdapter,
    SparkflowsDatasetAdapter,
    SparkflowsProjectAdapter,
    SparkflowsWorkflowAdapter,
)
from nuguard.sbom.types import ComponentType


def _by_type(
    detections: list[ComponentDetection],
    component_type: ComponentType,
) -> list[ComponentDetection]:
    return [detection for detection in detections if detection.component_type == component_type]


def test_project_adapter_emits_framework_node() -> None:
    source = json.dumps(
        {
            "id": 1,
            "name": "Demo Project",
            "tag": "DEMO_1",
            "uuid": "DEMO_1",
            "category": "AGENTS",
            "createdBy": "alice",
        }
    )

    detections = SparkflowsProjectAdapter().scan(source, "project.json")
    frameworks = _by_type(detections, ComponentType.FRAMEWORK)

    assert len(frameworks) == 1
    assert frameworks[0].metadata["framework"] == "sparkflows"


def test_project_adapter_ignores_unrelated_json() -> None:
    source = json.dumps({"name": "some-package", "version": "1.0.0"})

    assert SparkflowsProjectAdapter().scan(source, "package.json") == []


def test_agent_adapter_extracts_prompt_model_and_tool() -> None:
    source = json.dumps(
        {
            "agentType": "SINGLE_LLM",
            "name": "Demo Agent",
            "uuid": "agent-uuid-1",
            "content": {
                "nodes": [
                    {
                        "id": "1",
                        "name": "Validate",
                        "type": "agent",
                        "nodeClass": "fire.nodes.gai.NodeAgent",
                        "fields": [
                            {"name": "llmConnection", "value": "737", "widget": "object_array"},
                            {
                                "name": "systemPrompt",
                                "value": "You are a helpful assistant.",
                                "widget": "textareafield",
                            },
                        ],
                    },
                    {
                        "id": "2",
                        "name": "Push To D365",
                        "type": "workflow_execution",
                        "nodeClass": "fire.nodes.utility.NodeWorkflowExecution",
                        "fields": [
                            {
                                "name": "workflow_uuid",
                                "value": "wf-uuid-1",
                                "widget": "workflow",
                            }
                        ],
                    },
                ],
                "edges": [],
            },
        }
    )

    detections = SparkflowsAgentAdapter().scan(source, "agents/Demo.json")

    agents = _by_type(detections, ComponentType.AGENT)
    prompts = _by_type(detections, ComponentType.PROMPT)
    models = _by_type(detections, ComponentType.MODEL)
    tools = _by_type(detections, ComponentType.TOOL)

    assert len(agents) == 1
    assert agents[0].display_name == "Demo Agent"

    assert len(prompts) == 1
    assert prompts[0].metadata["content"] == "You are a helpful assistant."
    assert prompts[0].metadata["role"] == "system"

    assert len(models) == 1
    assert models[0].canonical_name == "llm_connection:737"
    assert models[0].confidence == 0.5
    assert models[0].metadata["resolved"] is False

    assert len(tools) == 1
    assert tools[0].canonical_name == "tool:workflow:wf_uuid_1"


def test_agent_adapter_ignores_unrelated_json() -> None:
    source = json.dumps({"nodes": [], "edges": []})

    assert SparkflowsAgentAdapter().scan(source, "graph.json") == []


def test_workflow_adapter_own_identity_matches_agent_tool_canonical() -> None:
    source = json.dumps(
        {
            "name": "Push Order to D365",
            "uuid": "wf-uuid-1",
            "nodes": [],
            "edges": [],
            "dataSetDetails": [],
        }
    )

    detections = SparkflowsWorkflowAdapter().scan(source, "workflows/Push.json")
    tools = _by_type(detections, ComponentType.TOOL)

    assert len(tools) == 1
    assert tools[0].canonical_name == "tool:workflow:wf_uuid_1"


def test_workflow_adapter_extracts_salesforce_and_jdbc_datastores() -> None:
    source = json.dumps(
        {
            "name": "Fetch Order",
            "uuid": "wf-uuid-2",
            "dataSetDetails": [],
            "nodes": [
                {
                    "id": "1",
                    "name": "FetchOrder",
                    "type": "dataset",
                    "nodeClass": "fire.nodes.salesforce.NodeExecuteSOQLInSalesforce",
                    "fields": [
                        {"name": "connection", "value": "Salesforce", "widget": "object_array"}
                    ],
                },
                {
                    "id": "2",
                    "name": "Save JDBC",
                    "type": "transform",
                    "nodeClass": "fire.nodes.save.NodeSaveJDBC",
                    "fields": [
                        {"name": "connection", "value": "JDBC_MySQL", "widget": "object_array"},
                        {"name": "jdbctable", "value": "sales_orders", "widget": "textfield"},
                    ],
                },
            ],
            "edges": [],
        }
    )

    detections = SparkflowsWorkflowAdapter().scan(source, "workflows/Fetch.json")
    datastores = {d.metadata["provider"]: d for d in _by_type(detections, ComponentType.DATASTORE)}

    assert datastores["salesforce"].canonical_name == "datastore:salesforce"
    assert datastores["jdbc"].canonical_name == "datastore:jdbc_mysql"
    assert datastores["jdbc"].metadata["table"] == "sales_orders"


def test_workflow_adapter_extracts_h2o_model_and_framework() -> None:
    source = json.dumps(
        {
            "name": "Train Model",
            "uuid": "wf-uuid-3",
            "dataSetDetails": [],
            "nodes": [
                {
                    "id": "1",
                    "name": "Isolation Forest",
                    "type": "model",
                    "nodeClass": "fire.nodes.h2o.NodeH2OIsolationForest",
                    "fields": [],
                }
            ],
            "edges": [],
        }
    )

    detections = SparkflowsWorkflowAdapter().scan(source, "workflows/Train.json")
    models = _by_type(detections, ComponentType.MODEL)
    frameworks = _by_type(detections, ComponentType.FRAMEWORK)

    assert len(models) == 1
    assert models[0].metadata["framework"] == "h2o"
    assert models[0].metadata["algorithm"] == "IsolationForest"

    assert any(f.canonical_name == "framework:h2o" for f in frameworks)


def test_workflow_adapter_skips_plain_etl_nodes() -> None:
    source = json.dumps(
        {
            "name": "Select Only",
            "uuid": "wf-uuid-4",
            "dataSetDetails": [],
            "nodes": [
                {
                    "id": "1",
                    "name": "Select",
                    "type": "transform",
                    "nodeClass": "fire.nodes.etl.NodeSelect",
                    "fields": [],
                }
            ],
            "edges": [],
        }
    )

    detections = SparkflowsWorkflowAdapter().scan(source, "workflows/Select.json")
    non_tool = [d for d in detections if d.component_type != ComponentType.TOOL]

    assert non_tool == []


def test_dataset_adapter_extracts_datastore_matching_workflow_canonical() -> None:
    source = json.dumps(
        {
            "id": 1,
            "name": "orders",
            "datasetType": "JDBC",
            "connectionName": "JDBC_MySQL",
            "path": "userdb|orders",
            "schemaModel": {"schemaColList": []},
        }
    )

    detections = SparkflowsDatasetAdapter().scan(source, "datasets/orders.json")
    datastores = _by_type(detections, ComponentType.DATASTORE)

    assert len(datastores) == 1
    assert datastores[0].canonical_name == "datastore:jdbc_mysql"
    assert datastores[0].metadata["provider"] == "jdbc"


def test_dataset_adapter_ignores_unrelated_json() -> None:
    source = json.dumps({"compilerOptions": {"target": "es2020"}})

    assert SparkflowsDatasetAdapter().scan(source, "tsconfig.json") == []


def test_analytics_app_adapter_extracts_app_and_connection_placeholder() -> None:
    source = json.dumps(
        {
            "id": 1,
            "name": "Order Review App",
            "uuid": "ORDER_REVIEW_APP",
            "appStages": [{"id": 1, "name": "stage"}],
            "executionType": "Workflow",
            "workflowUuid": "wf-uuid-1",
            "jdbcConnectionId": 39,
        }
    )

    detections = SparkflowsAnalyticsAppAdapter().scan(source, "analytics_app/App.json")
    agents = _by_type(detections, ComponentType.AGENT)
    datastores = _by_type(detections, ComponentType.DATASTORE)

    assert len(agents) == 1
    assert agents[0].canonical_name == "agent:app:order_review_app"

    assert len(datastores) == 1
    assert datastores[0].canonical_name == "datastore:jdbc_connection:39"
    assert datastores[0].confidence == 0.5
    assert datastores[0].metadata["resolved"] is False


def test_analytics_app_adapter_ignores_unrelated_json() -> None:
    source = json.dumps({"appStages": []})

    assert SparkflowsAnalyticsAppAdapter().scan(source, "app.json") == []
