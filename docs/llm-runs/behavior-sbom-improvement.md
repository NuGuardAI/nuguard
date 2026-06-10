# Behavior Validation SBOM Coverage Improvement Plan

Date: 2026-06-09

## Executive Summary

Behavior validation is not yet using all expanded SBOM nodes and edges.

The current implementation uses the SBOM well for the original behavior contract: `AGENT` and `TOOL` coverage, `CALLS`-based tool grouping, selected `GUARDRAIL` and `DATASTORE` static checks, selected API endpoint discovery, and schema-aware endpoint scenarios. But the expanded SBOM graph is broader than the current behavior planner, runner, judge, and coverage report.

The main gap is that behavior validation still treats runtime coverage as an agent/tool exercise problem. Expanded node types such as `AUTH`, `IAM`, `DEPLOYMENT`, `CONTAINER_IMAGE`, `MODEL`, `FRAMEWORK`, `PRIVILEGE`, `PROMPT`, `DATASTORE`, and `API_ENDPOINT` are either excluded from dynamic coverage or used only indirectly. Expanded edge types such as `USES`, `ACCESSES`, `DEPLOYS`, and `DELEGATES_TO` are not consistently converted into validation objectives.

## Current State

### Static Behavior Alignment

Static alignment is implemented in `nuguard/behavior/alignment.py` as eight deterministic BA checks:

- `BA-001`: `AGENT.metadata.system_prompt_excerpt` against restricted topics.
- `BA-002`: risky `TOOL` metadata (`sql_injectable`, `ssrf_possible`) without `PROTECTS`.
- `BA-003`: restricted-action `TOOL` reachable from an `AGENT` through `CALLS`.
- `BA-004`: sensitive `DATASTORE` metadata without `PROTECTS`.
- `BA-005`: unauthenticated `AGENT` with `CALLS` access to high-privilege `TOOL`.
- `BA-006`: untrusted MCP-like `FRAMEWORK`/server with `CALLS` access to write-capable tool.
- `BA-007`: `AGENT.metadata.blocked_topics` gaps against policy restricted topics.
- `BA-008`: missing HITL guardrail/configuration for policy HITL triggers.

Static alignment uses these node types meaningfully: `AGENT`, `TOOL`, `GUARDRAIL`, `DATASTORE`, and MCP-flavored `FRAMEWORK`.

Static alignment uses these edge types meaningfully: `CALLS` and `PROTECTS`.

Static alignment does not materially use the full expanded graph:

- `AUTH` nodes are not traversed directly; auth is inferred from metadata such as `no_auth_required`.
- `IAM`, `DEPLOYMENT`, `CONTAINER_IMAGE`, `MODEL`, `PROMPT`, `PRIVILEGE`, and `API_ENDPOINT` nodes are not first-class static behavior checks.
- `USES`, `ACCESSES`, `DEPLOYS`, and `DELEGATES_TO` edges are not first-class static behavior checks.
- Rich SBOM fields such as `auth_detail`, `rate_limit_detail`, `encryption_detail`, `data_handling`, `instrumentation`, `testing`, `dependency_names`, `loc`, `source_url`, `integrity_hash`, `checksum`, and summary security/workflow findings do not drive BA findings.

### Dynamic Scenario Generation

Dynamic behavior scenario generation uses more SBOM context than static alignment, but still only partially covers the expanded graph.

What dynamic scenarios use today:

- `IntentProfile` extraction uses SBOM summary `use_case` plus `AGENT` and `TOOL` names.
- Happy-path scenario prompts include known `AGENT` and `TOOL` names.
- Agent coverage generates one scenario per `AGENT` node.
- Tool coverage groups `TOOL` nodes by `AGENT -> CALLS -> TOOL`, classifies tool action tiers, and emits chained scenarios.
- Standalone `TOOL` nodes without `CALLS` edges are still grouped and exercised.
- Endpoint coverage generates scenarios for interactive `API_ENDPOINT` nodes with request or response schemas.
- Data discovery generates scenarios for `AGENT` nodes that appear to handle user data, using sensitive `DATASTORE` metadata or user-data name/description heuristics.
- Runtime target discovery and client construction use endpoint metadata and chat payload hints outside scenario generation.

Important limitations:

