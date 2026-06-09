# Redteam SBOM and Cognitive Policy Coverage Analysis

Date: 2026-06-08

## Executive Summary

NuGuard's redteam implementation already has a strong baseline: it loads the AI-SBOM, auto-enriches it before execution, discovers the target endpoint/auth shape, generates static and guided attack scenarios from the SBOM graph, merges a capability-aware scenario catalog, and evaluates chat responses against parts of the Cognitive Policy.

However, it does not yet use the full SBOM or enriched SBOM as a coverage contract. The current engine mostly consumes agents, tools, datastores, API endpoints, guardrails, selected graph edges, selected data-classification fields, and a few runtime endpoint/auth hints. Many high-value enriched fields are not used to generate, prioritize, or verify offensive tests: `auth_detail`, `auth_scope`, `rate_limit_detail`, `data_handling`, `encryption_detail`, `instrumentation`, `testing`, `loc`, `dependency_names`, `request_schema`, `response_schema`, deployment/IAM/security findings, workflow findings, streaming endpoints, and model supply-chain fields.

Cognitive Policy support is also partial. Redteam generates offensive tests for `restricted_topics`, `restricted_actions`, and generic `hitl_triggers`; it uses `data_classification` in selected data-exfiltration paths; and compiled controls can enrich generation. But it does not fully generate or evaluate tests for `allowed_topics`, `hitl_tool_conditions`, `rate_limits`, `raw_sections`, data-handling duties, or every compiled control. There is also a concrete runtime bug: tool-scoped HITL conditions are parsed but skipped when the policy has no generic HITL trigger.

The biggest fixes are:

1. Add a coverage planner that turns SBOM + enriched SBOM + Cognitive Policy into a test matrix and records generated/executed/skipped coverage for every relevant node, field, and policy clause.
2. Pass the effective compiled policy everywhere, not just to scenario generation.
3. Bind catalog scenarios to concrete SBOM tools, endpoints, datastores, APIs, and policies rather than mostly entry agents.
4. Add scenario families for enriched SBOM fields and missing Cognitive Policy dimensions.
5. Fix the tool-scoped HITL detector and add regression tests.

## Current Redteam Data Flow

The `nuguard redteam` command loads the SBOM and resolves the target URL from either user input or SBOM-discovered deployment data (`nuguard/cli/commands/redteam.py:191-203`). Before orchestration, it calls `enrich_sbom_for_run(...)`, which means redteam is designed to operate on the enriched SBOM, not only the static extraction output (`nuguard/cli/commands/redteam.py:441-455`).

The command then parses Cognitive Policy markdown and, if present, loads compiled policy controls from the sibling compiled JSON (`nuguard/cli/commands/redteam.py:457-470`). It passes the enriched SBOM, parsed policy, compiled controls, canary config, auth config, chat endpoint/payload settings, LLM config, scenario filters, catalog config, and discovery settings into the orchestrator (`nuguard/cli/commands/redteam.py:520-615`).

The orchestrator performs endpoint auto-discovery, target URL fallback, auth upgrade, auth bootstrap, login-response context injection, SBOM context payload hint injection, and optional pre-scan discovery before generating scenarios (`nuguard/redteam/executor/orchestrator.py:650-813`). Scenario generation uses `ScenarioGenerator(self._sbom, effective_policy)` after enriching the parsed policy with compiled controls where available (`nuguard/redteam/executor/orchestrator.py:814-829`).

Catalog scenarios are merged after baseline scenario generation (`nuguard/redteam/executor/orchestrator.py:831-859`). LLM payload enrichment runs only on scenarios selected for execution, using a prompt cache keyed on SBOM and policy (`nuguard/redteam/executor/orchestrator.py:883-920`).

## What Redteam Uses Well Today

### SBOM graph and node types

`ScenarioGenerator` indexes SBOM nodes and outgoing edges at construction time (`nuguard/redteam/scenarios/generator.py:82-99`). The main generation path covers these families:

