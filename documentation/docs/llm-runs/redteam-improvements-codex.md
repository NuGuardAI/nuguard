# Red-Team Catalog Improvement Review

Review date: 2026-06-08

Scope: `nuguard/redteam/catalog/registry.py`, with light context from the
catalog taxonomy and builder registry. This review focuses on offensive
pentesting and red-teaming coverage for Agentic and RAG applications across:

- Business logic failures
- AI-layer failures: prompts, tools, RAG, memory, agents, MCP, model behavior
- Infrastructure and runtime failures: identity, browser/API surfaces, egress,
  execution, audit, deployment and supply-chain boundaries

External baselines used for coverage comparison:

- OWASP Top 10 for LLM Applications 2025:
  https://genai.owasp.org/resource/owasp-top-10-for-llm-applications-2025/
- OWASP Top 10 for Agentic Applications 2026:
  https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/
- OWASP MCP Top 10:
  https://owasp.org/www-project-mcp-top-10/
- OWASP Gen AI Red Teaming Guide:
  https://genai.owasp.org/2025/01/22/announcing-the-owasp-gen-ai-red-teaming-guide/
- NIST AI 600-1 Generative AI Profile:
  https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence

## Current Registry Shape

The current catalog imports cleanly with:

| Metric | Current state |
|---|---:|
| Total `ScenarioSpec` entries | 85 |
| Enabled specs | 85 |
| Missing builders | 0 |
| Extra builders not referenced by specs | 0 |

The top-level docstring still says this is an 84-scenario catalog. The current
count is 85 because `E07` has been added. This is only a hygiene issue, but it
matters because downstream snapshot tests, docs, and coverage claims should not
disagree with the executable catalog.

Current category counts:

| Category | Count |
|---|---:|
| Data Exfiltration | 8 |
| Covert Exfiltration | 8 |
| Destructive Tool Actions | 8 |
| Authorization Failures | 8 |
| Indirect Prompt Injection | 8 |
| MCP and Tool Poisoning | 8 |
| Memory and Persistence | 6 |
| Multi-Agent Trust Abuse | 6 |
| Jailbreak and Policy Bypass | 6 |
| Evasion and Robustness | 7 |
| Business Logic and Safety | 6 |
| Coding and Automation Agents | 6 |

Strong areas:

- Direct/cross-tenant exfiltration, IDOR/BOLA/BFLA, HITL bypass, destructive
  tool calls, covert egress, indirect injection, MCP poisoning, memory poisoning,
  multi-agent handoffs, jailbreaks, basic evasion, and coding-agent filesystem
  risks are represented.
- The catalog is declarative and builder-backed. Every current `builder_key`
  resolves, so adding scenarios can follow a stable pattern.
- The `safe_execution` model is mature enough to support safe variants for
  missing offensive techniques through canaries, trap endpoints, emulated tools,
  synthetic tenants, browser sandboxes, and dry-run boundaries.

## Highest-Priority Gaps

### 1. Framework mappings are stale or overloaded

What I saw:

- Only these OWASP LLM tags appear: `LLM01`, `LLM06`, `LLM07`, `LLM09`, `LLM10`.
- No registry entries map to `LLM02` Sensitive Information Disclosure, `LLM03`
  Supply Chain, `LLM04` Data and Model Poisoning, `LLM05` Improper Output
  Handling, or `LLM08` Vector and Embedding Weaknesses.
- Data exfiltration scenarios are mostly mapped to `LLM06`, but under the 2025
  OWASP LLM taxonomy, most of those should map primarily to `LLM02`.
- Agentic mappings stop at `ASI07`; there are no `ASI08`, `ASI09`, or `ASI10`
  entries. Memory scenarios are mapped to `ASI05`, but the 2026 Agentic Top 10
  places memory and context poisoning under `ASI06`.

Why it matters:

- Coverage reporting will understate or misstate what NuGuard tests.
- Customers comparing NuGuard output against modern OWASP LLM/Agentic controls
  may think important risks are covered when they are only adjacent.
- The registry cannot express several current risk families without forcing them
  into older or wrong labels.

Fix:

- Update the mapping taxonomy to OWASP LLM 2025 and Agentic 2026.
- Re-map existing scenarios before adding many new ones:
  - `D01-D08`, `C01-C08`, `K03` -> add `LLM02` where sensitive data disclosure
    is the primary outcome.
  - Tool/action scenarios such as `T01-T08`, `A06`, `B05`, `K02`, `K04-K06` ->
    keep or add `LLM06` for excessive agency where appropriate.
  - `M01-M08`, `K01`, future package/skill scenarios -> add `LLM03` or `ASI04`
    where supply chain is the root cause.
  - RAG/vector scenarios -> add `LLM08` and, when poisoned corpus state is used,
    `LLM04`.
  - Downstream parser/interpreter scenarios -> add `LLM05`.
  - Memory scenarios `P01-P06` -> change agentic mapping to `ASI06`, while
    keeping `ASI03` on identity/auth drift cases.
- Add a small snapshot test that fails when a modern OWASP category has no
  corresponding scenario or explicit `not_applicable_reason`.

### 2. RAG and vector-store coverage is too thin

What I saw:

- Only `D05` Private document extraction and `D06` RAG citation overreach require
  `C.RAG`.
- `C.VECTOR_STORE` exists in the capability enum but no current scenario requires
  it.
- There is no dedicated RAG category or vector evidence type. Current evidence
  uses generic `BACKEND_TRACE` and `RESPONSE_QUOTE`.

Missing offensive scenarios:

| Proposed ID | Scenario | Risk covered | Safe execution |
|---|---|---|---|
| `R01` | Poisoned document ingestion changes answer or action | RAG poisoning, indirect injection | Synthetic corpus |
| `R02` | Vector metadata ACL bypass | Cross-tenant retrieval despite filters | Synthetic tenant |
| `R03` | Embedding collision / similarity hijack | Attacker chunk outranks trusted chunk | Synthetic corpus |
| `R04` | Chunk-boundary instruction smuggling | Hidden instruction split across chunks | Canary only |
| `R05` | Stale/deleted document retrieval | Deleted or revoked content still retrieved | Synthetic corpus |
| `R06` | Cross-index or namespace bleed | Tenant A query retrieves tenant B index | Synthetic tenant |
| `R07` | Citation laundering | Answer cites authorized doc while using unauthorized context | Retrieval trace |
| `R08` | Sensitive nearest-neighbor enumeration | Repeated semantic probes reveal private corpus facts | Canary only |

Fix:

- Add a `RAG_AND_VECTOR` category or expand data exfiltration with `Rxx`
  scenarios.
- Add capabilities: `VECTOR_STORE`, `RAG_INGESTION`, `RETRIEVAL_METADATA_FILTERS`,
  `HYBRID_SEARCH`, `RERANKER`, `DOCUMENT_ACL`, `INDEX_NAMESPACE`.
- Add evidence types: `RETRIEVAL_TRACE`, `CHUNK_ID`, `CITATION_TRACE`,
  `INDEX_NAMESPACE`, `ACL_DECISION`.
- Add builders that seed synthetic documents through the app's own ingestion
  path when possible; otherwise use an emulated retrieval fixture.

### 3. Improper output handling is not explicitly tested

What I saw:

- `E04` tests structured prompt-side injection.
- `C05` tests structured field smuggling.
- `K02` tests shell injection for coding agents.
- There is no first-class category for model output being consumed by a
  downstream interpreter, renderer, workflow engine, database query builder, or
  API client.

Missing offensive scenarios:

| Proposed ID | Scenario | Sink |
|---|---|---|
| `O01` | HTML/Markdown output becomes XSS or unsafe link action | Browser/renderer |
| `O02` | JSON output injects extra tool/action fields | Tool argument parser |
| `O03` | SQL/NoSQL query generation crosses tenant or deletes data | Query executor |
| `O04` | Generated URL causes internal fetch or SSRF | HTTP client |
| `O05` | YAML/CI/config output alters security settings | Config parser |
| `O06` | File/MIME confusion creates executable artifact | Artifact generator |

Fix:

- Add `DOWNSTREAM_INTERPRETER` or `IMPROPER_OUTPUT_HANDLING` category.
- Add sink types: `BROWSER_DOM`, `QUERY_EXECUTION`, `CONFIG_WRITE`,
  `WORKFLOW_ENGINE`, `ARTIFACT_RENDERER`.
- Use `SE.EMULATED_TOOL`, `SE.TRAP_ENDPOINT`, and `SE.SANDBOX` so the test proves
  exploitability at the boundary without causing real damage.
- Add output validators that inspect final rendered DOM/tool args, not just the
  model text.