- Runtime coverage is still initialized only from `_agent_names` and `_tool_names`; `BehaviorRunner._build_coverage_map()` reports only `AGENT` and `TOOL`.
- `NON_EXERCISABLE_NODE_TYPES` explicitly excludes `AUTH`, `IAM`, `DEPLOYMENT`, `CONTAINER_IMAGE`, `FRAMEWORK`, `MODEL`, `PRIVILEGE`, `DATASTORE`, `PROMPT`, and `API_ENDPOINT`.
- `GUARDRAIL` is not in `NON_EXERCISABLE_NODE_TYPES`, but it is not added to `_agent_names` or `_tool_names`, so it is not a dynamic coverage denominator either.
- Endpoint scenarios are generated, but `API_ENDPOINT` nodes are excluded from coverage, so endpoint exercise does not close a coverage item.
- `BehaviorJudge` receives only `expected_agents` and `expected_tools`, and its rubric is framed around whether the target agent/tool was invoked.
- Data-discovery comments describe `CALLS -> ACCESSES` reachability, but `_agent_has_user_data()` only checks direct outgoing edges from the agent to sensitive target nodes and does not verify the edge relationship type.
- Tool-chain grouping only consumes `CALLS`; it ignores `DELEGATES_TO` and does not use `USES` model/prompt/framework context to shape expected behavior.

## SBOM Coverage Matrix

| SBOM surface | Static alignment | Dynamic scenarios | Dynamic coverage/reporting | Gap |
|---|---:|---:|---:|---|
| `AGENT` nodes | Strong | Strong | Strong | Mostly covered |
| `TOOL` nodes | Strong for selected risks | Strong | Strong | Parameters and dependency context underused |
| `GUARDRAIL` nodes | Partial | Weak | Missing | Used for static HITL/protection checks, not actively validated as protected-path behavior |
| `DATASTORE` nodes | Partial | Partial | Missing | Sensitive metadata drives probes, but datastore access paths and access type are incomplete |
| `API_ENDPOINT` nodes | Missing | Partial | Missing | Endpoint scenarios exist but are not first-class coverage/finding surfaces |
| `PROMPT` nodes | Missing | Missing | Missing | Behavior uses agent prompt excerpts, not prompt nodes and `USES` prompt edges |
| `MODEL` nodes | Missing | Missing | Missing | Model/provider/supply-chain fields are not behavior validation inputs |
| `FRAMEWORK` nodes | Partial for MCP | Missing | Missing | MCP/static use only; framework routing/delegation not generally validated |
| `AUTH` nodes | Missing | Runtime helper only | Missing | Auth posture is handled by target client, not validated as SBOM behavior |
| `PRIVILEGE` nodes | Missing | Missing | Missing | High privilege is read as tool metadata, not graph privilege paths |
| `IAM` nodes | Missing | Missing | Missing | No behavior assertions for identity/role-to-deployment relationships |
| `DEPLOYMENT` nodes | Missing | Runtime URL fallback only | Missing | Not used for behavior risk, streaming, network, or availability checks |
| `CONTAINER_IMAGE` nodes | Missing | Missing | Missing | Not usually chat-exercisable, but should influence risk and report as not behavior-exercisable |
| `CALLS` edges | Strong | Strong | Indirect | Core behavior graph edge |
| `PROTECTS` edges | Strong | Weak | Missing | Static only; dynamic should validate protected paths |
| `ACCESSES` edges | Missing | Partial | Missing | Sensitive-data and write-path scenarios should use access type and full paths |
| `USES` edges | Missing | Missing | Missing | Should connect agents to prompts, models, auth, frameworks, dependencies |
| `DEPLOYS` edges | Missing | Missing | Missing | Should be static/metadata coverage, not chat coverage |
| `DELEGATES_TO` edges | Missing | Missing | Missing | Multi-agent handoff behavior is not first-class in behavior validation |

## Recommended Plan

### P0: Add a Behavior SBOM Coverage Contract

Create a small planner that converts SBOM nodes, edges, and policy clauses into explicit validation objectives before scenarios are generated.

Suggested object:

```python
class BehaviorCoverageObjective(BaseModel):
    objective_id: str
    surface_type: str  # node, edge, field, policy_clause
    node_id: str | None = None
    edge_id: str | None = None
    relationship_type: str | None = None
    behavior_mode: str  # static, dynamic, metadata_only, not_behavior_exercisable
    scenario_type: str | None = None
    status: str  # generated, executed, passed, failed, skipped, not_applicable
    reason: str = ""
```

Acceptance criteria:

- Every SBOM node appears in the behavior coverage contract.
- Every SBOM edge appears in the behavior coverage contract.
- Every objective is classified as dynamic, static, metadata-only, or not behavior-exercisable.
- Reports distinguish "not applicable to chat behavior" from "missed by behavior validation".

### P0: Extend Dynamic Coverage Beyond Agents and Tools

Replace the agent/tool-only coverage denominator with typed coverage objectives.

Keep `AGENT` and `TOOL` as directly exercisable. Add specialized coverage states for:

