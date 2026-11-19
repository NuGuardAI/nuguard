"""Tool privilege heuristics of the Java AI adapter (NuGuardAI/nuguard#644).

Write verbs must be found in camelCase method names (``cancelBooking``, ``deleteTicket``) and in
the calls made by the method body, not only as standalone lowercase words.
"""

from __future__ import annotations

import pytest

from nuguard.sbom.adapters.java import JavaAIAdapter
from nuguard.sbom.core.java_parser import parse_java
from nuguard.sbom.types import ComponentType

_TEMPLATE = """package demo;

import dev.langchain4j.agent.tool.Tool;
import org.springframework.stereotype.Component;

@Component
public class DemoTools {{
    @Tool("demo tool")
    public String {name}(String id) {{ {body} }}
}}
"""


def _privilege_scope(name: str, body: str) -> list[str]:
    source = _TEMPLATE.format(name=name, body=body)
    parsed = parse_java(source, "DemoTools.java")
    detections = JavaAIAdapter().extract(source, "DemoTools.java", parsed)
    tools = [item for item in detections if item.component_type == ComponentType.TOOL]
    assert len(tools) == 1
    return list(tools[0].metadata["privilege_scope"])


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("cancelBooking", "return bookingService.cancelBooking(id);"),
        ("deleteTicket", "return repo.remove(id);"),
        ("saveOrder", "return svc.store(id);"),
        ("updateProfile", "return users.apply(id);"),
        ("refundPayment", "return payments.process(id);"),
        ("purgeCache", "return cache.clear();"),
        ("removeItem", "return cart.apply(id);"),
    ],
)
def test_write_verb_in_camel_case_method_name_is_db_write(name: str, body: str) -> None:
    assert "db_write" in _privilege_scope(name, body)


def test_write_verb_in_a_called_method_is_db_write() -> None:
    assert "db_write" in _privilege_scope("handle", "return bookingService.cancelBooking(id);")


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("getBookingDetails", "return bookingService.getBookingDetails(id);"),
        ("lookup", "return repo.find(id);"),
        ("findCustomer", "return customers.find(id);"),
        ("listTickets", "return tickets.list();"),
    ],
)
def test_read_only_tools_are_not_db_write(name: str, body: str) -> None:
    assert "db_write" not in _privilege_scope(name, body)


def test_other_privilege_scopes_are_unchanged() -> None:
    assert "code_execution" in _privilege_scope(
        "runIt", "return new ProcessBuilder(id).start().toString();"
    )
    assert "email_out" in _privilege_scope("sendMail", "return mailer.send(id);")
