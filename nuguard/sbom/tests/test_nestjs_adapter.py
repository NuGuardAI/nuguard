"""Unit tests for NestJSAdapter (nuguard/sbom/adapters/typescript/nestjs_adapter.py).

Fixture shape mirrors the real gap this adapter closes: Studyield's
``chat.controller.ts`` (see tests/apps/studyield-app/studyield-sbom-fix.md
item #1) — zero API_ENDPOINT nodes were extracted for any NestJS/Express
backend before this adapter existed, causing redteam/behavior chat-endpoint
auto-discovery to fail outright.
"""

from __future__ import annotations

from nuguard.sbom.adapters.typescript.nestjs_adapter import (
    NestJSAdapter,
    collect_dto_schemas,
)
from nuguard.sbom.types import ComponentType

_CONTROLLER = '''
import { Controller, Get, Post, Body, Param, UseGuards } from '@nestjs/common';
import { JwtAuthGuard } from '../auth/jwt-auth.guard';
import { ChatService, SendMessageDto } from './chat.service';

@Controller('chat')
@UseGuards(JwtAuthGuard)
export class ChatController {
  constructor(private readonly chatService: ChatService) {}

  @Get('conversations')
  async getConversations() {
    return this.chatService.getConversations();
  }

  @Post('conversations/:id/messages')
  async sendMessage(
    @Param('id') id: string,
    @Body() dto: SendMessageDto,
  ) {
    return this.chatService.sendMessage(id, dto);
  }

  @Post('conversations/:id/messages/stream')
  async sendMessageStream(
    @Param('id') id: string,
    @Body() dto: SendMessageDto,
  ) {
    return this.chatService.sendMessageStream(id, dto);
  }
}
'''

_SERVICE_DTOS = '''
export interface SendMessageDto {
  content: string;
  stream?: boolean;
}
'''

_PUBLIC_ROUTE_CONTROLLER = '''
import { Controller, Post, Body, UseGuards } from '@nestjs/common';
import { Public } from '../auth/public.decorator';

@Controller('auth')
@UseGuards(JwtAuthGuard)
export class AuthController {
  @Public()
  @Post('login')
  async login(@Body() dto: LoginDto) {
    return {};
  }

  @Post('logout')
  async logout() {
    return {};
  }
}
'''


def _endpoints(dets: list) -> list:
    return [d for d in dets if d.component_type == ComponentType.API_ENDPOINT]


class TestRouteAndPrefixComposition:
    def test_controller_prefix_composed_with_method_paths(self) -> None:
        adapter = NestJSAdapter()
        dets = _endpoints(adapter.extract(_CONTROLLER, "chat.controller.ts", None))
        paths = {(d.metadata["method"], d.metadata["endpoint"]) for d in dets}
        assert ("GET", "/chat/conversations") in paths
        assert ("POST", "/chat/conversations/:id/messages") in paths
        assert ("POST", "/chat/conversations/:id/messages/stream") in paths

    def test_class_level_guard_marks_all_routes_auth_required(self) -> None:
        adapter = NestJSAdapter()
        dets = _endpoints(adapter.extract(_CONTROLLER, "chat.controller.ts", None))
        assert all(d.metadata["auth_required"] for d in dets)
        assert all(d.metadata.get("auth_type") == "guard" for d in dets)


class TestPublicOverride:
    def test_public_decorator_overrides_class_level_guard(self) -> None:
        adapter = NestJSAdapter()
        dets = _endpoints(adapter.extract(_PUBLIC_ROUTE_CONTROLLER, "auth.controller.ts", None))
        by_path = {d.metadata["endpoint"]: d for d in dets}
        assert by_path["/auth/login"].metadata["auth_required"] is False
        assert by_path["/auth/logout"].metadata["auth_required"] is True


class TestDistinctSimilarlyNamedRoutesNotCrossContaminated:
    """sendMessage's window must not bleed into sendMessageStream's @Body()/DTO,
    and vice versa — regression guard for the lookahead-bounding fix."""

    def test_each_route_gets_its_own_endpoint_node(self) -> None:
        adapter = NestJSAdapter()
        adapter.set_global_model_schemas(collect_dto_schemas(_SERVICE_DTOS))
        dets = _endpoints(adapter.extract(_CONTROLLER, "chat.controller.ts", None))
        by_path = {d.metadata["endpoint"]: d for d in dets}
        assert "/chat/conversations/:id/messages" in by_path
        assert "/chat/conversations/:id/messages/stream" in by_path
        # GET has no @Body() of its own — must not inherit the next POST's DTO.
        assert by_path["/chat/conversations"].metadata.get("chat_payload_key") is None


class TestCrossFileDtoResolution:
    def test_body_dto_resolved_from_global_schema_index(self) -> None:
        dto_schemas = collect_dto_schemas(_SERVICE_DTOS)
        assert dto_schemas["SendMessageDto"] == {"content": "string", "stream": "boolean"}

        adapter = NestJSAdapter()
        adapter.set_global_model_schemas(dto_schemas)
        dets = _endpoints(adapter.extract(_CONTROLLER, "chat.controller.ts", None))
        by_path = {d.metadata["endpoint"]: d for d in dets}

        send = by_path["/chat/conversations/:id/messages"]
        assert send.metadata["chat_payload_key"] == "content"
        assert send.metadata["chat_payload_list"] is False

    def test_no_global_schema_leaves_chat_payload_key_unset(self) -> None:
        adapter = NestJSAdapter()
        dets = _endpoints(adapter.extract(_CONTROLLER, "chat.controller.ts", None))
        by_path = {d.metadata["endpoint"]: d for d in dets}
        assert "chat_payload_key" not in by_path["/chat/conversations/:id/messages"].metadata


class TestMessageHistoryDto:
    def test_list_dict_messages_field_sets_chat_payload_list(self) -> None:
        dto_src = '''
export interface SendHistoryDto {
  messages: ChatMessage[];
}
'''
        controller = '''
import { Controller, Post, Body } from '@nestjs/common';

@Controller('agent')
export class AgentController {
  @Post('respond')
  async respond(@Body() dto: SendHistoryDto) {
    return {};
  }
}
'''
        adapter = NestJSAdapter()
        adapter.set_global_model_schemas(collect_dto_schemas(dto_src))
        dets = _endpoints(adapter.extract(controller, "agent.controller.ts", None))
        ep = next(d for d in dets if d.metadata["endpoint"] == "/agent/respond")
        assert ep.metadata["chat_payload_key"] == "messages"
        assert ep.metadata["chat_payload_list"] is True


class TestCollectDtoSchemas:
    def test_interface_fields_extracted(self) -> None:
        schemas = collect_dto_schemas(_SERVICE_DTOS)
        assert schemas["SendMessageDto"]["content"] == "string"
        assert schemas["SendMessageDto"]["stream"] == "boolean"

    def test_no_dto_returns_empty(self) -> None:
        assert collect_dto_schemas("const x = 1;\n") == {}
