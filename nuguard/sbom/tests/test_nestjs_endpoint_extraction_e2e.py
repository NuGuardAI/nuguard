"""End-to-end regression test: NestJS API_ENDPOINT extraction through the
full AiSbomExtractor pipeline (adapter + cross-file DTO pre-pass + dedup).

Covers the exact bug class found in Studyield (see
tests/apps/studyield-app/studyield-sbom-fix.md item #1): zero API_ENDPOINT
nodes for any NestJS backend, and — once the adapter existed — a separate
dedup bug (_dedup_by_name_prefix) that silently dropped the real
'sendMessage' chat endpoint because its display name is a word-boundary
prefix of the sibling 'sendMessageStream' endpoint's display name.
"""

from __future__ import annotations

from nuguard.sbom.config import AiSbomConfig
from nuguard.sbom.extractor import AiSbomExtractor
from nuguard.sbom.types import ComponentType

_TS_ONLY = AiSbomConfig(include_extensions={".ts"}, enable_llm=False)

_CONTROLLER = """
import { Controller, Get, Post, Body, Param, UseGuards } from '@nestjs/common';

@Controller('chat')
@UseGuards(JwtAuthGuard)
export class ChatController {
  @Get('conversations')
  async getConversations() {
    return [];
  }

  @Post('conversations/:id/messages')
  async sendMessage(@Param('id') id: string, @Body() dto: SendMessageDto) {
    return {};
  }

  @Post('conversations/:id/messages/stream')
  async sendMessageStream(@Param('id') id: string, @Body() dto: SendMessageDto) {
    return {};
  }

  @Post('conversations/:id/messages/upload')
  async sendMessageWithFiles(@Param('id') id: string, @Body() dto: SendMessageDto) {
    return {};
  }
}
"""

_SERVICE = """
export interface SendMessageDto {
  content: string;
  stream?: boolean;
}
"""


def _endpoints(doc):
    return [n for n in doc.nodes if n.component_type == ComponentType.API_ENDPOINT]


def test_nestjs_controller_produces_endpoint_nodes(tmp_path):
    (tmp_path / "chat.controller.ts").write_text(_CONTROLLER, encoding="utf-8")
    (tmp_path / "chat.service.ts").write_text(_SERVICE, encoding="utf-8")

    doc = AiSbomExtractor().extract_from_path(tmp_path, _TS_ONLY)
    eps = _endpoints(doc)

    assert len(eps) >= 4, f"expected >=4 API_ENDPOINT nodes, got {len(eps)}: {[e.name for e in eps]}"


def test_similarly_named_sibling_routes_all_survive_dedup(tmp_path):
    """Regression guard: sendMessage/sendMessageStream/sendMessageWithFiles
    are three distinct routes sharing a display-name prefix in the same
    file — _dedup_by_name_prefix must not collapse them (was dropping the
    real 'sendMessage' chat route entirely before the API_ENDPOINT
    exclusion fix in extractor/core.py)."""
    (tmp_path / "chat.controller.ts").write_text(_CONTROLLER, encoding="utf-8")
    (tmp_path / "chat.service.ts").write_text(_SERVICE, encoding="utf-8")

    doc = AiSbomExtractor().extract_from_path(tmp_path, _TS_ONLY)
    paths = {(n.metadata.method, n.metadata.endpoint) for n in _endpoints(doc)}

    assert ("POST", "/chat/conversations/:id/messages") in paths
    assert ("POST", "/chat/conversations/:id/messages/stream") in paths
    assert ("POST", "/chat/conversations/:id/messages/upload") in paths


def test_real_chat_endpoint_resolves_content_payload_key_cross_file(tmp_path):
    """The chat_payload_key comes from SendMessageDto, declared in a sibling
    file (chat.service.ts) — exercises the cross-file TS DTO pre-pass."""
    (tmp_path / "chat.controller.ts").write_text(_CONTROLLER, encoding="utf-8")
    (tmp_path / "chat.service.ts").write_text(_SERVICE, encoding="utf-8")

    doc = AiSbomExtractor().extract_from_path(tmp_path, _TS_ONLY)
    by_path = {n.metadata.endpoint: n for n in _endpoints(doc)}

    send = by_path["/chat/conversations/:id/messages"]
    assert send.metadata.chat_payload_key == "content"
