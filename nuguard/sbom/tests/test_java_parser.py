from __future__ import annotations

from nuguard.sbom.core.java_parser import parse_java


def test_parse_java_extracts_structures_and_locations() -> None:
    source = '''package demo;

import org.springframework.ai.chat.client.ChatClient;
import static java.util.Objects.requireNonNull;

@RestController
@RequestMapping("/api")
public class ChatController {
    private static final String SYSTEM_PROMPT = "You are a safe support assistant.";

    @PostMapping("/chat")
    @PreAuthorize("hasRole('USER')")
    public String chat(@RequestParam String message) {
        String prompt = String.format("User: %s", message);
        return client.prompt(prompt).call().content();
    }

    public String instructions() {
        return """
            You are a careful assistant.
            Respond with JSON only.
            """;
    }
}
'''
    result = parse_java(source, "src/main/java/demo/ChatController.java")

    assert result.parse_error is None
    assert [item.module for item in result.imports] == [
        "org.springframework.ai.chat.client.ChatClient",
        "java.util.Objects.requireNonNull",
    ]
    assert result.imports[1].is_static is True
    assert result.type_declarations[0].name == "ChatController"
    assert "@RestController" in result.type_declarations[0].annotations

    chat = next(item for item in result.method_declarations if item.name == "chat")
    assert chat.containing_type == "ChatController"
    assert chat.line == 11
    assert "@PostMapping" in chat.annotations[0]
    assert chat.parameters == ("@RequestParam String message",)

    prompt = next(item for item in result.string_literals if item.assigned_to == "SYSTEM_PROMPT")
    assert prompt.line == 9
    assert prompt.is_potential_prompt is True
    text_block = next(item for item in result.string_literals if item.is_text_block)
    assert text_block.enclosing_method == "instructions"
    assert "Respond with JSON only" in text_block.value


def test_parse_java_reports_unclosed_source_without_raising() -> None:
    result = parse_java('class Broken { String value = "unterminated\n', "Broken.java")
    assert result.parse_error
    assert "Unterminated string" in result.parse_error