- `API_ENDPOINT`: exercised when an endpoint scenario runs successfully against the endpoint or when the runtime client resolves and sends through that endpoint.
- `GUARDRAIL`: exercised when a protected scenario reaches the guarded path and observes block, redaction, escalation, or allow behavior as expected.
- `DATASTORE`: exercised when a scenario covers a path that reaches the datastore through `ACCESSES`, with separate read/write/readwrite objectives.
- `PROMPT`: exercised indirectly when an agent with `USES -> PROMPT` is tested and prompt-specific expectations are asserted.
- `AUTH`: exercised when authenticated/unauthenticated role behavior is tested for nodes/endpoints protected by `AUTH -> PROTECTS`.
- `DELEGATES_TO`: exercised when a handoff scenario confirms the expected source and target agents participate.

Do not put infrastructure-only nodes into ordinary pass/fail chat coverage. Mark `IAM`, `DEPLOYMENT`, and `CONTAINER_IMAGE` as metadata/static coverage unless a concrete runtime behavior objective exists.

Acceptance criteria:

- `API_ENDPOINT` endpoint-coverage scenarios update endpoint coverage.
- `GUARDRAIL` and `DATASTORE` objectives appear in reports with clear status.
- Existing agent/tool coverage percentages remain available for backward compatibility.

### P0: Fix Data Access Path Traversal

Update data-discovery and data-classification planning to use actual graph paths:

- Direct `AGENT -> ACCESSES -> DATASTORE`.
- `AGENT -> CALLS -> TOOL -> ACCESSES -> DATASTORE`.
- `AGENT -> DELEGATES_TO -> AGENT -> ... -> DATASTORE`.

Use `Edge.access_type` to generate different read, write, and readwrite scenarios.

Acceptance criteria:

- A fixture with `AGENT -> CALLS -> TOOL -> ACCESSES(read) -> DATASTORE` produces a read-data behavior objective.
- A fixture with `ACCESSES(write)` produces a write/modification/HITL objective.
- Relationship type is checked explicitly; arbitrary outgoing edges do not imply data access.

### P1: Add Static BA Checks for Expanded Graph Edges

Add static alignment checks that consume the expanded SBOM graph directly:

- `BA-009`: `AUTH -> PROTECTS -> API_ENDPOINT/AGENT/TOOL` gaps for endpoints/tools with `auth_required` or sensitive response/data access.
- `BA-010`: `PRIVILEGE` reachable without `AUTH` or `GUARDRAIL` through `CALLS`, `USES`, or privilege metadata.
- `BA-011`: `DATASTORE` reached through `ACCESSES(write/readwrite)` without HITL, auth, or guardrail.
- `BA-012`: `AGENT -> USES -> MODEL` where external model/provider is reachable from sensitive datastore paths.
- `BA-013`: `AGENT -> USES -> PROMPT` prompt node includes restricted topics or missing data-handling instructions.
- `BA-014`: `DELEGATES_TO` handoff to a higher-privilege agent without policy or guardrail boundary.
- `BA-015`: `DEPLOYS` path has missing network policy, resource limits, health checks, or root container posture when those fields exist.
- `BA-016`: endpoint schema returns sensitive data but lacks auth, redaction, or guardrail protection.

Acceptance criteria:

- Each canonical relationship type has at least one static behavior check or an explicit non-applicability classification.
- Tests include every `RelationshipType`: `USES`, `CALLS`, `ACCESSES`, `PROTECTS`, `DEPLOYS`, `DELEGATES_TO`.

### P1: Make Endpoint Coverage First-Class

Endpoint coverage currently generates scenarios but does not close coverage. Promote it into its own coverage family.

Recommended changes:

- Add `BehaviorScenarioType.ENDPOINT_COVERAGE` instead of overloading `COMPONENT_COVERAGE`.
- Scope endpoint scenarios with `scoped_endpoints`.
- Add endpoint-specific judge signals: response key found, schema-compatible response, auth behavior expected, context fields honored, sensitive fields redacted.
- Use both `request_schema` and `response_schema`, not only `request_body_schema`/`response_schema`.
- Use `auth_scope`, `auth_detail`, `returns_sensitive_data`, `rate_limited`, and `rate_limit_detail` for assertions.

Acceptance criteria:

- An SBOM with one interactive `API_ENDPOINT` and no agents/tools still reports endpoint coverage.
- Endpoint findings name the endpoint node, not `unknown` or a generic component.

### P1: Validate Guardrail-Protected Paths Dynamically

Static `PROTECTS` checks tell us a guardrail exists, but behavior validation should confirm it behaves correctly.

Generate dynamic scenarios for each meaningful protected path:

- `GUARDRAIL -> PROTECTS -> AGENT`
- `GUARDRAIL -> PROTECTS -> TOOL`
- `GUARDRAIL -> PROTECTS -> API_ENDPOINT`
- `AUTH -> PROTECTS -> API_ENDPOINT`

Use guardrail metadata:

- `rules_excerpt`
- `blocked_topics`
- `blocked_actions`
- `refusal_style`
- HITL-related extras

Acceptance criteria:

- A guardrail with `blocked_actions=["transfer funds"]` generates an allowed precondition turn plus a protected action turn.
- Expected outcomes are scenario-specific: block, redaction, HITL, or scoped refusal.
- Reports show whether the guardrail was observed, not just declared.

### P1: Use Prompt, Model, Framework, and Delegation Context in Scenario Prompts

The current scenario prompts mostly include names and descriptions. Expanded SBOM context should shape scenario expectations:

- `PROMPT` nodes and `USES` edges: assert agent behavior against prompt constraints and detect prompt drift.
- `MODEL` nodes and `source_url`/`integrity_hash`/`checksum`: classify external model or local artifact risk and add metadata coverage.
- `FRAMEWORK` nodes: generate framework-specific routing/handoff/tool-call expectations when supported.
- `DELEGATES_TO` edges: generate handoff scenarios that verify the right downstream agent handles the right task.

Acceptance criteria:

- Agents with `USES -> PROMPT` get at least one prompt-grounded behavior objective.
- Agents with `DELEGATES_TO` get a handoff behavior scenario.
- Model/framework nodes are either linked to a dynamic scenario or explicitly marked metadata-only.

### P2: Use Enriched SBOM Fields for Prioritization

Use expanded SBOM fields to prioritize scenarios and explain risk:

- `injection_risk_score`: run higher-risk agents/tools first.
- `high_privilege`, `privilege_scope`, `no_auth_required`: prioritize privilege and auth scenarios.
- `pii_fields`, `phi_fields`, `pfi_fields`, `classified_fields`: prioritize sensitive data paths.
- `rate_limit_detail`: add safe rate-limit checks and tune scenario concurrency.
- `data_handling`: generate export/delete/retention/anonymization behavior checks.
- `instrumentation` and log/redaction fields: add leakage and logging behavior assertions where observable.
- `testing`: mark untested components as higher confidence debt.
- `loc` and `dependency_names`: report complexity/dependency-weighted behavior coverage.

Acceptance criteria:

- Scenario ordering is deterministic and risk-weighted.
- Reports show why a scenario was prioritized.
- Coverage debt includes skipped high-risk objectives when `max_scenarios` truncates the run.

### P2: Improve Reporting

Add a Behavior SBOM Coverage section with separate percentages:

- Direct dynamic coverage: agents, tools, endpoints, guardrail paths, datastore paths, delegation paths.
- Static alignment coverage: BA checks applied to graph surfaces.
- Metadata-only coverage: deployment, image, IAM, model artifact, testing/instrumentation evidence.
- Untested coverage debt: generated but skipped, not generated due to cap, unsupported node/edge type.

Acceptance criteria:

- A user can see which expanded SBOM surfaces were used, skipped, or deemed not behavior-exercisable.
- `coverage_percentage` is not silently inflated by omitting expanded nodes.
- Backward-compatible agent/tool coverage remains in the report.

## Suggested Implementation Sequence

1. Add graph indexing helpers in `nuguard/behavior/sbom_graph.py`.
2. Add `BehaviorCoverageObjective` and typed objective status models.
3. Build the initial objective planner with complete node/edge enumeration.
4. Wire endpoint objectives into scenario generation and coverage reporting.
5. Fix data access traversal to use `ACCESSES` and transitive paths.
6. Add guardrail-protected dynamic scenarios.
7. Add expanded static BA checks.
8. Update `BehaviorJudge` to accept typed expected components beyond agents/tools.
9. Update reports and docs.
10. Add fixture tests for every node type and relationship type.

## Regression Test Matrix

Add tests that assert the planner and reports account for every canonical SBOM type:

- Node coverage: `AGENT`, `GUARDRAIL`, `FRAMEWORK`, `MODEL`, `TOOL`, `DATASTORE`, `AUTH`, `PRIVILEGE`, `API_ENDPOINT`, `DEPLOYMENT`, `PROMPT`, `CONTAINER_IMAGE`, `IAM`.
- Edge coverage: `USES`, `CALLS`, `ACCESSES`, `PROTECTS`, `DEPLOYS`, `DELEGATES_TO`.
- Field coverage: `auth_detail`, `rate_limit_detail`, `encryption_detail`, `data_handling`, `instrumentation`, `testing`, `request_schema`, `response_schema`, `context_payload_fields`, `returns_sensitive_data`, `idor_surface`, `source_url`, `integrity_hash`, `checksum`.
- Scenario caps: skipped objectives are reported as skipped, not silently absent.

## Bottom Line

Behavior validation already uses a useful subset of the expanded SBOM, especially `AGENT`, `TOOL`, `CALLS`, selected `PROTECTS`, selected sensitive datastore fields, and endpoint schemas. It does not yet use all expanded SBOM nodes and edges.

The highest-value improvement is to make the SBOM graph an explicit behavior coverage contract, then let each node and edge resolve to dynamic coverage, static alignment, metadata-only coverage, or a documented non-applicability reason.
