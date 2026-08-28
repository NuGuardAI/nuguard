"""JSON adapters for sparkflows.io no-code/low-code IDE projects.

A sparkflows project is exported entirely as a tree of structured JSON
files — there is no source code to parse with an AST. Each subdirectory
holds one JSON "shape" per resource:

``project.json``
    Project/app metadata (``name``, ``tag``, ``category``, ``uuid``,
    ``createdBy``). Emits a ``framework:sparkflows`` FRAMEWORK node.

``agents/*.json``
    LLM agent graphs (``agentType`` + ``content.nodes``/``content.edges``).
    ``fire.nodes.gai.NodeAgent`` / ``fire.nodes.agent.NodeEmailNotification``
    nodes emit PROMPT (from ``systemPrompt``) and a low-confidence MODEL
    placeholder keyed by the opaque ``llmConnection`` id (the actual
    provider/model mapping lives in the Sparkflows platform's connection
    registry, not the project export). ``fire.nodes.utility.NodeWorkflowExecution``
    nodes emit a TOOL node keyed by the referenced ``workflow_uuid`` — the
    same canonical name the target workflow file uses for its own identity
    node, so NuGuard's existing dedup/merge phase coalesces the two into
    one node with evidence from both files.

``workflows/*.json``
    ETL/Spark pipeline graphs (top-level ``nodes``/``edges``/``dataSetDetails``,
    no ``agentType``). Emits the workflow's own identity as a TOOL node
    (canonical keyed by its own ``uuid``, matching the agent-side reference
    above). ``fire.nodes.salesforce.*`` and JDBC-connection nodes
    (``fire.nodes.save.NodeSaveJDBC`` or any node whose ``connection`` field
    has ``widget: "object_array"``) emit DATASTORE nodes. ``fire.nodes.h2o.*``
    nodes emit an ML MODEL node plus a ``framework:h2o`` FRAMEWORK node.

``datasets/*.json``
    Dataset definitions (``datasetType``, ``connectionName``, ``path``,
    ``schemaModel``). Emits a DATASTORE node keyed by the same canonical
    scheme as the workflow-embedded connection, so they merge.

``analytics_app/*.json``
    The deployed end-user app (``appStages``, ``executionType``,
    ``workflowUuid``, ``jdbcConnectionId``). Emits an AGENT node for the app
    entry point, plus a low-confidence DATASTORE placeholder for the opaque
    ``jdbcConnectionId``.

``charts/*.json``, ``dashboards/*.json``, ``wikiDocs/*.json`` are BI
visualization/documentation assets with no security-relevant surface and
are intentionally not covered.
"""

from __future__ import annotations

import json
import re
from typing import Any

from nuguard.common.logging import get_logger

from ..normalization import canonicalize_text
from ..types import ComponentType
from .base import ComponentDetection

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_load_json(content: str) -> Any:
    try:
        return json.loads(content)
    except Exception as exc:  # noqa: BLE001
        _log.debug("sparkflows: JSON parse error: %s", exc)
        return None


def _line_for_offset(content: str, offset: int) -> int:
    if offset < 0:
        return 1
    return content.count("\n", 0, offset) + 1


def _find_key_line(content: str, key: str, start: int = 0) -> int:
    idx = content.find(f'"{key}"', start)
    if idx == -1:
        return _line_for_offset(content, start)
    return _line_for_offset(content, idx)


def _node_offset(content: str, node_id: Any, start: int = 0) -> int:
    idx = content.find(f'"id": "{node_id}"', start)
    if idx == -1:
        return start
    return idx


def _field_value(node: dict[str, Any], field_name: str) -> str:
    for field in node.get("fields") or []:
        if isinstance(field, dict) and field.get("name") == field_name:
            return str(field.get("value") or "").strip()
    return ""


def _algorithm_from_node_class(node_class: str) -> str:
    name = node_class.rsplit(".", 1)[-1]
    return re.sub(r"^NodeH2O", "", name) or name


# ---------------------------------------------------------------------------
# Fingerprints
# ---------------------------------------------------------------------------

