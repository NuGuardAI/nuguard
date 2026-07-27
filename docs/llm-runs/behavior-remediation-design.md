# Behavior Remediation Design

## Problem Statement

The current `RecommendationEngine` in `nuguard/behavior/recommendations.py` produces generic, pattern-matched remediation text. From the Golden Bank report:

```
### [CRITICAL] system_prompt: Add violated clause to blocked_topics/actions: 
Rationale: Policy violation: Agent violates data handling rules by asking for password.

### [HIGH] system_prompt: Review and remediate: Data handling rules not respected
Rationale: Data handling rules not respected
```

These are unhelpful to a developer. A useful remediation needs to answer: **what exact text to add to which system prompt, which guardrail to configure and with what rule, or which architectural change to make.**

---

## Design Goal

After all findings are collected (static + dynamic), run a final **Remediation Pass** that:

1. Groups findings by affected SBOM node (agent, tool, guardrail)
2. For each node, synthesises concrete remediation actions using the SBOM node's existing content (system prompt text, tool description, guardrail config) + the finding evidence
3. Produces one of three artefact types per remediation:
   - **System prompt patch** — exact text to insert/replace in the agent's system prompt
   - **Guardrail rule** — guardrail type (input/output), trigger condition, and action (block / rewrite / escalate)
   - **Architectural change** — a structural fix (add auth gate, split tool, add HITL node)

---

## Architecture

### New class: `RemediationSynthesizer`

```
nuguard/behavior/remediation.py
```

Sits between the existing `RecommendationEngine` and report generation. Receives:
- `BehaviorAnalysisResult` (findings, deviations, coverage, intent)
- `AiSbomDocument` (node metadata, evidence, existing system prompts)
- `CognitivePolicy` (allowed/restricted topics, HITL triggers, data rules)
- `LLMClient` (optional — used for prompt patching; falls back to deterministic templates)

### Pipeline position

```
BehaviorAnalyzer.analyze()
  → static_findings + dynamic_findings
  → RecommendationEngine.generate()          # existing (fast, deterministic)
  → RemediationSynthesizer.synthesize()      # new (SBOM-aware, LLM-optional)
  → BehaviorAnalysisResult.remediation_plan  # new field
```

---

## Finding → Remediation Mapping

The synthesizer maps findings to remediation artefacts across eight categories, derived directly from the eight static alignment checks (BA-001 → BA-008) plus dynamic deviations.

| Category | Source | SBOM nodes involved |
|---|---|---|
| 1. Sensitive data request | dynamic `policy_violation` | AGENT, PROMPT |
| 2. HITL trigger not honoured | dynamic `policy_violation` / BA-008 | AGENT, TOOL, GUARDRAIL |
| 3. Blocked topics gap | BA-007 | AGENT, PROMPT |
| 4. Data leak / output leak | dynamic `data_leak` / BA-004 | TOOL, DATASTORE |
| 5. Intent misalignment / wrong tool | dynamic `intent_misalignment` | AGENT, TOOL, PROMPT |
| 6. HITL gate missing (structural) | BA-008 | system / GUARDRAIL |
| 7. Privilege escalation — high-privilege tool | BA-005, BA-002 | AGENT, TOOL, PRIVILEGE, AUTH |
| 8. Restricted-action tool reachable | BA-003, BA-006 | AGENT, TOOL, GUARDRAIL |

---

### 1. Policy violation — agent asks for sensitive data

**Finding pattern**: `deviation_type=policy_violation`, evidence contains "password" / "PII" / "asking for"

**Remediation type**: System prompt patch

**Action**:
1. Locate the SBOM PROMPT node(s) wired to the affected agent
2. Read `metadata.extras["content"]` (the actual system prompt text)
3. Generate a patch instruction:
   - **Deterministic**: append a `## Security Rules` section with explicit prohibitions
   - **LLM path**: ask the LLM to rewrite just the data handling section

**Output**:
```
Component: Golden Bank AI Support (AGENT)
Patch target: golden_bank_system_prompt (PROMPT node, webapp/prompts/system.py:12)

--- existing ---
You are a helpful banking assistant...

+++ add to system prompt ---
## Data Handling Rules
- NEVER ask the user for their password, PIN, or full card number.
- If authentication is required, direct the user to the secure login portal.
- You may confirm the last 4 digits of an account number but never the full number.
```

---