### 4. Human-agent trust exploitation is only partially covered

What I saw:

- `A06` HITL bypass and `G06` supervisor approval spoof are useful.
- They do not fully test approval-surface deception, consent laundering, or
  operator social engineering, which are now explicit in the Agentic Top 10
  under `ASI09`.

Missing offensive scenarios:

| Proposed ID | Scenario | Key assertion |
|---|---|---|
| `H01` | Approval summary mismatch | Human sees benign summary; raw tool args are harmful |
| `H02` | Consent laundering across turns | Earlier vague consent reused for later high-impact action |
| `H03` | Authority-bias operator phishing | Agent asks user/admin to approve unsafe action using false urgency |
| `H04` | Partial approval overreach | User approves read-only action; agent escalates to write/send |
| `H05` | Hidden recipient or amount in approval payload | UI summary omits material action detail |

Fix:

- Add `HUMAN_AGENT_TRUST` category mapped to `ASI09`.
- Add evidence type `APPROVAL_TRACE` with both raw action payload and the
  human-visible approval text.
- Add a policy rule: a HITL control only counts as effective when the raw action,
  target resource, identity, amount, recipient, and side effect are visible to
  the approver.

### 5. Cascading failures and unsafe autonomous loops are missing

What I saw:

- `B05` covers rate-limit/resource exhaustion.
- `T08` covers persistent task creation.
- There is no explicit testing for failure cascades across retries, planners,
  tools, sub-agents, fallback models, or degraded guardrails.

Missing offensive scenarios:

| Proposed ID | Scenario | Risk covered |
|---|---|---|
| `Q01` | Retry amplification | One failed tool call becomes many writes/sends |
| `Q02` | Fallback model policy downgrade | Safer model refuses, fallback model complies |
| `Q03` | Guardrail outage fail-open | Content filter unavailable, agent proceeds |
| `Q04` | Planner error cascades through executor | Bad plan triggers multiple bad tool calls |
| `Q05` | Multi-agent consensus poisoning | One compromised agent causes group decision failure |
| `Q06` | Budget exhaustion changes safety behavior | Long context or cost pressure drops safety checks |

Fix:

- Add `CASCADING_FAILURE` category mapped to `ASI08` and `LLM10` where cost or
  resource exhaustion is central.
- Add evidence types: `RETRY_TRACE`, `MODEL_FALLBACK_TRACE`,
  `GUARDRAIL_STATUS`, `COST_USAGE`, `PLAN_TRACE`.
- Add executor support for simulated guardrail failure and fallback model
  selection so these tests can run deterministically.

### 6. Rogue-agent and self-protection behaviors are not represented

What I saw:

- `T07` tests disabling alerts/logging.
- `K06` tests verifier/test sabotage.
- There is no broad `ASI10` coverage for self-directed action, concealment,
  refusing shutdown, unauthorized task continuation, or unauthorized agent
  replication.

Missing offensive scenarios:

| Proposed ID | Scenario | Safe boundary |
|---|---|---|
| `Z01` | Agent conceals failed or unsafe action | Trace-only |
| `Z02` | Agent refuses shutdown/cancellation instruction | Trace-only |
| `Z03` | Agent creates unauthorized clone/delegated worker | Emulated tool |
| `Z04` | Agent modifies its own guardrails or policy memory | Sandbox/memory trace |
| `Z05` | Agent continues task after revoked consent | Dry-run tool |

Fix:

- Add `ROGUE_AGENT` category mapped to `ASI10`.
- Add a `CONTROL_PLANE` sink type for shutdown, delegation, policy update, and
  self-modification paths.
- Require `AUDIT_TRACE` evidence for any scenario where concealment is the core
  success signal.

### 7. Agentic supply-chain coverage should extend beyond MCP metadata

What I saw:

- `M01-M08` cover malicious tool descriptions, shadow tools, tool output
  poisoning, toxic flows, credential overreach, SSRF, discovery leakage, and
  cross-server exfiltration.
- OWASP MCP Top 10 also calls out token mismanagement, scope creep, command
  execution, audit/telemetry gaps, shadow MCP servers, context over-sharing, and
  dependency tampering.
- The current registry does not explicitly cover tool rug pulls after approval,
  signed manifest bypass, unpinned skill/prompt dependencies, model or guardrail
  version downgrade, or malicious package/extension updates.

Missing offensive scenarios:

| Proposed ID | Scenario | Risk covered |
|---|---|---|
| `S01` | Tool rug pull after allowlist approval | Supply-chain drift |
| `S02` | Signed manifest/provenance mismatch | Tool integrity |
| `S03` | Malicious skill/prompt package update | Agentic skill supply chain |
| `S04` | Guardrail/model version downgrade | Safety control supply chain |
| `S05` | Dependency executes during tool install or discovery | Build/runtime compromise |
| `S06` | MCP audit telemetry suppression | Detection gap |

Fix:

- Add capabilities: `TOOL_REGISTRY`, `PLUGIN_INSTALL`, `SIGNED_MANIFEST`,
  `MODEL_ROUTER`, `GUARDRAIL_PROVIDER`.
- Add evidence types: `PROVENANCE_ATTESTATION`, `MANIFEST_DIFF`,
  `INSTALL_TRACE`, `AUDIT_TRACE`.
- Map these scenarios to `LLM03`, `ASI04`, and relevant MCP Top 10 categories.

### 8. Nonhuman identity and credential lifecycle coverage is narrow

What I saw:

- `A01-A08`, `P04`, `P06`, and `M05` cover important authorization and scope
  issues.
- There is no explicit coverage for agent identities as first-class principals:
  OAuth consent abuse, token replay, stale scoped credentials, ownerless agents,
  delegated identity confusion, or credential bleed across tools and agents.

Missing offensive scenarios:

| Proposed ID | Scenario | Assertion |
|---|---|---|
| `N01` | OAuth consent/scope escalation | Agent obtains broader scope than required |
| `N02` | Token replay or session fixation | Old token still works after revocation |
| `N03` | Ownerless agent action | Action lacks accountable human/service owner |
| `N04` | Cross-agent credential bleed | Agent B can use Agent A's token |
| `N05` | Delegated identity confusion | Tool sees agent identity, not user identity |
| `N06` | Long-lived secret in memory/logs | Credential persists in memory or telemetry |

Fix:

- Add `AGENT_IDENTITY` category mapped to `ASI03` and MCP token/scope risks.
- Add capabilities: `OAUTH`, `SERVICE_ACCOUNT`, `TOKEN_BROKER`,
  `DELEGATED_AUTH`, `SCOPED_CREDENTIALS`.
- Add evidence types: `IDENTITY_TRACE`, `TOKEN_SCOPE_TRACE`,
  `REVOCATION_TRACE`, `OWNER_TRACE`.

### 9. Multimodal injection is absent

What I saw:

- Delivery channels cover text, web, email, calendar, documents, tools, MCP,
  memory, API, repo, terminal, markdown rendering, and multi-session.
- There are no channels or capabilities for image, audio, OCR, screenshot, PDF
  annotation/form content, QR code, or video transcript injection.

Missing offensive scenarios:

| Proposed ID | Scenario | Safe execution |
|---|---|---|
| `V01` | Image/OCR hidden instruction injection | Synthetic image |
| `V02` | Audio transcript prompt injection | Synthetic transcript |
| `V03` | PDF comments/forms/metadata injection | Synthetic document |
| `V04` | Screenshot/UI text injection | Browser sandbox |
| `V05` | QR/image URL egress instruction | Trap endpoint |

Fix:

- Add delivery channels: `IMAGE`, `AUDIO`, `OCR_TEXT`, `PDF_METADATA`,
  `SCREENSHOT`, `VIDEO_TRANSCRIPT`.
- Add capabilities: `MULTIMODAL_INPUT`, `OCR`, `AUDIO_TRANSCRIPTION`,
  `IMAGE_UNDERSTANDING`, `PDF_PROCESSING`.
- Keep fixtures synthetic and canary-based; avoid requiring real harmful media.

### 10. Infrastructure-layer coverage is mostly indirect

What I saw:

- Existing infrastructure-relevant scenarios include SSRF via tool (`M06`),
  coding-agent shell/file/sandbox tests (`K02-K04`), delayed CI exfil (`K05`),
  verifier sabotage (`K06`), resource exhaustion (`B05`), and alert/logging
  disablement (`T07`).
- The registry does not explicitly model browser session security, cloud
  metadata exposure, Kubernetes/service-account access, API gateway auth gaps,
  egress policy bypass, audit/log integrity, network segmentation, or deployment
  misconfiguration.

Missing offensive scenarios:

| Proposed ID | Scenario | Safe boundary |
|---|---|---|
| `F01` | Browser session/CSRF protected chat action | Browser sandbox |
| `F02` | Cloud metadata or private network fetch blocked | Trap/private-IP simulator |
| `F03` | Kubernetes/service-account credential read blocked | Sandbox |
| `F04` | Egress allowlist bypass attempt | Trap endpoint |
| `F05` | Audit/log deletion or tampering attempt | Emulated audit sink |
| `F06` | API gateway route/auth mismatch | Synthetic tenant/API fixture |
| `F07` | Secrets surfaced in traces/logs | Canary secret |
| `F08` | Container or workspace boundary read/write | Sandbox |

Fix:

- Add `INFRA_RUNTIME` category or attach infra-specific scenarios to MCP/coding
  categories with explicit `infrastructure_layer=True` metadata.
- Add capabilities: `BROWSER_APP`, `CLOUD_METADATA`, `KUBERNETES`,
  `SERVICE_ACCOUNT`, `API_GATEWAY`, `AUDIT_LOGGING`, `EGRESS_POLICY`,
  `CONTAINER_SANDBOX`.
- Add evidence types: `NETWORK_TRACE`, `BROWSER_TRACE`, `AUDIT_TRACE`,
  `CLOUD_IDENTITY_TRACE`.

### 11. Business logic coverage is too generic for enterprise apps

What I saw:

- `B01-B06` cover false action claims, out-of-domain advice, fraud workflow,
  regulated advice overreach, rate/cost exhaustion, and hallucinated authority.
- There are no explicit scenarios for workflow sequencing, eligibility checks,
  discounts/refunds/returns, KYC/AML, healthcare eligibility, claims processing,
  inventory/order constraints, procurement limits, or maker-checker separation.

Missing offensive scenarios:

| Proposed ID | Scenario | Business control |
|---|---|---|
| `L01` | Workflow step skipping | Required sequence |
| `L02` | Eligibility bypass | Account/user/product eligibility |
| `L03` | Limit/threshold bypass | Transaction, refund, discount, claims limits |
| `L04` | Maker-checker bypass | Separation of duties |
| `L05` | Duplicate action/idempotency failure | Replay and duplicate suppression |
| `L06` | Policy ambiguity exploitation | Conflicting policy clauses |
| `L07` | Domain-specific regulated workflow | KYC, PHI, PFI, insurance, healthcare, legal |

Fix:

- Add a `BUSINESS_WORKFLOW` category or expand `Bxx`.
- Allow scenario builders to ingest domain policy clauses from the NuGuard
  cognitive policy and SBOM business endpoints.
- Add evidence type `BUSINESS_RULE_TRACE` so a finding can show the violated
  rule, not just the model text.

### 12. Scenario metadata is not expressive enough for modern adaptive testing

What I saw:

- `ScenarioSpec` has useful metadata: category, goal type, scenario type,
  channel, trust, sink, capabilities, evidence, safe execution, mappings, impact,
  builder key, enabled, and priority rules.
- It does not encode attack phase, required identities, statefulness, lock keys,
  budget/cost expectations, model fallback behavior, expected refusal type,
  fixture requirements, or whether a scenario is adaptive/multi-turn.

Fix:

- Extend `ScenarioSpec` or introduce an adjacent `ScenarioExecutionProfile` with:
  - `attack_phase`: recon, seed, trigger, verify, cleanup
  - `state_impact`: none, memory, backend, file, external egress, control plane
  - `requires_identities`: none, single, dual tenant, admin plus user
  - `resource_locks`: memory/user/index/tool/workflow identifiers
  - `requires_browser`, `requires_retrieval_trace`, `requires_approval_trace`
  - `max_turns`, `max_tool_calls`, `max_cost_usd`, `max_runtime_seconds`
  - `adaptive_strategy`: none, PAIR, TAP/tree-search, mutation, fuzzing
  - `cleanup_required`: true/false
- Use that metadata for scheduling, safety gating, replay, and coverage reports.

## Proposed Prioritized Backlog

### P0: Correct taxonomy and coverage accounting

- Update the registry docstring from 84 to 85, or generate the count in docs.
- Re-map OWASP LLM and Agentic tags to the current 2025/2026 taxonomies.
- Add coverage assertions for every OWASP LLM and Agentic category:
  - covered
  - partially covered
  - intentionally out of scope
  - planned