- Prompt-driven threats.
- Policy violations.
- Guardrail bypass from `GUARDRAIL` nodes and `PROTECTS` edges.
- Data exfiltration from `DATASTORE` nodes and reachable agents.
- Privilege escalation and tool abuse.
- SBOM-driven tool-specific attacks.
- MCP toxic flow and MCP attacks.
- RAG/vector store poisoning.
- Direct API attacks.
- Guided adversarial conversations.
- Advanced jailbreak, evasion, agentic trust abuse, and oracle attacks.

This is visible in the generation sequence (`nuguard/redteam/scenarios/generator.py:103-208`).

### Datastore and sensitive-field metadata

Data exfiltration scenarios use `DATASTORE` nodes, `pii_fields`, `phi_fields`, `pfi_fields`, `classified_fields`, `datastore_type`, reachable agents, SQL-vs-NoSQL heuristics, and data-access graph paths (`nuguard/redteam/scenarios/generator.py:491-853`). The generator also uses policy `data_classification` to derive sensitive fields when datastore metadata is sparse (`nuguard/redteam/scenarios/generator.py:607-609`, `nuguard/redteam/scenarios/generator.py:788-853`).

### Tool metadata

Tool-oriented tests use `description`, `mcp_server_url`, `no_auth_required`, `high_privilege`, `privilege_scope`, `sql_injectable`, and `ssrf_possible`. The SBOM-driven tool-specific builder classifies tool names/descriptions into file, SQL, SSRF, email, path, command, and generic categories (`nuguard/redteam/scenarios/sbom_driven.py`).

### API endpoint metadata

Direct API attacks are generated from `API_ENDPOINT` nodes. They use `endpoint`, `method`, `auth_required`, `path_params`, `idor_surface`, and `request_body_schema` to build auth-bypass, mass-assignment, and IDOR probes (`nuguard/redteam/scenarios/generator.py:1232-1315`). The API builders generate plausible request bodies from `request_body_schema`, which helps reach authz checks instead of failing early with validation errors (`nuguard/redteam/scenarios/api_attacks.py`).

### Chat endpoint and session metadata

The common session resolver uses `context_payload_fields` to inject identity/session fields into chat payload extras. Identity fields are filled from login responses when possible; session fields get a generated UUID (`nuguard/common/session_resolver.py`). This is a strong use of enriched API metadata because it improves realism and reduces false negatives.

### Guardrail metadata

Guardrail bypass scenarios consume `GUARDRAIL` nodes, `blocked_topics`, `blocked_actions`, and `PROTECTS` edges (`nuguard/redteam/scenarios/generator.py:424-467`). Guided conversations also use guardrail-style metadata such as `blocked_topics`, `blocked_actions`, and `refusal_style` (`nuguard/redteam/scenarios/generator.py:1411-1440`, `nuguard/redteam/scenarios/generator.py:1525-1570`).

### Cognitive Policy basics

The parsed `CognitivePolicy` model supports `allowed_topics`, `restricted_topics`, `restricted_actions`, `hitl_triggers`, `hitl_tool_conditions`, `data_classification`, `rate_limits`, and `raw_sections` (`nuguard/models/policy.py:182-201`). The parser preserves unknown sections in `raw_sections` and parses `tool_name: condition` bullets into `hitl_tool_conditions` (`nuguard/policy/parser.py:75-105`, `nuguard/policy/parser.py:132-155`).

Rule-based policy compilation generates controls for allowed topics, restricted topics, restricted actions, HITL triggers, data classification, and rate limits (`nuguard/policy/compiler.py:219-286`). Redteam uses compiled controls to enrich generation with boundary prompts (`nuguard/redteam/executor/orchestrator.py:814-829`).

Runtime policy evaluation checks topic boundaries, restricted actions, and HITL bypass for chat/agent turns (`nuguard/redteam/policy_engine/evaluator.py:49-53`, `nuguard/redteam/executor/executor.py:942-967`).

