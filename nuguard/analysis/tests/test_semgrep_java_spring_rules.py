"""Spring MVC inputs and LangChain4j ``@AiService`` calls in the Java Semgrep rules (#645)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from nuguard.analysis.plugins.java_aiservice import (
    augment_java_rules,
    find_ai_service_interfaces,
)
from nuguard.analysis.plugins.semgrep_scanner import SemgrepScannerPlugin

_RULES = Path(__file__).parents[1] / "plugins" / "semgrep_rules" / "java-ai-security.yaml"
_PROMPT = "java-ai-prompt-injection"
_RESPONSE = "java-ai-unvalidated-controller-response"

_HEADER = """package demo;
import org.springframework.web.bind.annotation.*;
import dev.langchain4j.service.Result;
"""

_JAVA_FILES = {
    "SupportAgent.java": """package demo;
import dev.langchain4j.service.spring.AiService;
@AiService
public interface SupportAgent {
    Result<String> answer(@MemoryId String id, @UserMessage String msg);
    String chat(@UserMessage String msg);
}
""",
    "QuarkusAgent.java": """package demo;
@RegisterAiService(tools = {Foo.class})
public interface QuarkusAgent {
    String ask(@UserMessage String q);
}
""",
    "ResultController.java": _HEADER
    + """@RestController
public class ResultController {
    private final SupportAgent agent;
    @GetMapping("/a")
    public String a(@RequestParam String sessionId, @RequestParam String userMessage) {
        Result<String> result = agent.answer(sessionId, userMessage);
        return result.content();
    }
}
""",
    "VariantsController.java": _HEADER
    + """@RestController
public class VariantsController {
    private final SupportAgent agent;
    private final QuarkusAgent quarkus;
    @PostMapping("/b")
    public String b(@RequestBody String body) {
        return agent.chat(body);
    }
    @GetMapping("/c/{id}")
    public String c(@PathVariable("id") String id) {
        return agent.chat(id);
    }
    @GetMapping("/d")
    public String d(@RequestParam(name = "q", required = false) String q) {
        return quarkus.ask(q);
    }
    @GetMapping("/e")
    public String e(@RequestHeader("X-Msg") String msg) {
        return agent.chat(msg);
    }
}
""",
    "StaticController.java": _HEADER
    + """@RestController
public class StaticController {
    private final ChatClient client;
    @GetMapping("/s")
    public String s(@RequestParam String message) {
        return client.prompt(message).call().content();
    }
}
""",
    "SafeController.java": _HEADER
    + """@RestController
public class SafeController {
    private final SupportAgent agent;
    private final Validator validator;
    @GetMapping("/f")
    public String f(@RequestParam String q) {
        String clean = validator.validate(q);
        return render(agent.chat(clean));
    }
    @GetMapping("/g")
    public String g() {
        return render(agent.chat("fixed"));
    }
    @GetMapping("/i")
    public String i(@RequestParam String q) {
        return q;
    }
    String render(String s) {
        return s;
    }
}
""",
}


def test_bundled_rules_declare_spring_mvc_sources() -> None:
    text = _RULES.read_text(encoding="utf-8")
    for annotation in ("@RequestParam", "@RequestBody", "@PathVariable", "@RequestHeader"):
        assert f"{annotation} $TYPE $SOURCE" in text
        assert f"{annotation}(...) $TYPE $SOURCE" in text


def test_find_ai_service_interfaces(tmp_path: Path) -> None:
    (tmp_path / "A.java").write_text("@AiService\npublic interface SupportAgent {}\n")
    (tmp_path / "B.java").write_text(
        "@RegisterAiService(tools = {Foo.class, Bar.class})\n@Singleton\ninterface Quarkus {}\n"
    )
    (tmp_path / "C.java").write_text("// @AiService\npublic class NotAnInterface {}\n")
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "D.java").write_text("@AiService\ninterface Built {}\n")

    assert find_ai_service_interfaces([tmp_path]) == ["Quarkus", "SupportAgent"]


def test_augment_adds_typed_sinks_without_mutating_input() -> None:
    rules = yaml.safe_load(_RULES.read_text(encoding="utf-8"))
    before = yaml.safe_dump(rules)

    out = augment_java_rules(rules, ["SupportAgent"])

    assert yaml.safe_dump(rules) == before
    by_id = {rule["id"]: rule for rule in out["rules"]}
    sinks = by_id[_PROMPT]["pattern-sinks"][0]["pattern-either"]
    assert {"pattern": "(SupportAgent $SVC).$M(...)"} in sinks
    alternatives = by_id[_RESPONSE]["patterns"][1]["pattern-either"]
    assert {"pattern": "return (SupportAgent $SVC).$M(...);"} in alternatives
    assert augment_java_rules(rules, []) == rules


@pytest.fixture
def findings(tmp_path: Path) -> dict[str, dict[str, set[int]]]:
    if shutil.which("semgrep") is None:
        pytest.skip("semgrep is not installed")
    src = tmp_path / "src"
    src.mkdir()
    for name, text in _JAVA_FILES.items():
        (src / name).write_text(text, encoding="utf-8")
    result = SemgrepScannerPlugin().run(
        {"nodes": []}, {"source_path": str(src), "semgrep_exclude_tests": False}
    )
    assert result.status != "skipped", result.message
    found: dict[str, dict[str, set[int]]] = {}
    for finding in result.findings:
        path, _, line = finding["affected"][0].rpartition(":")
        rule = finding["rule_id"].split(".")[-1]
        found.setdefault(Path(path).name, {}).setdefault(rule, set()).add(int(line))
    return found


def test_request_params_reach_ai_service_and_response(
    findings: dict[str, dict[str, set[int]]],
) -> None:
    flows = findings["ResultController.java"]
    assert len(flows[_PROMPT]) == 1
    assert len(flows[_RESPONSE]) == 1


def test_spring_annotation_variants_and_quarkus_interface(
    findings: dict[str, dict[str, set[int]]],
) -> None:
    flows = findings["VariantsController.java"]
    assert len(flows[_PROMPT]) == 4
    assert len(flows[_RESPONSE]) == 4


def test_request_param_reaches_existing_prompt_sink(
    findings: dict[str, dict[str, set[int]]],
) -> None:
    assert _PROMPT in findings["StaticController.java"]


def test_sanitized_constant_and_unrelated_flows_are_not_reported(
    findings: dict[str, dict[str, set[int]]],
) -> None:
    assert "SafeController.java" not in findings


def test_rule_ids_do_not_leak_the_temporary_rules_directory(tmp_path: Path) -> None:
    if shutil.which("semgrep") is None:
        pytest.skip("semgrep is not installed")
    src = tmp_path / "src"
    src.mkdir()
    for name in ("SupportAgent.java", "ResultController.java"):
        (src / name).write_text(_JAVA_FILES[name], encoding="utf-8")
    config = {"source_path": str(src), "semgrep_exclude_tests": False}

    runs = [
        {f["rule_id"] for f in SemgrepScannerPlugin().run({"nodes": []}, config).findings}
        for _ in range(2)
    ]

    assert runs[0] and runs[0] == runs[1]
    assert not any("nuguard-semgrep-" in rule_id for rule_id in runs[0])
