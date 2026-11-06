"""Regression gate for the deterministic Go SBOM of eino-support-copilot.

Checks stable facts from EXPECTED_SBOM.md (minimums and presence, not exact totals)
so independent SBOM improvements do not break it.

Usage: python check_sbom.py <sbom.json>
"""

import json
import sys
from collections import Counter

MIN_NODES = 33
MIN_EDGES = 22
MIN_TYPE_COUNTS = {
    "AGENT": 1,
    "API_ENDPOINT": 2,
    "AUTH": 1,
    "DATASTORE": 1,
    "FRAMEWORK": 3,
    "MCP_SERVER": 1,
    "MODEL": 1,
    "PROMPT": 1,
    "TOOL": 4,
}
MCP_TOOLS = ("get_account_status", "get_payment_status", "get_service_status", "refund_payment")
FRAMEWORKS = ("eino", "mcp go", "net/http")


def _norm(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def check(doc: dict) -> list[str]:
    """Return a list of failure messages (empty when the SBOM passes)."""
    nodes = doc.get("nodes") or []
    edges = doc.get("edges") or []
    by_id = {n.get("id"): n for n in nodes}
    types = Counter(n.get("component_type") for n in nodes)
    failures: list[str] = []

    if len(nodes) < MIN_NODES:
        failures.append(f"nodes {len(nodes)} < {MIN_NODES}")
    if len(edges) < MIN_EDGES:
        failures.append(f"edges {len(edges)} < {MIN_EDGES}")
    for ctype, minimum in MIN_TYPE_COUNTS.items():
        if types[ctype] < minimum:
            failures.append(f"{ctype} count {types[ctype]} < {minimum}")

    def names(ctype: str) -> list[str]:
        return [str(n.get("name", "")) for n in nodes if n.get("component_type") == ctype]

    if not any("support copilot" in n.lower() for n in names("AGENT")):
        failures.append(f"no AGENT named like 'Support Copilot' (got {names('AGENT')})")

    tool_ids = {
        _norm(str(n.get("name", ""))): n.get("id")
        for n in nodes
        if n.get("component_type") == "TOOL"
    }
    called = {e.get("target") for e in edges if e.get("relationship_type") == "CALLS"}
    for tool in MCP_TOOLS:
        if tool not in tool_ids:
            failures.append(f"missing MCP TOOL {tool}")
        elif tool_ids[tool] not in called:
            failures.append(f"MCP TOOL {tool} has no incoming CALLS edge")

    fw = [n.lower() for n in names("FRAMEWORK")]
    for name in FRAMEWORKS:
        if name not in fw:
            failures.append(f"missing FRAMEWORK {name} (got {names('FRAMEWORK')})")

    if not any("postgres" in n.lower() for n in names("DATASTORE")):
        failures.append(f"missing DATASTORE Postgres (got {names('DATASTORE')})")

    endpoints = {
        (n.get("metadata") or {}).get("endpoint")
        for n in nodes
        if n.get("component_type") == "API_ENDPOINT"
    }
    if "/mcp" not in endpoints:
        failures.append(f"missing API_ENDPOINT /mcp (got {sorted(map(str, endpoints))})")

    jwt_ids = {
        n.get("id")
        for n in nodes
        if n.get("component_type") == "AUTH"
        and str((n.get("metadata") or {}).get("auth_type", n.get("name", ""))).lower() == "jwt"
    }
    if not jwt_ids:
        failures.append("missing AUTH jwt")
    protected = [
        e.get("target")
        for e in edges
        if e.get("relationship_type") == "PROTECTS" and e.get("source") in jwt_ids
    ]
    if not any(by_id.get(t, {}).get("component_type") == "API_ENDPOINT" for t in protected):
        failures.append("AUTH jwt has no PROTECTS edge to an API_ENDPOINT")

    return failures


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
    failures = check(doc)
    nodes, edges = len(doc.get("nodes") or []), len(doc.get("edges") or [])
    if failures:
        print(f"SBOM regression ({nodes} nodes, {edges} edges):")
        for msg in failures:
            print(f"  FAIL {msg}")
        return 1
    print(f"SBOM check passed: {nodes} nodes, {edges} edges")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