_PROJECT_KEYS = {"name", "tag", "category", "uuid", "createdBy"}
_APP_KEYS = {"appStages", "executionType", "workflowUuid"}
_DATASET_KEYS = {"datasetType", "connectionName", "schemaModel"}


def _is_sparkflows_project_file(data: Any) -> bool:
    return isinstance(data, dict) and _PROJECT_KEYS <= data.keys()


def _is_sparkflows_agent_file(data: Any) -> bool:
    return isinstance(data, dict) and "agentType" in data


def _is_sparkflows_workflow_file(data: Any) -> bool:
    return (
        isinstance(data, dict)
        and "agentType" not in data
        and isinstance(data.get("nodes"), list)
        and isinstance(data.get("edges"), list)
        and "dataSetDetails" in data
    )


def _is_sparkflows_dataset_file(data: Any) -> bool:
    return isinstance(data, dict) and _DATASET_KEYS <= data.keys()


def _is_sparkflows_app_file(data: Any) -> bool:
    return isinstance(data, dict) and _APP_KEYS <= data.keys()


# ---------------------------------------------------------------------------
# project.json -> FRAMEWORK
# ---------------------------------------------------------------------------


class SparkflowsProjectAdapter:
    """Detect a sparkflows project root and emit a platform FRAMEWORK node."""

    name = "sparkflows_project"
    priority = 44

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        data = _try_load_json(content)
        if not _is_sparkflows_project_file(data):
            return []

        line = _find_key_line(content, "tag")
        return [
            ComponentDetection(
                component_type=ComponentType.FRAMEWORK,
                canonical_name="framework:sparkflows",
                display_name="Sparkflows",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.9,
                metadata={
                    "framework": "sparkflows",
                    "language": "json",
                    "project_name": str(data.get("name") or ""),
                },
                file_path=rel_path,
                line=line,
                snippet=f"project: {data.get('name')}",
                evidence_kind="json",
            )
        ]


# ---------------------------------------------------------------------------
# agents/*.json -> AGENT, PROMPT, MODEL (placeholder), TOOL
# ---------------------------------------------------------------------------

_AGENT_LLM_NODE_CLASSES = {
    "fire.nodes.gai.NodeAgent",
    "fire.nodes.agent.NodeEmailNotification",
}
_WORKFLOW_EXECUTION_NODE_CLASS = "fire.nodes.utility.NodeWorkflowExecution"