## Key Coverage Gaps and Risks

### P0: Tool-scoped HITL conditions are parsed but can be skipped at runtime

The HITL detector returns immediately if `policy.hitl_triggers` is empty (`nuguard/redteam/policy_engine/detectors/hitl_bypass.py:65-66`). The check for `hitl_tool_conditions` happens later (`nuguard/redteam/policy_engine/detectors/hitl_bypass.py:109-160`), so policies that only specify tool-scoped approval rules are never evaluated.

Risk: a policy such as `payment_tool: amount exceeds $500` can be parsed successfully but redteam may not flag execution without approval unless there is also a generic HITL trigger.

Fix:

- Change the early return to check both structures: return only when neither `hitl_triggers` nor `hitl_tool_conditions` exists.
- Generate explicit offensive scenarios for every `hitl_tool_conditions` entry, not only generic `hitl_triggers`.
- Add tests covering tool-scoped-only policies, mixed generic/tool policies, and approval-signal suppression.

### P0: Effective compiled policy is not propagated through all redteam phases

The orchestrator constructs an `effective_policy` enriched from compiled controls and passes it to `ScenarioGenerator` (`nuguard/redteam/executor/orchestrator.py:814-829`). But LLM payload enrichment still uses `self._policy` for cache keying and prompt generation (`nuguard/redteam/executor/orchestrator.py:896-901`), and `AttackExecutor` is created with `policy=self._policy` (`nuguard/redteam/executor/orchestrator.py:1046-1049`).

Risk: compiled boundary prompts can improve scenario selection, but runtime evaluation and LLM payload variants may miss the same richer policy language. This creates inconsistent coverage and can suppress findings that depend on compiled controls.

Fix:

- Store `effective_policy` on the orchestrator for the run.
- Use it for prompt cache keys, `LLMPromptGenerator`, `AttackExecutor`, guided attack context, and reports.
- Include a policy-control coverage report mapping each compiled control to generated, enriched, executed, and evaluated scenarios.

### P0: Cognitive Policy dimensions are only partially tested

Static policy-violation scenarios only iterate `restricted_topics`, `restricted_actions`, and `hitl_triggers` (`nuguard/redteam/scenarios/generator.py:373-422`). They do not generate first-class offensive scenarios for:

- `allowed_topics`: out-of-scope boundary testing, topic pivoting, mixed allowed/disallowed requests, and allowed-topic pretext abuse.
- `hitl_tool_conditions`: tool-specific approval bypass.
- `rate_limits`: burst, retry, streaming, session rotation, and tool-call amplification tests.
- `raw_sections`: user-defined safety rules such as retention, logging, jurisdiction, escalation, or data-sharing.
- `data_classification`: complete DLP/runtime validation across all response surfaces.

Runtime evaluation also checks only topic boundaries, restricted actions, and HITL bypass (`nuguard/redteam/policy_engine/evaluator.py:49-53`). It does not evaluate data classification leaks, rate limit violations, raw-section constraints, retention/deletion obligations, or policy-defined authz requirements.

Fix:

- Introduce a `PolicyScenarioPlanner` that emits one or more offensive tests for every parsed and compiled policy clause.
- Treat `allowed_topics` as an explicit boundary: generate off-domain requests, mixed-intent requests, indirect-injection pivots, and "valid topic as pretext" attacks.
- Treat `data_classification` as a runtime DLP detector as well as a scenario input.
- Implement rate-limit probes with safe caps and per-profile throttling.
- Convert `raw_sections` into LLM-assisted test objectives when a redteam LLM is configured, and otherwise report them as untested.

### P1: Enriched SBOM 1.5.0 fields are underused

