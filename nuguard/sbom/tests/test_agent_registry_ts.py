"""Tests for AgentRegistryTSAdapter (docs/sbom-accuracy-plan.md #3):
config-driven "agent registry" detection — a named-map object where each
entry looks like an agent config (model/prompt/system/permission/tools
field cluster), a shape no existing adapter recognizes since it isn't a
direct SDK call site or class instantiation.
"""
from __future__ import annotations

from nuguard.sbom.adapters.typescript.agent_registry import AgentRegistryTSAdapter
from nuguard.sbom.types import ComponentType


def _by_type(detections, component_type):
    return [d for d in detections if d.component_type == component_type]


_REGISTRY_TS = """
export const AGENTS = {
  build: {
    model: "claude-sonnet-4-6",
    prompt: "You are the build agent.",
    permission: { write: true },
    tools: ["bash", "edit"],
  },
  plan: {
    model: "claude-haiku-4-5",
    prompt: "You are the planning agent.",
  },
  general: {
    system: "You are a general-purpose assistant.",
    tools: ["read"],
  },
};
"""


def test_named_map_with_agent_shape_emits_one_agent_per_entry():
    dets = AgentRegistryTSAdapter().extract(_REGISTRY_TS, "src/agents/config.ts", None)
    agents = _by_type(dets, ComponentType.AGENT)
    names = {a.display_name for a in agents}
    assert names == {"build", "plan", "general"}


def test_partial_field_cluster_still_matches():
    dets = AgentRegistryTSAdapter().extract(_REGISTRY_TS, "src/agents/config.ts", None)
    plan = next(d for d in dets if d.display_name == "plan")
    assert set(plan.metadata["matched_fields"]) == {"model", "prompt"}


def test_unrelated_config_map_not_flagged():
    code = """
    export const ROUTES = {
      home: { path: "/", exact: true },
      about: { path: "/about", exact: false },
    };
    """
    dets = AgentRegistryTSAdapter().extract(code, "src/routes.ts", None)
    assert dets == []


def test_single_matching_field_not_enough():
    code = """
    export const ITEMS = {
      widget: { model: "acme-widget-42", price: 9.99 },
    };
    """
    dets = AgentRegistryTSAdapter().extract(code, "src/catalog.ts", None)
    assert dets == []


def test_test_path_gets_lower_confidence():
    normal = AgentRegistryTSAdapter().extract(_REGISTRY_TS, "src/agents/config.ts", None)
    test_path = AgentRegistryTSAdapter().extract(_REGISTRY_TS, "src/agents/config.test.ts", None)
    assert normal[0].confidence > test_path[0].confidence