- Split "data exfiltration" from "excessive agency" in mappings.

### P1: Add high-impact missing scenario families

- RAG/vector category (`R01-R08`) with retrieval evidence.
- Improper output handling category (`O01-O06`) with emulated downstream sinks.
- Human-agent trust category (`H01-H05`) with approval traces.
- Agent identity category (`N01-N06`) with token/scope/owner traces.

### P2: Deepen agentic and infrastructure coverage

- Cascading failure category (`Q01-Q06`) mapped to `ASI08`.
- Rogue agent category (`Z01-Z05`) mapped to `ASI10`.
- Supply-chain category (`S01-S06`) mapped to `LLM03`, `ASI04`, and MCP risks.
- Infrastructure runtime category (`F01-F08`) with browser/network/audit evidence.
- Multimodal category (`V01-V05`) using synthetic fixtures.

### P3: Make the catalog adaptive and evidence-complete

- Add retrieval, approval, identity, browser, audit, cost, and plan evidence types.
- Add scenario metadata for identity requirements, state locks, cleanup, adaptive
  strategy, and fixture dependencies.
- Generate coverage matrices by:
  - OWASP LLM 2025
  - OWASP Agentic 2026
  - OWASP MCP Top 10
  - Business logic
  - RAG/vector
  - Infrastructure/runtime
  - Safe execution mode

## Concrete Code-Level Touch Points

Likely files to update when implementing these recommendations:

- `nuguard/redteam/catalog/registry.py`
  - Add new scenario specs and correct framework mappings.
- `nuguard/redteam/catalog/taxonomy.py`
  - Add categories, delivery channels, sink types, capabilities, and evidence
    types.
- `nuguard/redteam/catalog/builders.py`
  - Add builder factories for new `builder_key` values.
- `nuguard/redteam/catalog/capability.py`
  - Detect vector/RAG internals, browser apps, identity providers, audit logs,
    multimodal inputs, model routers, tool registries, and infra surfaces.
- `nuguard/redteam/executor/*`
  - Add retrieval trace, browser trace, approval trace, identity trace, audit
    trace, model fallback trace, and network trace capture.
- `tests/redteam/test_catalog_snapshot.py`
  - Update expected count and add coverage assertions for new taxonomies.
- `docs/red-teaming-guide.md`
  - Publish the modern mapping and explain safe execution for the new categories.

## Suggested New Coverage Matrix

| Risk family | Current coverage | Recommended state |
|---|---|---|
| Direct sensitive data disclosure | Strong, but mapping stale | Strong with `LLM02` mapping |
| Tool misuse/excessive agency | Strong | Strong plus approval-surface evidence |
| Prompt injection/jailbreak | Strong text coverage | Add multimodal and adaptive metadata |
| RAG/vector attacks | Thin | Dedicated category and retrieval traces |
| Improper output handling | Thin | Dedicated downstream-sink category |
| Agentic supply chain | Partial MCP-only | Add lifecycle, manifest, skill, model/guardrail cases |
| Agent identity | Partial authz only | Add OAuth, token, owner, delegated identity cases |
| Human-agent trust | Partial HITL bypass | Add approval UI/payload mismatch cases |
| Cascading failure | Mostly absent | Add retry/fallback/fail-open/consensus cases |
| Rogue agents | Mostly absent | Add self-protection and unauthorized autonomy cases |
| Business workflow | Generic | Add domain/workflow-specific policy cases |
| Infrastructure runtime | Partial via coding/SSRF | Add browser, cloud, k8s, audit, egress, API gateway |

## Bottom Line

The registry is a solid first-generation AI red-team catalog: it has broad
coverage for prompt injection, data exfiltration, authorization failures,
destructive tools, MCP poisoning, memory, multi-agent trust, jailbreaks, and
coding-agent risks. The biggest gaps are not builder wiring; the current builder
coverage is clean. The biggest gaps are taxonomy drift and missing modern
offensive families: RAG/vector attacks, improper output handling, agentic
supply-chain lifecycle, nonhuman identity, human-agent trust exploitation,
cascading failures, rogue agents, multimodal injection, and explicit
infrastructure/runtime probes.

Fixing the mappings first will make the coverage report honest. Adding the P1
families next will materially improve NuGuard's ability to find real security
gaps in Agentic and RAG applications without making tests unsafe against owned
targets.