The SBOM model includes rich enrichment fields: `rate_limit_detail`, `auth_detail`, `encryption_detail`, `data_handling`, `dependency_names`, `instrumentation`, `testing`, `loc`, `request_schema`, `response_schema`, `has_network_policy`, `source_url`, `integrity_hash`, `checksum`, and `extras` (`nuguard/sbom/models.py:561-625`). The summary includes deployment/security/IAM findings, log paths, streaming endpoints, total LOC, app-wide instrumentation/testing, GitHub Actions content, and workflow security findings (`nuguard/sbom/models.py:657-856`).

Most of these fields do not currently drive redteam scenario generation, prioritization, or assertions.

Risk: redteam may miss high-value attacks against the real application posture, especially auth downgrade, rate limit bypass, sensitive-data retention/export/delete, observability leakage, CI/CD abuse, cloud/IAM lateral movement, missing network policy, model supply-chain integrity, streaming leakage, and dependency-specific exploit paths.

Fix:

- Add enriched-SBOM scenario families:
  - `auth_detail` and `auth_scope`: role downgrade, missing scope, token/header manipulation, cross-role direct endpoint tests.
  - `rate_limit_detail`: safe burst, session rotation, retry-after bypass, streaming amplification.
  - `data_handling`: deletion/export/retention/backup/anonymization abuse.
  - `encryption_detail`: storage/log/backup leakage checks where applicable.
  - `instrumentation` and `log_paths`: prompt/log injection, sensitive data in logs, trace export leakage.
  - `testing`: lower confidence or higher priority for untested components; generate regression-focused tests for untested safety paths.
  - `loc`: prioritize high-LOC/high-risk components and report LOC-weighted coverage.
  - `dependency_names`: map risky AI/security libraries to targeted probes.
  - `request_schema` and `response_schema`: richer request body generation, mass-assignment by schema diff, sensitive response assertions.
  - `has_network_policy`, IAM, and `security_findings`: deployment and lateral-movement scenario families.
  - `source_url`, `integrity_hash`, `checksum`: model/artifact supply-chain tests.
  - `uses_streaming` and `streaming_endpoints`: streaming-specific leakage and refusal-boundary tests.

### P1: Catalog selection loses concrete SBOM context

The catalog capability selector builds contexts only per entry agent and sets `target_tool=None` (`nuguard/redteam/catalog/selector.py:85-98`). It then invokes each builder using that agent-only context (`nuguard/redteam/catalog/selector.py:140-151`).

Several catalog builders need concrete tools/endpoints to be precise. For example, catalog mass assignment uses a generic `/api/users` path instead of an actual API endpoint (`nuguard/redteam/catalog/builders.py:139-145`). MCP toxic flow and other tool-specific builders depend on `target_tool`, but selector never binds one (`nuguard/redteam/catalog/builders.py:757-760`).

Risk: catalog scenarios are selected by application capability but often execute as generic prompts, hit the wrong endpoint, or fail to generate the most relevant tool-specific attack.

Fix:

- Extend `BuilderContext` with `target_endpoint`, `target_datastore`, `target_guardrail`, `target_policy_clause`, and `edge_path`.
- Build contexts per relevant SBOM surface, not only per entry agent.
- For each catalog spec, bind to the specific tool/API/datastore that satisfied its capability.
- Replace hardcoded catalog paths with real `API_ENDPOINT` metadata and schemas.

### P1: Capability detection under-gates and over-gates catalog scenarios

The taxonomy defines capabilities such as `DOCUMENT` and `MULTI_SESSION` (`nuguard/redteam/catalog/taxonomy.py:103-146`), but the detector never appears to add those capabilities in the primary build path (`nuguard/redteam/catalog/capability.py:111-207`). Conversely, `RENDERS_MARKDOWN` is added unconditionally for every chat app (`nuguard/redteam/catalog/capability.py:191-192`).

Risk:

- Multi-session and document scenarios may never be selected even when the SBOM contains evidence.
- Markdown rendering/covert exfiltration scenarios may run against targets that do not render markdown, creating noise.

Fix:

- Detect `DOCUMENT` from document/file/OCR/attachment tool metadata, accepted upload endpoints, and delivery channels.
- Detect `MULTI_SESSION` from session memory, user history, persistent profile, cookie/session fields, or summary memory.
- Detect `RENDERS_MARKDOWN` from frontend/framework evidence, response renderer metadata, or explicit config; otherwise mark uncertain.
- Report capability evidence and missing evidence in catalog coverage.

### P1: Direct API tests use only a narrow endpoint model

The direct API generator uses `endpoint`, `method`, `auth_required`, `request_body_schema`, `path_params`, and `idor_surface` (`nuguard/redteam/scenarios/generator.py:1232-1315`). It does not use `auth_scope`, `returns_sensitive_data`, `rate_limited`, `rate_limit_detail`, `auth_detail`, `request_schema`, `response_schema`, `context_payload_fields`, or `response_text_key`.

Risk: endpoint-specific security bugs are missed or poorly asserted, especially broken function-level authorization, scope confusion, sensitive response leakage, schema over-posting, and rate-limit bypass.

Fix:

- Add BFLA/RBAC endpoint scenarios from `auth_scope` and `auth_detail`.
- Use `response_schema` and `returns_sensitive_data` to assert leakage of classified fields.
- Use `request_schema` to synthesize valid nested payloads and mutation payloads.
- Reuse `context_payload_fields` for direct endpoint calls, not only chat endpoint calls.
- Add rate-limit endpoint tests with strict safe-execution caps.

### P1: Runtime policy detectors are mostly keyword-based and response-only

Topic boundary detection looks for restricted-topic substrings in the response and checks low-confidence overlap with allowed topics (`nuguard/redteam/policy_engine/detectors/topic_boundary.py:66-140`). Restricted-action and HITL detection are similarly deterministic and lightweight. This is useful for cheap CI, but insufficient for semantic policy violations.

Risk: redteam can miss violations where the model complies without repeating the restricted keywords, paraphrases the action, executes a tool with a differently named function, or violates a raw-section/data-handling policy.

Fix:

- Keep deterministic detectors as fast signals.
- Add optional LLM policy evaluation using the compiled policy controls, original prompt, response, tool calls, HTTP metadata, and scenario objective.
- Evaluate both prompt intent and response/tool behavior, not only response text.
- Attach confidence and evidence for each policy clause.

### P1: CI escalation pass skips LLM payload enrichment

LLM payload enrichment is applied to the initially selected `scenarios` list (`nuguard/redteam/executor/orchestrator.py:883-920`). If CI produces no findings, the escalation pass takes lower-scored scenarios directly from `all_scenarios` and runs them (`nuguard/redteam/executor/orchestrator.py:1106-1124`).

Risk: escalation scenarios are less realistic than the initial scenario set when a redteam LLM is configured, reducing the chance of surfacing subtle issues.

Fix:

- Run the same LLM enrichment pipeline on escalation scenarios before execution.
- Track enrichment counts separately for initial and escalation passes.

### P2: Framework summary lookup uses the wrong field name

`ScanSummary` defines `frameworks` (`nuguard/sbom/models.py:664-667`), but the orchestrator uses `frameworks_detected` when building the LLM summary (`nuguard/redteam/executor/orchestrator.py:1147-1149`). A similar lookup exists in target-context construction.

Risk: guided summaries and generated context may omit detected frameworks.

Fix:

- Replace `frameworks_detected` with `frameworks`, while optionally retaining fallback compatibility for older SBOMs.

### P2: Scenario caps and deduplication can hide coverage debt

The generator caps agents per attack goal to two (`nuguard/redteam/scenarios/generator.py:74-76`) and deduplicates sub-agent scenarios when entry agents exist (`nuguard/redteam/scenarios/generator.py:175-180`). This is sensible for runtime, but it should be visible as unexecuted coverage debt.

Risk: multi-agent systems may have agent-specific tools, prompts, or policies that are never directly exercised, especially under CI/standard profiles.