### 2. Policy violation — HITL trigger not honoured

**Finding pattern**: `deviation_type=policy_violation`, mentions "escalation" / "human agent"

**Remediation type**: Guardrail rule + system prompt patch (dual output)

**Action**:
1. Find the `escalate_to_human_agent` TOOL node in SBOM
2. Find the PROMPT node for the affected agent
3. Generate:
   - An **input guardrail rule**: pattern that should trigger escalation
   - A **system prompt clause** stating when to call the escalation tool

**Output**:
```
Component: Golden Bank AI Support (AGENT)

[Guardrail — Input]
Trigger: user message matches "speak to (a|an)? (agent|human|representative|person)" (regex)
Action: ROUTE → escalate_to_human_agent tool
Priority: before LLM call

[System Prompt Patch]
Add to agent instructions:
"If the customer says they want to speak to a human, agent, or representative,
 immediately call escalate_to_human_agent() without further conversation."
```

---

### 3. Blocked topics missing from agent config

**Finding pattern**: static finding `BA-001` — agent `blocked_topics` missing required restrictions

**Remediation type**: System prompt patch (+ optional guardrail)

**Action**:
1. Read the policy `restricted_topics` list
2. Read the agent's existing PROMPT content
3. Format the missing topics into a `## Out of Scope` section

**Output**:
```
Component: Golden Bank AI Support (AGENT)

[System Prompt Patch]
Add section:
## Out of Scope
Do NOT discuss or assist with any of the following:
- "Any topic outside of banking services, such as financial advice, investment strategies..."
- "Any topic that involves personal financial information or sensitive data..."
If asked about these, respond: "I can only assist with Golden Bank account services."

[Optional: Input Guardrail]
Type: topic_classifier
Blocked topics: ["financial_advice", "investment_strategy", "insurance"]
Action: BLOCK with message "I'm sorry, that's outside my area of expertise."
```

---

### 4. Data leak — sensitive fields in response

**Finding pattern**: `deviation_type=data_leak`, SBOM node has classified fields

**Remediation type**: Output guardrail

**Output**:
```
Component: add_biller (TOOL)

[Guardrail — Output]
Type: field_redactor
Scan response for patterns matching: account_number, routing_number, SSN, card_number
Action: REDACT matching values with "[REDACTED]"
On match: log security event + continue (do not block)
```

---

### 5. Intent misalignment — component not mentioned / wrong tool used

**Finding pattern**: `deviation_type=intent_misalignment`, `component_correctness` score 1

**Remediation type**: System prompt patch (tool invocation instruction)

**Action**:
1. Identify which component was expected but not invoked
2. Read tool description from SBOM
3. Generate explicit invocation instruction

**Output**:
```
Component: get_account_summary (TOOL)

[System Prompt Patch — Golden Bank AI Support]
Add explicit invocation instruction:
"When the user asks for their account balance, account overview, or summary:
 call get_account_summary(account_id=<user's account>) and present the result."
```

---

### 6. HITL gate missing (static finding)

**Finding pattern**: static `BA-006` — no GUARDRAIL node for a policy HITL trigger

**Remediation type**: Architectural change + guardrail spec

**Output**:
```
HITL Trigger: "Any statement saying need to speak to an agent or representative..."

[Architectural Change]
Add a GUARDRAIL node to the SBOM connected to the Golden Bank AI Support agent.
Node type: GUARDRAIL
Relationship: PROTECTS → Golden Bank AI Support

[Guardrail Specification]
Name: human_escalation_guard
Type: input_classifier
Model: regex OR intent classifier
Rules:
  - pattern: \b(speak|talk|connect)\s+(to\s+)?(a\s+)?(human|agent|person|representative)\b
    action: ROUTE → escalate_to_human_agent()
    fallback: "Let me connect you with a team member who can help."
```

---

### 7. Privilege escalation — unauthenticated agent with high-privilege tool (BA-005)

**Finding pattern**: static `BA-005` — agent has `no_auth_required=True` but a CALLS edge to a TOOL with `high_privilege=True` (TOOL is connected to a PRIVILEGE node in the SBOM).

**SBOM signals used**:
- `node.metadata.no_auth_required` on the AGENT node
- `node.metadata.high_privilege` on the TOOL node (set by the SBOM enricher when a PRIVILEGE node is reachable)
- The PRIVILEGE node's `name` (e.g. `privilege:db_write`, `privilege:filesystem_write`, `privilege:admin`, `privilege:code_execution`)
- The tool description / parameters (to explain what the privileged action does)

