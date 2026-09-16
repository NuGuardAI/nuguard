from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_RULES = Path(__file__).parents[1] / "plugins" / "semgrep_rules" / "java-ai-security.yaml"


def test_java_rules_are_bundled_and_well_formed() -> None:
    data = yaml.safe_load(_RULES.read_text(encoding="utf-8"))
    ids = {item["id"] for item in data["rules"]}
    assert ids == {
        "java-ai-prompt-injection",
        "java-ai-llm-response-deserialization",
        "java-ai-jackson-default-typing",
        "java-ai-unvalidated-controller-response",
    }


def test_java_rules_detect_representative_unsafe_flows(tmp_path: Path) -> None:
    semgrep = shutil.which("semgrep")
    if semgrep is None:
        pytest.skip("semgrep executable is not installed")

    source = tmp_path / "UnsafeController.java"
    source.write_text(
        """import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.*;

@RestController
class UnsafeController {
  private final ObjectMapper mapper = new ObjectMapper().enableDefaultTyping();

  @PostMapping("/chat")
  String chat(HttpServletRequest request) {
    String user = request.getParameter("message");
    return client.prompt(String.format("User: %s", user)).call().content();
  }

  Object parse(ChatResponse response) throws Exception {
    String output = response.content();
    return mapper.readValue(output, Object.class);
  }
}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [semgrep, "--json", "--quiet", "--config", str(_RULES), str(source)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode in {0, 1}, completed.stderr
    findings = json.loads(completed.stdout).get("results", [])
    ids = {item["check_id"].split(".")[-1] for item in findings}
    assert "java-ai-prompt-injection" in ids
    assert "java-ai-llm-response-deserialization" in ids
    assert "java-ai-jackson-default-typing" in ids
    assert "java-ai-unvalidated-controller-response" in ids