class SparkflowsAgentAdapter:
    """Detect sparkflows agent graphs and their LLM/tool-call nodes."""

    name = "sparkflows_agent"
    priority = 42

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        data = _try_load_json(content)
        if not _is_sparkflows_agent_file(data):
            return []

        detections: list[ComponentDetection] = []
        agent_uuid = str(data.get("uuid") or data.get("content", {}).get("uuid") or rel_path)
        agent_name = str(data.get("name") or "sparkflows agent")

        detections.append(
            ComponentDetection(
                component_type=ComponentType.AGENT,
                canonical_name=f"agent:{canonicalize_text(agent_uuid)}",
                display_name=agent_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.9,
                metadata={
                    "framework": "sparkflows",
                    "language": "json",
                    "agent_type": str(data.get("agentType") or ""),
                },
                file_path=rel_path,
                line=_find_key_line(content, "agentType"),
                snippet=f"agent: {agent_name}",
                evidence_kind="json",
            )
        )

        graph = data.get("content") if isinstance(data.get("content"), dict) else data
        nodes = graph.get("nodes") if isinstance(graph, dict) else None
        if not isinstance(nodes, list):
            return detections

        cursor = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_class = str(node.get("nodeClass") or "")
            node_id = node.get("id")
            offset = _node_offset(content, node_id, cursor)
            cursor = max(cursor, offset)

            if node_class in _AGENT_LLM_NODE_CLASSES:
                detections.extend(self._llm_node_detections(node, content, offset, rel_path))
            elif node_class == _WORKFLOW_EXECUTION_NODE_CLASS:
                det = self._tool_call_detection(node, content, offset, rel_path)
                if det:
                    detections.append(det)

        return detections

    def _llm_node_detections(
        self, node: dict[str, Any], content: str, offset: int, rel_path: str
    ) -> list[ComponentDetection]:
        dets: list[ComponentDetection] = []
        node_name = str(node.get("name") or "LLM node")

        system_prompt = _field_value(node, "systemPrompt")
        if system_prompt:
            prompt_line = _line_for_offset(
                content, content.find('"name": "systemPrompt"', offset)
            )
            dets.append(
                ComponentDetection(
                    component_type=ComponentType.PROMPT,
                    canonical_name=f"prompt:{canonicalize_text(rel_path + ':' + node_name)}",
                    display_name=node_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.9,
                    metadata={
                        "role": "system",
                        "content": system_prompt,
                        "language": "json",
                    },
                    file_path=rel_path,
                    line=prompt_line if prompt_line > 0 else _line_for_offset(content, offset),
                    snippet=system_prompt[:120],
                    evidence_kind="json",
                )
            )

        llm_connection = _field_value(node, "llmConnection")
        if llm_connection:
            conn_line = _line_for_offset(content, content.find('"name": "llmConnection"', offset))
            dets.append(
                ComponentDetection(
                    component_type=ComponentType.MODEL,
                    canonical_name=f"llm_connection:{canonicalize_text(llm_connection)}",
                    display_name=f"llmConnection:{llm_connection}",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.5,
                    metadata={
                        "framework": "sparkflows",
                        "provider": "unknown",
                        "connection_id": llm_connection,
                        "resolved": False,
                        "language": "json",
                    },
                    file_path=rel_path,
                    line=conn_line if conn_line > 0 else _line_for_offset(content, offset),
                    snippet=f"llmConnection: {llm_connection}",
                    evidence_kind="json",
                )
            )
            dets.append(
                ComponentDetection(
                    component_type=ComponentType.FRAMEWORK,
                    canonical_name="framework:sparkflows",
                    display_name="Sparkflows",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.9,
                    metadata={"framework": "sparkflows", "language": "json"},
                    file_path=rel_path,
                    line=_line_for_offset(content, offset),
                    snippet=f"node: {node_name}",
                    evidence_kind="json",
                )
            )

        return dets

    def _tool_call_detection(
        self, node: dict[str, Any], content: str, offset: int, rel_path: str
    ) -> ComponentDetection | None:
        workflow_uuid = _field_value(node, "workflow_uuid")
        if not workflow_uuid:
            return None

        node_name = str(node.get("name") or "workflow call")
        line = _line_for_offset(content, content.find('"name": "workflow_uuid"', offset))
        return ComponentDetection(
            component_type=ComponentType.TOOL,
            canonical_name=f"tool:workflow:{canonicalize_text(workflow_uuid)}",
            display_name=node_name,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.9,
            metadata={
                "framework": "sparkflows",
                "tool_type": "workflow_execution",
                "workflow_uuid": workflow_uuid,
                "language": "json",
            },
            file_path=rel_path,
            line=line if line > 0 else _line_for_offset(content, offset),
            snippet=f"workflow_execution: {node_name}",
            evidence_kind="json",
        )


# ---------------------------------------------------------------------------
# workflows/*.json -> TOOL (own identity), DATASTORE, MODEL (H2O)
# ---------------------------------------------------------------------------

_SALESFORCE_PREFIX = "fire.nodes.salesforce."
_JDBC_SAVE_NODE_CLASS = "fire.nodes.save.NodeSaveJDBC"
_H2O_PREFIX = "fire.nodes.h2o."