**Remediation type**: Architectural change + system prompt patch (dual output)

**Action**:
1. Look up the PRIVILEGE node(s) reachable from the flagged TOOL
2. Map the `PrivilegeScope` to a concrete risk description and mitigation strategy:
   - `db_write` → require auth gate + parameterised query guardrail
   - `filesystem_write` → require auth gate + path allowlist guardrail
   - `code_execution` → require auth gate + sandbox / HITL approval
   - `admin` → require auth gate + manager HITL before any admin action
   - `network_out` → require auth gate + URL allowlist guardrail
   - `email_out` / `social_media_out` → require auth gate + HITL approval

**Output** (example: `transfer_funds` tool with `db_write` privilege, unauthenticated agent):
```
Component: Golden Bank AI Support (AGENT)  →  transfer_funds (TOOL)
Privilege node: privilege:db_write

[Architectural Change — Critical]
The agent 'Golden Bank AI Support' can reach the high-privilege tool
'transfer_funds' without authentication. This is a privilege escalation risk.

Required changes:
1. Add an AUTH node (type: bearer / basic / oauth2) connected to
   'Golden Bank AI Support' via a PROTECTS edge in the SBOM.
2. Wire the AUTH node to also PROTECT 'transfer_funds' directly.
3. The application must verify a valid session token before any
   transfer_funds() invocation.

[Input Guardrail — transfer_funds]
Name: transfer_auth_gate
Type: auth_check
Trigger: any call to transfer_funds()
Check: valid session token OR account ownership assertion present
Action on failure: BLOCK + respond "Please log in to complete this action."

[System Prompt Patch — Golden Bank AI Support]
Add to agent instructions:
"Before calling transfer_funds(), confirm that the customer is authenticated.
 If no active session is detected, prompt the user to log in at the secure
 portal before proceeding. Never transfer funds for an unauthenticated caller."
```

---

### 8. Restricted-action tool reachable without guardrail (BA-003 + BA-006)

**Finding pattern**:
- `BA-003` — a policy `restricted_action` matches a tool description, and an agent has a direct CALLS edge to that tool with no GUARDRAIL PROTECTS edge
- `BA-006` — an untrusted MCP server has a write-capable tool with no guardrail

