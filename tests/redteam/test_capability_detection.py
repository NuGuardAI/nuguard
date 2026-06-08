"""Tests for improved capability detection in CapabilityDetector.

Verifies:
- DOCUMENT is detected from tool keyword metadata
- MULTI_SESSION is detected from context_payload_fields and memory tool names
- RENDERS_MARKDOWN is still added (default-on) but detection logic runs correctly
"""
from __future__ import annotations

import uuid

import pytest

from nuguard.redteam.catalog.capability import CapabilityDetector
from nuguard.redteam.catalog.taxonomy import Capability as C
from nuguard.sbom.models import AiSbomDocument, Node, NodeMetadata
from nuguard.sbom.types import ComponentType


def _node(
    name: str,
    ctype: ComponentType,
    description: str = "",
    context_payload_fields: list[str] | None = None,
) -> Node:
    meta = NodeMetadata(description=description)
    if context_payload_fields is not None:
        meta.context_payload_fields = context_payload_fields
    return Node(
        id=str(uuid.uuid4()),
        name=name,
        component_type=ctype,
        confidence=1.0,
        metadata=meta,
    )


def _sbom(*nodes: Node) -> AiSbomDocument:
    return AiSbomDocument(target="test://local", nodes=list(nodes), edges=[])


class TestDocumentCapability:
    def test_detected_from_file_tool(self) -> None:
        agent = _node("main_agent", ComponentType.AGENT)
        file_tool = _node("file_upload_tool", ComponentType.TOOL, description="uploads files")
        sbom = _sbom(agent, file_tool)
        profile = CapabilityDetector(sbom).build()
        assert C.DOCUMENT in profile.capabilities

    def test_detected_from_ocr_tool_description(self) -> None:
        agent = _node("agent", ComponentType.AGENT)
        tool = _node("scan_tool", ComponentType.TOOL, description="performs OCR on scanned documents")
        sbom = _sbom(agent, tool)
        profile = CapabilityDetector(sbom).build()
        assert C.DOCUMENT in profile.capabilities

    def test_detected_from_pdf_in_name(self) -> None:
        agent = _node("agent", ComponentType.AGENT)
        tool = _node("pdf_extractor", ComponentType.TOOL)
        sbom = _sbom(agent, tool)
        profile = CapabilityDetector(sbom).build()
        assert C.DOCUMENT in profile.capabilities

    def test_not_detected_without_document_tool(self) -> None:
        agent = _node("agent", ComponentType.AGENT)
        tool = _node("calculator_tool", ComponentType.TOOL, description="performs math calculations")
        sbom = _sbom(agent, tool)
        profile = CapabilityDetector(sbom).build()
        assert C.DOCUMENT not in profile.capabilities


class TestMultiSessionCapability:
    def test_detected_from_context_payload_session_id(self) -> None:
        agent = _node("agent", ComponentType.AGENT)
        ep = _node(
            "chat_endpoint", ComponentType.API_ENDPOINT,
            context_payload_fields=["session_id", "message"]
        )
        sbom = _sbom(agent, ep)
        profile = CapabilityDetector(sbom).build()
        assert C.MULTI_SESSION in profile.capabilities

    def test_detected_from_context_payload_user_id(self) -> None:
        agent = _node("agent", ComponentType.AGENT)
        ep = _node(
            "api", ComponentType.API_ENDPOINT,
            context_payload_fields=["user_id", "query"]
        )
        sbom = _sbom(agent, ep)
        profile = CapabilityDetector(sbom).build()
        assert C.MULTI_SESSION in profile.capabilities

    def test_detected_from_memory_tool_name(self) -> None:
        agent = _node("agent", ComponentType.AGENT)
        tool = _node("user_memory_store", ComponentType.TOOL)
        sbom = _sbom(agent, tool)
        profile = CapabilityDetector(sbom).build()
        assert C.MULTI_SESSION in profile.capabilities

    def test_detected_from_history_tool(self) -> None:
        agent = _node("agent", ComponentType.AGENT)
        tool = _node("conversation_history", ComponentType.TOOL)
        sbom = _sbom(agent, tool)
        profile = CapabilityDetector(sbom).build()
        assert C.MULTI_SESSION in profile.capabilities

    def test_not_detected_without_session_signals(self) -> None:
        agent = _node("agent", ComponentType.AGENT)
        tool = _node("calculator", ComponentType.TOOL, description="does math")
        ep = _node("api", ComponentType.API_ENDPOINT, context_payload_fields=["query"])
        sbom = _sbom(agent, tool, ep)
        profile = CapabilityDetector(sbom).build()
        assert C.MULTI_SESSION not in profile.capabilities


class TestRendersMarkdownCapability:
    def test_renders_markdown_present_by_default(self) -> None:
        agent = _node("agent", ComponentType.AGENT)
        sbom = _sbom(agent)
        profile = CapabilityDetector(sbom).build()
        # Still default-on to preserve existing behavior
        assert C.RENDERS_MARKDOWN in profile.capabilities
