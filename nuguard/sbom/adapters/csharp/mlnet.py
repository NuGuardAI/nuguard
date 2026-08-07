"""C# adapter for ML.NET contexts, pipelines, and trainers."""

from __future__ import annotations

from typing import Any

from ...normalization import canonicalize_text
from ...types import ComponentType
from ..base import ComponentDetection, RelationshipHint
from ._csharp_base import CSharpFrameworkAdapter
from ._source import find_calls

_TASK_SEGMENTS = {
    "BinaryClassification": ("binary_classification"),
    "MulticlassClassification": ("multiclass_classification"),
    "Regression": "regression",
    "Clustering": "clustering",
    "Ranking": "ranking",
    "Recommendation": "recommendation",
    "Forecasting": "forecasting",
    "AnomalyDetection": "anomaly_detection",
}


class CSharpMLNetAdapter(CSharpFrameworkAdapter):
    """Detect ML.NET model-building and prediction pipelines."""

    name = "csharp_mlnet"
    priority = 55
    handles_namespaces = ["Microsoft.ML"]

    def extract(
        self,
        content: str,
        file_path: str,
        parse_result: Any,
    ) -> list[ComponentDetection]:
        result = self._parse_result(
            content,
            file_path,
            parse_result,
        )

        if not self._detect(result) and "MLContext" not in content:
            return []

        root = "framework:mlnet"
        import_line = next(
            (
                item.line
                for item in result.using_directives
                if (item.namespace == "Microsoft.ML" or item.namespace.startswith("Microsoft.ML."))
            ),
            0,
        )
        detections: list[ComponentDetection] = [
            ComponentDetection(
                component_type=(ComponentType.FRAMEWORK),
                canonical_name=root,
                display_name="ML.NET",
                adapter_name=self.name,
                priority=self.priority,
                confidence=0.97,
                metadata={
                    "framework": "mlnet",
                    "language": "csharp",
                },
                file_path=file_path,
                line=import_line,
                snippet="using Microsoft.ML",
                evidence_kind="ast_import",
            )
        ]

        pipeline_emitted = False

        for call in find_calls(content):
            receiver = call.receiver or ""

            if call.name == "MLContext" and call.is_constructor:
                display = call.assigned_to or "MLContext"
                canonical = canonicalize_text(f"mlnet:context:{display}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.FRAMEWORK),
                        canonical_name=canonical,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.95,
                        metadata={
                            "framework": "mlnet",
                            "context_class": ("MLContext"),
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind=("ast_instantiation"),
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.FRAMEWORK),
                                relationship_type=("CONTAINS"),
                            )
                        ],
                    )
                )
                continue

            if ".Trainers" in receiver:
                task = _task_from_receiver(receiver)
                display = call.name
                canonical = canonicalize_text(f"mlnet:trainer:{task}:{display}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.MODEL),
                        canonical_name=canonical,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.92,
                        metadata={
                            "framework": "mlnet",
                            "provider": "mlnet",
                            "trainer": call.name,
                            "task": task,
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind="ast_call",
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.MODEL),
                                relationship_type="USES",
                            )
                        ],
                    )
                )
                continue

            if ".Transforms" in receiver:
                if pipeline_emitted:
                    continue

                display = call.assigned_to or "ML.NET pipeline"
                canonical = canonicalize_text(f"mlnet:pipeline:{display}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.FRAMEWORK),
                        canonical_name=canonical,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.86,
                        metadata={
                            "framework": "mlnet",
                            "pipeline": True,
                            "first_stage": call.name,
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind="ast_call",
                    )
                )
                pipeline_emitted = True
                continue

            if call.name == "Fit" and call.receiver:
                display = call.assigned_to or f"trained_model_{call.line}"
                canonical = canonicalize_text(f"mlnet:model:{display}")
                detections.append(
                    ComponentDetection(
                        component_type=(ComponentType.MODEL),
                        canonical_name=canonical,
                        display_name=display,
                        adapter_name=self.name,
                        priority=self.priority,
                        confidence=0.90,
                        metadata={
                            "framework": "mlnet",
                            "provider": "mlnet",
                            "trained_model": True,
                            "language": "csharp",
                        },
                        file_path=file_path,
                        line=call.line,
                        snippet=call.snippet,
                        evidence_kind="ast_call",
                        relationships=[
                            RelationshipHint(
                                source_canonical=root,
                                source_type=(ComponentType.FRAMEWORK),
                                target_canonical=(canonical),
                                target_type=(ComponentType.MODEL),
                                relationship_type="USES",
                            )
                        ],
                    )
                )

        return _dedupe(detections)


def _task_from_receiver(
    receiver: str,
) -> str:
    for segment, task in _TASK_SEGMENTS.items():
        if segment in receiver:
            return task

    return "machine_learning"


def _dedupe(
    detections: list[ComponentDetection],
) -> list[ComponentDetection]:
    seen: set[tuple[ComponentType, str]] = set()
    result: list[ComponentDetection] = []

    for detection in detections:
        key = (
            detection.component_type,
            detection.canonical_name,
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(detection)

    return result


__all__ = ["CSharpMLNetAdapter"]