**SBOM signals used**:
- Policy `restricted_actions` list (the specific action text gives context)
- `edge.access_type` on ACCESSES edges (read / write / readwrite)
- Tool `name`, `description`, and `parameters` (to craft specific guardrail rules)
- GUARDRAIL nodes already in SBOM (to see what's missing)
- MCP server `trust_level` metadata

**Remediation type**: Input guardrail + system prompt patch

**Action**:
1. Read the `restricted_action` text to understand what the policy forbids
2. Detect whether the issue is:
   - **A missing guardrail** (tool reachable but unprotected) → add guardrail
   - **An unnecessary CALLS edge** (agent shouldn't have access at all) → remove edge
3. For write-capable tools (delete, update, create, transfer), always add HITL confirmation requirement

**Output** (example: `approve_discount` with restricted action "approve discounts without manager confirmation"):
```
Component: cymbal_upsell_agent (AGENT)  →  approve_discount (TOOL)
Restricted action: "Call update_cart or ask_to_modify_cart without explicit prior user confirmation"

[Option A — Preferred: Add confirmation guardrail]
Name: discount_confirmation_gate
Type: confirmation_required
Scope: all calls to approve_discount()
Requirement: explicit user confirmation in the same conversation turn
Pattern: user message must contain affirmative ("yes", "confirm", "go ahead", "proceed")
Action if missing: HOLD — ask "Would you like me to apply this discount? (yes/no)"

[System Prompt Patch — cymbal_upsell_agent]
Add to agent instructions:
"Before calling approve_discount() or ask_to_modify_cart(), you MUST receive
 explicit confirmation from the customer in the same turn (e.g. 'yes', 'go ahead').
 Do not apply discounts or modify the cart based on implied consent."

[Option B — If tool access is unnecessary]
Remove the CALLS edge from cymbal_upsell_agent → approve_discount in the SBOM
and restrict discount approval to the manager-facing workflow only.
```

**Output** (example: untrusted MCP server with `filesystem_write` privilege):
```
Component: external_mcp_server (FRAMEWORK, trust_level=untrusted)  →  write_file (TOOL)
MCP server privilege: privilege:filesystem_write

[Architectural Change — High]
Untrusted MCP server 'external_mcp_server' has write-capable tool access.
This is an MCP tool poisoning / confused-deputy risk.

Required changes:
1. Set trust_level to "trusted" only after server identity is verified (TLS + signed manifest).
2. If the server must remain untrusted, remove the CALLS edge to write_file.
3. If write access is required, add a GUARDRAIL PROTECTS edge:

[Input Guardrail — write_file]
Name: mcp_write_guard
Type: allowlist
Scope: all write operations from external_mcp_server
Allowed paths: ["/data/output/", "/tmp/nuguard/"]
Action: BLOCK all writes outside allowlist paths + log security event
```

---

### Privilege → Remediation Strategy Table

The synthesizer uses this lookup table to select the concrete mitigation per `PrivilegeScope` value:

| PrivilegeScope | Risk | Primary mitigation | Secondary mitigation |
|---|---|---|---|
| `db_write` | Data corruption, exfiltration | Auth gate + parameterised query guardrail | HITL for bulk operations |
| `filesystem_write` | Code injection, data destruction | Auth gate + path allowlist guardrail | Sandbox isolation |
| `code_execution` | Full system compromise | Auth gate + sandbox + HITL approval | Remove tool if not essential |
| `admin` | Privilege escalation, account takeover | Auth gate + manager HITL for every action | Separate admin interface |
| `network_out` | SSRF, data exfiltration | Auth gate + URL allowlist guardrail | Egress firewall rule |
| `email_out` | Phishing, spam | Auth gate + HITL approval | Rate limiting |
| `social_media_out` | Brand risk, misinformation | HITL approval before every post | Dry-run mode |
| `rbac` | Horizontal privilege escalation | Auth gate + role assertion in every call | Audit logging |

---

## `RemediationSynthesizer` Implementation Sketch

```python
class RemediationSynthesizer:
    """SBOM-aware remediation synthesizer.

    Converts raw findings into concrete, node-specific remediation artefacts.
    """

    def __init__(
        self,
        sbom: AiSbomDocument | None = None,
        policy: CognitivePolicy | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._sbom = sbom
        self._policy = policy
        self._llm = llm_client
        # Build lookup maps
        self._node_by_name: dict[str, Node] = {}
        self._prompt_by_agent: dict[str, Node] = {}  # agent name → PROMPT node
        if sbom is not None:
            _build_lookup_maps(sbom, self._node_by_name, self._prompt_by_agent)

    def synthesize(
        self,
        result: BehaviorAnalysisResult,
    ) -> list[RemediationArtefact]:
        """Return concrete remediation artefacts for all findings."""
        artefacts: list[RemediationArtefact] = []
        all_findings = result.static_findings + result.dynamic_findings

        for finding in all_findings:
            artefact = self._synthesize_one(finding, result)
            if artefact:
                artefacts.append(artefact)

        # Merge artefacts for the same component+type (avoid duplicate patches)
        return _merge_artefacts(artefacts)

    def _synthesize_one(
        self,
        finding: dict,
        result: BehaviorAnalysisResult,
    ) -> RemediationArtefact | None:
        component = finding.get("affected_component", "unknown")
        node = self._node_by_name.get(component)
        dtype = _classify_finding(finding)

        if dtype == "sensitive_data_request":
            return self._patch_data_handling(component, node, finding)
        elif dtype == "hitl_not_honoured":
            return self._add_hitl_guardrail(component, node, finding)
        elif dtype == "blocked_topics_missing":
            return self._patch_blocked_topics(component, node, finding)
        elif dtype == "data_leak":
            return self._add_output_redactor(component, node, finding)
        elif dtype == "intent_misalignment":
            return self._patch_tool_invocation(component, node, finding)
        elif dtype == "hitl_gate_missing":
            return self._spec_hitl_guardrail(component, node, finding)
        elif dtype == "privilege_escalation":
            return self._remediate_privilege_escalation(component, node, finding)
        elif dtype == "restricted_action_reachable":
            return self._remediate_restricted_action(component, node, finding)
        return None
```

---

## New Data Model: `RemediationArtefact`

```python
class RemediationArtefactType(str, Enum):
    SYSTEM_PROMPT_PATCH = "system_prompt_patch"
    INPUT_GUARDRAIL = "input_guardrail"
    OUTPUT_GUARDRAIL = "output_guardrail"
    ARCHITECTURAL_CHANGE = "architectural_change"

class RemediationArtefact(BaseModel):
    """A concrete, node-specific remediation action."""

    finding_ids: list[str]               # which findings this addresses
    component: str                       # SBOM node name
    component_type: str                  # AGENT | TOOL | GUARDRAIL | system
    artefact_type: RemediationArtefactType
    priority: str                        # critical | high | medium | low

    # System prompt patch fields
    patch_location: str | None = None    # e.g. "webapp/prompts/system.py:12"
    patch_section: str | None = None     # section title to add/replace
    patch_text: str | None = None        # the exact text to insert

    # Guardrail fields
    guardrail_name: str | None = None
    guardrail_type: str | None = None    # input_classifier | output_redactor | regex |
                                         # topic_classifier | auth_check | allowlist |
                                         # confirmation_required | rate_limiter
    guardrail_trigger: str | None = None # condition (regex, topic label, auth check, etc.)
    guardrail_action: str | None = None  # BLOCK | REDACT | ROUTE | ESCALATE | HOLD
    guardrail_message: str | None = None # message to show user on block/hold

    # Privilege-specific fields (populated for BA-005, BA-003, BA-006 findings)
    privilege_scope: str | None = None   # PrivilegeScope value: db_write, admin, etc.
    privilege_node: str | None = None    # PRIVILEGE SBOM node name
    requires_auth: bool = False          # True when an AUTH node must be added
    requires_hitl: bool = False          # True when HITL is mandated for this action
    edge_to_remove: tuple[str, str] | None = None  # (source_node, target_node) CALLS edge
                                                    # to remove if access is not needed

    # Architectural change fields
    change_description: str | None = None
    change_detail: str | None = None

    rationale: str                       # human-readable explanation
```

---

## LLM Pass for System Prompt Patches

When `llm_client` is available, the synthesizer uses it to generate the `patch_text` for `SYSTEM_PROMPT_PATCH` artefacts:

```
System: You are a security engineer fixing an AI agent's system prompt.
        The agent is: {agent purpose from intent}.
        The policy violation is: {finding description}.
        The existing system prompt section is:
        ---
        {prompt_content}
        ---
        Write ONLY the new or replacement section text to fix the violation.
        Be specific. 40 words max. Do not explain — output the section text only.
```

This ensures the patch is contextual (uses the actual prompt wording) rather than generic.

Without LLM, the synthesizer falls back to **deterministic templates** per finding type.

---

## Report Integration

The remediation plan replaces the current `## Recommendations` section with `## Remediation Plan`.

Each artefact is rendered as:

```markdown
### [PRIORITY] Component: Golden Bank AI Support

**Type**: System Prompt Patch  
**Addresses**: Policy violation — agent asks for password, data leak  
**Patch location**: `webapp/prompts/system.py` (line 12)

**Add section `## Data Handling Rules`:**
```text
NEVER ask the user for their password, PIN, or full card number. If
authentication is required, direct to the secure login portal at
https://online.goldenbank.com/login.
```

---

### [HIGH] Component: system

**Type**: Input Guardrail + System Prompt Patch  
**Addresses**: HITL gate missing for "speak to a human" trigger

**Guardrail spec:**
- Name: `human_escalation_guard`
- Type: regex input classifier
- Pattern: `\b(speak|talk|connect)\s+(to\s+)?(a\s+)?(human|agent|person|representative)\b`
- Action: ROUTE → `escalate_to_human_agent()`

**System prompt addition:**
```text
When a customer says they want to speak with a human, agent, or
representative, immediately call escalate_to_human_agent() without further
conversation.
```
```

---

## Implementation Plan

### Phase 1 — Data model and deterministic synthesizer (no LLM)

1. Add `RemediationArtefact` and `RemediationArtefactType` to `behavior/models.py`
2. Add `remediation_plan: list[RemediationArtefact]` to `BehaviorAnalysisResult`
3. Create `nuguard/behavior/remediation.py` with `RemediationSynthesizer`
4. Implement deterministic template paths for all 8 finding types (including privilege escalation and restricted-action categories)
5. Wire into `BehaviorAnalyzer.analyze()` after `RecommendationEngine.generate()`
6. Update `report.py` to render `## Remediation Plan` from artefacts

**Privilege-specific implementation details (Phase 1)**:
- Build a `privilege_map: dict[str, list[str]]` in `_build_lookup_maps()`:
  - Keys: TOOL node names
  - Values: list of PRIVILEGE node names reachable via CALLS edges and the PRIVILEGE node lookup
- This map is checked when handling BA-005 and BA-003 findings
- Use the `_PRIVILEGE_STRATEGY` lookup table (keyed on `PrivilegeScope` values) to select the correct guardrail type, required auth flag, and HITL flag

```python
_PRIVILEGE_STRATEGY: dict[str, dict] = {
    "db_write":           {"guardrail": "auth_check+parameterised_query", "requires_auth": True,  "requires_hitl": False},
    "filesystem_write":   {"guardrail": "auth_check+path_allowlist",       "requires_auth": True,  "requires_hitl": False},
    "code_execution":     {"guardrail": "auth_check+sandbox",              "requires_auth": True,  "requires_hitl": True},
    "admin":              {"guardrail": "auth_check",                      "requires_auth": True,  "requires_hitl": True},
    "network_out":        {"guardrail": "url_allowlist",                   "requires_auth": True,  "requires_hitl": False},
    "email_out":          {"guardrail": "hitl_approval",                   "requires_auth": True,  "requires_hitl": True},
    "social_media_out":   {"guardrail": "hitl_approval",                   "requires_auth": True,  "requires_hitl": True},
    "rbac":               {"guardrail": "role_assertion",                  "requires_auth": True,  "requires_hitl": False},
}
```

### Phase 2 — LLM system prompt patch generation

1. Add `_patch_with_llm()` to `RemediationSynthesizer`
2. Read PROMPT node content from `node.metadata.extras["content"]`
3. Build targeted prompt and call `llm_client.complete()`
4. Parse response, validate it's < 200 words, fall back to template on failure

**Privilege-specific LLM prompt** (for BA-005/BA-003):
```
System: You are a security engineer writing a restriction for an AI agent's system prompt.
        The agent: {agent purpose}.
        The high-privilege tool: {tool_name} — {tool_description}.
        The privilege granted by this tool: {privilege_scope} ({risk_description}).
        Write ONLY the new "Access Controls" section to add to the agent's system prompt.
        Be specific about when the tool may and may not be called. Under 60 words.
```

### Phase 3 — Merge + deduplication

1. Group artefacts by `(component, artefact_type)`
2. For `SYSTEM_PROMPT_PATCH` artefacts on the same component, merge `patch_text` into a single section
3. Assign aggregated finding_ids to merged artefact
4. For privilege artefacts: if both `ARCHITECTURAL_CHANGE` and `INPUT_GUARDRAIL` are generated for the same (agent, tool) pair, keep them as a linked pair (reference each other's `finding_ids`) rather than merging

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Separate `RemediationSynthesizer` from `RecommendationEngine` | Recommendations remain fast/deterministic; synthesis is optional/async |
| SBOM node lookup by name | Components are identified by name in findings; name→Node map avoids O(n) scans |
| Read actual prompt text | Patches need the real current wording to be contextual, not generic |
| LLM is optional | Deterministic templates always work; LLM improves quality when available |
| Artefact types (not free text) | Structured artefacts can be rendered as patch files, Terraform, or IaC in future |
| Merge artefacts per component | One consolidated patch per agent is more actionable than 12 separate ones |
| `privilege_map` built from SBOM enricher output | `high_privilege` flag already set by enricher; synthesizer reads it rather than re-traversing the graph |
| Dual artefact for privilege (ARCHITECTURAL_CHANGE + INPUT_GUARDRAIL) | Auth gate is the mandatory fix; guardrail is defence-in-depth — both are needed and have distinct owners (infra vs. app team) |
| `_PRIVILEGE_STRATEGY` lookup table | Maps each `PrivilegeScope` to a concrete, consistent mitigation so the synthesizer doesn't need LLM reasoning for privilege decisions |
| `edge_to_remove` field on artefact | When a restricted action is reachable purely because of an unnecessary CALLS edge, the fix is structural (remove the edge), not a guardrail |
| Privilege remediations always set `requires_auth=True` | Any high-privilege tool must never be callable without authentication — this is a hard invariant regardless of the specific privilege scope |