class SparkflowsWorkflowAdapter:
    """Detect sparkflows ETL/Spark workflow graphs and their datastore/ML nodes."""

    name = "sparkflows_workflow"
    priority = 43

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        data = _try_load_json(content)
        if not _is_sparkflows_workflow_file(data):
            return []

        detections: list[ComponentDetection] = []
        workflow_uuid = str(data.get("uuid") or "")
        workflow_name = str(data.get("name") or "sparkflows workflow")

        if workflow_uuid:
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.TOOL,
                    canonical_name=f"tool:workflow:{canonicalize_text(workflow_uuid)}",
                    display_name=workflow_name,
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.9,
                    metadata={
                        "framework": "sparkflows",
                        "tool_type": "workflow",
                        "workflow_uuid": workflow_uuid,
                        "language": "json",
                    },
                    file_path=rel_path,
                    line=_find_key_line(content, "uuid"),
                    snippet=f"workflow: {workflow_name}",
                    evidence_kind="json",
                )
            )

        nodes = data.get("nodes")
        if not isinstance(nodes, list):
            return detections

        cursor = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_class = str(node.get("nodeClass") or "")
            node_id = node.get("id")
            offset = _node_offset(content, node_id, cursor)
            cursor = max(cursor, offset)

            if node_class.startswith(_SALESFORCE_PREFIX):
                detections.append(self._salesforce_detection(node, content, offset, rel_path))
            elif node_class == _JDBC_SAVE_NODE_CLASS or self._has_jdbc_connection(node):
                det = self._jdbc_detection(node, content, offset, rel_path)
                if det:
                    detections.append(det)
            elif node_class.startswith(_H2O_PREFIX):
                detections.extend(self._h2o_detections(node, node_class, content, offset, rel_path))

        return detections

    @staticmethod
    def _has_jdbc_connection(node: dict[str, Any]) -> bool:
        for field in node.get("fields") or []:
            if (
                isinstance(field, dict)
                and field.get("name") == "connection"
                and field.get("widget") == "object_array"
            ):
                return True
        return False

    def _salesforce_detection(
        self, node: dict[str, Any], content: str, offset: int, rel_path: str
    ) -> ComponentDetection:
        connection = _field_value(node, "connection") or "Salesforce"
        line = _line_for_offset(content, content.find('"name": "connection"', offset))
        return ComponentDetection(
            component_type=ComponentType.DATASTORE,
            canonical_name=f"datastore:{canonicalize_text(connection)}",
            display_name=connection,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.9,
            metadata={
                "provider": "salesforce",
                "connection_name": connection,
                "language": "json",
            },
            file_path=rel_path,
            line=line if line > 0 else _line_for_offset(content, offset),
            snippet=f"connection: {connection}",
            evidence_kind="json",
        )

    def _jdbc_detection(
        self, node: dict[str, Any], content: str, offset: int, rel_path: str
    ) -> ComponentDetection | None:
        connection = _field_value(node, "connection")
        if not connection:
            return None
        table = _field_value(node, "jdbctable") or _field_value(node, "jdbcDatabase")
        line = _line_for_offset(content, content.find('"name": "connection"', offset))
        return ComponentDetection(
            component_type=ComponentType.DATASTORE,
            canonical_name=f"datastore:{canonicalize_text(connection)}",
            display_name=connection,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.9,
            metadata={
                "provider": "jdbc",
                "connection_name": connection,
                "table": table or None,
                "language": "json",
            },
            file_path=rel_path,
            line=line if line > 0 else _line_for_offset(content, offset),
            snippet=f"connection: {connection}",
            evidence_kind="json",
        )

    def _h2o_detections(
        self,
        node: dict[str, Any],
        node_class: str,
        content: str,
        offset: int,
        rel_path: str,
    ) -> list[ComponentDetection]:
        node_name = str(node.get("name") or "H2O model")
        algorithm = _algorithm_from_node_class(node_class)
        line = _line_for_offset(content, offset)

        model_det = ComponentDetection(
            component_type=ComponentType.MODEL,
            canonical_name=f"model:h2o:{canonicalize_text(rel_path + ':' + node_name)}",
            display_name=node_name,
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.85,
            metadata={
                "framework": "h2o",
                "model_type": "ml",
                "algorithm": algorithm,
                "language": "json",
            },
            file_path=rel_path,
            line=line,
            snippet=f"{node_class}: {node_name}",
            evidence_kind="json",
        )
        framework_det = ComponentDetection(
            component_type=ComponentType.FRAMEWORK,
            canonical_name="framework:h2o",
            display_name="H2O",
            adapter_name=self.name,
            priority=self.priority,
            confidence=0.9,
            metadata={"framework": "h2o", "language": "json"},
            file_path=rel_path,
            line=line,
            snippet=f"{node_class}: {node_name}",
            evidence_kind="json",
        )
        return [model_det, framework_det]