Fix:

- Add a coverage matrix that records "not generated due to cap" and "deduped behind entry agent" per agent/tool/datastore/policy clause.
- Let `full` profile override more caps or sample all unique high-risk surfaces.
- Prioritize by `injection_risk_score`, tool privilege, data sensitivity, and policy severity.

### P2: `injection_risk_score` and LOC are modeled but not used for scenario priority

`injection_risk_score` is documented as being used by `ScenarioGenerator` to sort scenarios by expected impact (`nuguard/sbom/models.py:391-400`), and `loc`/`total_loc` are included in the enriched SBOM (`nuguard/sbom/models.py:594-597`, `nuguard/sbom/models.py:813-817`). Current scenario sorting uses `impact_score` only (`nuguard/redteam/scenarios/generator.py:182-183`).

Risk: high-complexity or high-risk components do not receive higher priority, and reports cannot show LOC-weighted redteam coverage.

Fix:

- Blend base impact with SBOM risk factors: `injection_risk_score`, sensitive data reachability, high privilege, no auth, unguarded HITL, low tests, high LOC, and security findings.
- Add LOC coverage reporting: total scanned LOC, component LOC exercised by redteam, high-risk LOC tested, and high-risk LOC skipped.

### P2: Unified `scan` workflow does not run policy or redteam

The `scan` command still skips policy checks and redteam execution even when enabled (`nuguard/cli/commands/scan.py:214-233`).

Risk: users running the apparent end-to-end pipeline can believe safety/security redteam coverage has been performed when it has not.

Fix:

- Wire `scan --steps redteam` into the same orchestrator used by `nuguard redteam`.
- Emit a hard warning or non-zero exit when requested policy/redteam steps are skipped.
- Include the redteam coverage matrix in the scan output bundle.

## Recommended Target Architecture

### 1. Redteam coverage planner

Add a planner layer before scenario generation:

```text
SBOM + enriched SBOM + Cognitive Policy + compiled controls
  -> RedteamSurfaceIndex
  -> CoverageObligations
  -> Scenario generation contexts
  -> Coverage report: generated / enriched / executed / skipped / findings
```

The planner should create obligations for:

- Every `AGENT`, `TOOL`, `DATASTORE`, `API_ENDPOINT`, `GUARDRAIL`, `MODEL`, deployment/IAM/security node, and relevant graph path.
- Every sensitive data label and classified field.
- Every auth, rate-limit, data-handling, encryption, logging, CI/CD, workflow, model supply-chain, streaming, dependency, and LOC risk signal.
- Every Cognitive Policy clause and every compiled `PolicyControl`.

This makes redteam coverage auditable and makes gaps explicit.

### 2. Concrete context binding

Scenario builders should receive concrete context objects:

```python
@dataclass
class RedteamScenarioContext:
    entry_agent: Node | None
    target_agent: Node | None
    target_tool: Node | None
    target_endpoint: Node | None
    target_datastore: Node | None
    target_guardrail: Node | None
    target_policy_control: PolicyControl | None
    graph_path: list[str]
    evidence_fields: dict[str, Any]
```

The catalog selector should select contexts based on the surfaces that satisfy the spec, not just entry agents.

### 3. Policy-driven offensive tests

Generate tests for all Cognitive Policy aspects:

- Allowed topics: off-domain pivot, mixed intent, allowed-topic pretext, refusal consistency.
- Restricted topics: direct, indirect, encoded, roleplay, multi-turn, tool-mediated.
- Restricted actions: tool coercion, BFLA/RBAC, false authority, delegated-agent coercion.
- Generic HITL triggers: skip approval, fabricate approval, social-engineer approval, race/sequence tests.
- Tool-scoped HITL conditions: tool-specific execution without approval.
- Data classification: direct disclosure, aggregation, inference, export, logging, streaming, tool output, API response leakage.
- Rate limits: safe bounded burst, session rotation, streaming amplification, retry-after bypass.
- Raw sections: LLM-assisted conversion to controls, or explicit "untested raw policy" report entries.

