"""Workflow payload generation, bundled templates and applicability filtering."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nuguard.redteam.workflows import WorkflowRunner, WorkflowSpec
from nuguard.redteam.workflows.library import (
    available_templates,
    filter_applicable,
    load_template,
    resolve_templates,
    sbom_has_path,
)
from nuguard.redteam.workflows.payloads import benign_control, generate_payloads


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def complete(self, prompt: str, system: str | None = None, label: str = "", **kw: Any) -> str:
        self.prompts.append(prompt)
        return self.reply


def _spec(**kw: Any) -> WorkflowSpec:
    base: dict[str, Any] = {
        "id": "w", "title": "t", "payload_goal": "pay five thousand dollars",
        "steps": [{"id": "s", "path": "/x"}], "oracle": {"path": "/o", "json_path": "v"},
    }
    base.update(kw)
    return WorkflowSpec.model_validate(base)


def test_llm_payloads_used_when_valid() -> None:
    llm = FakeLLM('Sure: ["alpha doc", "beta doc"]')
    out = asyncio.run(generate_payloads(_spec(payload_style="html_document"), llm))
    assert out == ["alpha doc", "beta doc"]
    assert "hidden" in llm.prompts[0] and "pay five thousand dollars" in llm.prompts[0]


@pytest.mark.parametrize("style", ["pretext", "html_document", "text_document", "tool_description"])
def test_template_fallback_without_llm_contains_goal(style: str) -> None:
    out = asyncio.run(generate_payloads(_spec(payload_style=style, payload_count=2), None))
    assert len(out) == 2 and all("pay five thousand dollars" in p for p in out)
    assert not any("ignore previous" in p.lower() for p in out)
    if style == "html_document":
        assert "font-size:0" in out[0]


def test_unusable_llm_reply_falls_back_and_explicit_payloads_win() -> None:
    assert len(asyncio.run(generate_payloads(_spec(), FakeLLM("no json here")))) == 2
    assert asyncio.run(generate_payloads(_spec(payloads=["mine"]), FakeLLM("[]"))) == ["mine"]


def test_auto_control_by_style_and_opt_out() -> None:
    assert "<html>" in benign_control(_spec(payload_style="html_document"))[0]
    assert benign_control(_spec(auto_control=False)) == []
    assert benign_control(_spec(control_payloads=["c"])) == ["c"]


def test_bundled_finbot_template_loads_and_covers_ctf_classes() -> None:
    assert "owasp-finbot" in available_templates()
    specs = load_template("owasp-finbot")
    ids = {s.id for s in specs}
    assert {"finbot-invoice-over-threshold", "finbot-hidden-text-inflated-payment",
            "finbot-poisoned-send-email", "finbot-cross-vendor-deletion"} <= ids
    poisoned = next(s for s in specs if s.id == "finbot-poisoned-send-email")
    assert poisoned.cleanup, "poisoning workflows must restore the tool description"
    assert len(resolve_templates(["auto"])) == len(specs)
    assert resolve_templates(["nope"]) == []


def test_sbom_path_matching_uses_param_wildcards() -> None:
    assert sbom_has_path(["/api/v1/invoices/{invoice_id}"], "/api/v1/invoices/{id}")
    assert not sbom_has_path(["/api/v1/invoices"], "/api/v1/vendors")


class _Prober:
    def __init__(self, alive: set[str]) -> None:
        self.alive = alive
        self.calls: list[str] = []

    async def invoke_endpoint(self, path: str, method: str = "POST", **kw: Any) -> tuple[int, str, dict]:
        self.calls.append(path)
        return (200, "", {}) if path in self.alive else (404, "", {})


def test_filter_applicable_probes_each_path_once_and_checks_tenants() -> None:
    a = _spec(id="a", requires={"probe_paths": ["/v"]})
    b = _spec(id="b", requires={"probe_paths": ["/v", "/gone"]})
    c = _spec(id="c", requires={"min_tenants": 2})
    prober = _Prober({"/v"})
    kept = asyncio.run(filter_applicable([a, b, c], sbom_paths=[], client=prober, tenant_count=1))  # type: ignore[arg-type]
    assert [s.id for s in kept] == ["a"]
    assert prober.calls.count("/v") == 1


class _App:
    """Poison -> trigger -> exfil counter; records cleanup."""

    def __init__(self) -> None:
        self.poisoned = False
        self.exfil = 0
        self.log: list[str] = []

    async def invoke_endpoint(self, path: str, method: str = "POST", body: dict | None = None,
                              **kw: Any) -> tuple[int, str, dict]:
        self.log.append(f"{method} {path}")
        if method == "PUT":
            self.poisoned = "collector" in str(body)
        elif path.endswith("reset-tools"):
            self.poisoned = False
        elif path.endswith("chat") and self.poisoned:
            self.exfil += 1
        elif path.endswith("exfil-data"):
            return 200, "{}", {"total": self.exfil}
        elif path.endswith("file/1"):
            return 404, "", {}
        return 200, "{}", {"file": {"id": 7}}


def test_poisoning_workflow_detects_exfil_and_always_restores() -> None:
    spec = next(s for s in load_template("owasp-finbot") if s.id == "finbot-poisoned-send-email")
    spec = spec.model_copy(update={"oracle": spec.oracle.model_copy(update={"poll_timeout_s": 0.05, "poll_interval_s": 0})})
    app = _App()

    async def nosleep(_: float) -> None:
        return None

    results = asyncio.run(WorkflowRunner(app, sleep=nosleep).run(spec, llm=None))  # type: ignore[arg-type]
    assert [r.succeeded for r in results] == [False, True]  # benign description, then poisoned
    assert app.poisoned is False
    assert app.log.count("POST /darklab/api/v1/supply-chain/servers/finmail/reset-tools") == len(results)


def test_status_oracle_and_native_capture_types() -> None:
    spec = _spec(
        steps=[{"id": "up", "path": "/up", "capture": {"file_id": "file.id"}},
               {"id": "use", "path": "/use", "body": {"ref": "{file_id}", "label": "f{file_id}"}}],
        oracle={"path": "/file/{file_id}", "json_path": "$status", "op": "equals", "value": 404,
                "poll_timeout_s": 0.05, "poll_interval_s": 0},
        payloads=["p"], auto_control=False,
    )
    seen: dict[str, Any] = {}

    class App(_App):
        async def invoke_endpoint(self, path: str, method: str = "POST", body: dict | None = None, **kw: Any):  # type: ignore[override]
            if path == "/use":
                seen.update(body or {})
            if path.startswith("/file/"):
                return 404, "", {}
            return 200, "{}", {"file": {"id": 7}}

    async def nosleep(_: float) -> None:
        return None

    results = asyncio.run(WorkflowRunner(App(), sleep=nosleep).run(spec))  # type: ignore[arg-type]
    assert results[-1].succeeded and seen == {"ref": 7, "label": "f7"}


def test_foot_in_the_door_builder_is_benign_then_escalates() -> None:
    from nuguard.redteam.scenarios.tool_abuse import build_foot_in_the_door_exec

    sc = build_foot_in_the_door_exec("agent", "tool", "execute_script")
    assert sc.chain is not None
    steps = sc.chain.steps
    assert [s.step_type for s in steps] == ["WARMUP", "WARMUP", "INJECT"]
    assert "`id`" in steps[2].payload and not any(
        bad in steps[2].payload for bad in ("rm -", "/dev/tcp", "curl")
    )