# ---------------------------------------------------------------------------
# datasets/*.json -> DATASTORE
# ---------------------------------------------------------------------------


class SparkflowsDatasetAdapter:
    """Detect sparkflows dataset definitions and emit DATASTORE nodes."""

    name = "sparkflows_dataset"
    priority = 45

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        data = _try_load_json(content)
        if not _is_sparkflows_dataset_file(data):
            return []

        connection_name = str(data.get("connectionName") or "")
        if not connection_name:
            return []

        dataset_type = str(data.get("datasetType") or "unknown").lower()
        return [
            ComponentDetection(
                component_type=ComponentType.DATASTORE,
                canonical_name=f"datastore:{canonicalize_text(connection_name)}",
                display_name=connection_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.9,
                metadata={
                    "provider": dataset_type,
                    "connection_name": connection_name,
                    "path": str(data.get("path") or ""),
                    "language": "json",
                },
                file_path=rel_path,
                line=_find_key_line(content, "connectionName"),
                snippet=f"connectionName: {connection_name}",
                evidence_kind="json",
            )
        ]


# ---------------------------------------------------------------------------
# analytics_app/*.json -> AGENT (app entry point), DATASTORE (placeholder)
# ---------------------------------------------------------------------------


class SparkflowsAnalyticsAppAdapter:
    """Detect sparkflows deployed analytics-app entry points."""

    name = "sparkflows_analytics_app"
    priority = 46

    def scan(self, content: str, rel_path: str) -> list[ComponentDetection]:
        data = _try_load_json(content)
        if not _is_sparkflows_app_file(data):
            return []

        detections: list[ComponentDetection] = []
        app_id = str(data.get("uuid") or data.get("id") or rel_path)
        app_name = str(data.get("name") or "sparkflows app")

        detections.append(
            ComponentDetection(
                component_type=ComponentType.AGENT,
                canonical_name=f"agent:app:{canonicalize_text(app_id)}",
                display_name=app_name,
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.9,
                metadata={
                    "framework": "sparkflows",
                    "agent_type": "analytics_app",
                    "execution_type": str(data.get("executionType") or ""),
                    "language": "json",
                },
                file_path=rel_path,
                line=_find_key_line(content, "appStages"),
                snippet=f"analytics_app: {app_name}",
                evidence_kind="json",
            )
        )

        jdbc_connection_id = data.get("jdbcConnectionId")
        if jdbc_connection_id not in (None, ""):
            detections.append(
                ComponentDetection(
                    component_type=ComponentType.DATASTORE,
                    canonical_name=f"datastore:jdbc_connection:{canonicalize_text(str(jdbc_connection_id))}",
                    display_name=f"jdbcConnectionId:{jdbc_connection_id}",
                    adapter_name=self.name,
                    priority=self.priority,
                    confidence=0.5,
                    metadata={
                        "provider": "jdbc",
                        "connection_id": str(jdbc_connection_id),
                        "resolved": False,
                        "language": "json",
                    },
                    file_path=rel_path,
                    line=_find_key_line(content, "jdbcConnectionId"),
                    snippet=f"jdbcConnectionId: {jdbc_connection_id}",
                    evidence_kind="json",
                )
            )

        return detections


__all__ = [
    "SparkflowsAgentAdapter",
    "SparkflowsAnalyticsAppAdapter",
    "SparkflowsDatasetAdapter",
    "SparkflowsProjectAdapter",
    "SparkflowsWorkflowAdapter",
]