### 4. Enriched SBOM scenario families

Add first-class scenario builders for:

- Auth and authorization posture.
- Rate limiting.
- Data handling/retention/export/delete.
- Logging/telemetry/observability leakage.
- CI/CD and workflow compromise.
- Deployment and IAM posture.
- Model supply-chain integrity.
- Dependency-informed attacks.
- Streaming output.
- LOC-weighted high-risk components.

### 5. Coverage and reporting

Reports should include:

- SBOM node coverage: generated, executed, skipped reason, finding count.
- Policy coverage: each clause/control and generated/executed/finding status.
- Enriched field coverage: each supported enrichment field and how it was used.
- LOC coverage: total scanned LOC, LOC under tested components, high-risk LOC skipped.
- Profile limitations: caps, deduplication, skipped catalog specs, missing capabilities.

## Proposed Implementation Plan

### P0 fixes

1. Fix `detect_hitl_bypass_violations` so tool-scoped HITL conditions are evaluated even when `hitl_triggers` is empty.
2. Generate tool-scoped HITL scenarios from `policy.hitl_tool_conditions`.
3. Propagate `effective_policy` into LLM payload enrichment, prompt cache keys, `AttackExecutor`, guided executors, and reports.
4. Add policy-control coverage reporting for parsed clauses and compiled controls.

### P1 fixes

1. Add `RedteamSurfaceIndex` and concrete scenario contexts.
2. Bind catalog builders to concrete tools, endpoints, datastores, and policy controls.
3. Expand capability detection for `DOCUMENT`, `MULTI_SESSION`, `RENDERS_MARKDOWN`, auth/rate/deployment/IAM/streaming signals.
4. Add endpoint scenarios for `auth_scope`, `auth_detail`, `returns_sensitive_data`, `request_schema`, `response_schema`, `rate_limit_detail`.
5. Add runtime DLP and rate-limit evaluation.
6. Enrich CI escalation scenarios with LLM payloads before execution.

### P2 fixes

1. Use `summary.frameworks` instead of `frameworks_detected` with compatibility fallback.
2. Add LOC/risk-aware prioritization using `injection_risk_score`, `loc`, `testing`, and security findings.
3. Wire `scan` policy/redteam steps into the orchestrator.
4. Expose skipped coverage debt in CI/standard profiles.

## Suggested Regression Tests

- Tool-scoped HITL only: `payment_tool: transaction amount exceeds $500` produces both scenarios and runtime violations when a matching tool call occurs without approval.
- Effective policy propagation: compiled boundary prompts appear in generated scenarios, LLM-enriched payloads, executor evaluation, and reports.
- Catalog target binding: MCP toxic flow receives a concrete MCP tool; mass assignment uses a real API endpoint, not `/api/users`.
- Capability detection: document and multi-session capabilities are added from representative SBOMs; markdown capability is not unconditional.
- API schema attacks: nested `request_schema` and `response_schema` are used to build valid request bodies and sensitive response assertions.
- Data classification runtime DLP: classified fields in response text, tool output, streaming chunks, logs, and API responses are detected.
- Rate limits: safe bounded rate-limit scenarios are generated from policy `rate_limits` and SBOM `rate_limit_detail`.
- Coverage report snapshots: every SBOM node, enriched field family, and policy clause has generated/executed/skipped accounting.

## Bottom Line

NuGuard redteam is already SBOM-aware, but it is not yet SBOM-complete. The current implementation uses the core graph and selected metadata well, but maximum AI safety/security coverage requires treating the enriched SBOM and Cognitive Policy as coverage obligations. The fastest risk reduction is to fix HITL tool-condition evaluation, propagate compiled policy consistently, and add a planner/reporting layer that makes every untested SBOM and policy dimension visible.
