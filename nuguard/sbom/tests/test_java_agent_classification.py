"""Which Java types become agents (NuGuardAI/nuguard#641).

A file that merely holds ``@Tool`` methods, a ``@Configuration`` class or a controller is not an
agent. ``@AiService`` / ``@RegisterAiService`` interfaces are, even though their body has no AI
call. Files that import an AI package without any other signal keep the old fallback.
"""

from __future__ import annotations

from nuguard.sbom.adapters.java import JavaAIAdapter
from nuguard.sbom.core.java_parser import parse_java
from nuguard.sbom.types import ComponentType


def _extract(source: str, path: str) -> list:
    parsed = parse_java(source, path)
    return JavaAIAdapter().extract(source, path, parsed)


def _names(detections: list, kind: ComponentType) -> list[str]:
    return [item.display_name for item in detections if item.component_type == kind]


_TOOL_HOLDER = """package demo;

import dev.langchain4j.agent.tool.Tool;
import org.springframework.stereotype.Component;

@Component
public class BookingTools {
    private final BookingService bookingService;

    public BookingTools(BookingService bookingService) { this.bookingService = bookingService; }

    @Tool
    public Booking getBookingDetails(String number) { return bookingService.find(number); }

    @Tool
    public void cancelBooking(String number) { bookingService.cancel(number); }
}
"""


def test_a_tool_holder_without_ai_usage_is_not_an_agent() -> None:
    detections = _extract(_TOOL_HOLDER, "BookingTools.java")
    assert _names(detections, ComponentType.AGENT) == []
    assert _names(detections, ComponentType.TOOL) == ["getBookingDetails", "cancelBooking"]


def test_an_ai_service_interface_is_an_agent() -> None:
    source = """package demo;

import dev.langchain4j.service.MemoryId;
import dev.langchain4j.service.UserMessage;
import dev.langchain4j.service.spring.AiService;

@AiService
public interface SupportAgent {
    String answer(@MemoryId String id, @UserMessage String message);
}
"""
    detections = _extract(source, "SupportAgent.java")
    assert _names(detections, ComponentType.AGENT) == ["SupportAgent"]


def test_a_quarkus_register_ai_service_interface_is_an_agent() -> None:
    source = """package demo;

import io.quarkiverse.langchain4j.RegisterAiService;

@RegisterAiService
public interface Assistant {
    String chat(String message);
}
"""
    detections = _extract(source, "Assistant.java")
    assert _names(detections, ComponentType.AGENT) == ["Assistant"]


def test_a_configuration_class_without_ai_usage_is_not_an_agent() -> None:
    source = """package demo;

import dev.langchain4j.model.embedding.EmbeddingModel;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class AppConfiguration {
    @Bean
    String name() { return "demo"; }
}
"""
    assert _names(_extract(source, "AppConfiguration.java"), ComponentType.AGENT) == []


def test_a_controller_without_ai_usage_is_not_an_agent_of_the_ai_adapter() -> None:
    source = """package demo;

import dev.langchain4j.service.Result;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class SupportController {
    private final SupportAgent agent;

    public SupportController(SupportAgent agent) { this.agent = agent; }

    @GetMapping("/support")
    public String support(String message) { return agent.answer("1", message).content(); }
}
"""
    assert _names(_extract(source, "SupportController.java"), ComponentType.AGENT) == []


def test_a_file_with_an_ai_import_and_no_other_signal_keeps_the_fallback_agent() -> None:
    source = """package demo;

import dev.langchain4j.data.message.UserMessage;

public class Helper {
    void run() { }
}
"""
    assert _names(_extract(source, "Helper.java"), ComponentType.AGENT) == ["Helper"]


def test_a_controller_that_calls_the_model_directly_is_still_an_agent() -> None:
    source = """package demo;

import org.springframework.ai.chat.client.ChatClient;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ChatController {
    private final ChatClient client;

    public ChatController(ChatClient client) { this.client = client; }

    @PostMapping("/chat")
    public String chat(String message) { return client.prompt().user(message).call().content(); }
}
"""
    assert _names(_extract(source, "ChatController.java"), ComponentType.AGENT) == [
        "ChatController"
    ]


def test_tools_and_an_ai_service_in_one_file_link_the_agent_to_the_tools() -> None:
    source = """package demo;

import dev.langchain4j.agent.tool.Tool;
import dev.langchain4j.service.spring.AiService;

@AiService
public interface Agent {
    String answer(String message);
}

class Tools {
    @Tool
    String lookup(String id) { return id; }
}
"""
    detections = _extract(source, "Agent.java")
    assert _names(detections, ComponentType.AGENT) == ["Agent"]
    assert _names(detections, ComponentType.TOOL) == ["lookup"]
    agent = next(item for item in detections if item.component_type == ComponentType.AGENT)
    assert any(rel.target_type == ComponentType.TOOL for rel in agent.relationships)
