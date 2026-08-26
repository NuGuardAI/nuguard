# Red-Team Scenario Catalog

Reference for the attack vectors NuGuard's red-team engine can generate. See [red-teaming-guide.md](red-teaming-guide.md) for how to configure a scan and [redteam-design.md](redteam-design.md) for the engine's internal architecture.

The built-in catalog ships **125 scenario specs** across **18 attack categories**. Each spec is SBOM-grounded: the `ScenarioGenerator` only instantiates a scenario against components that actually exist in the scanned application (e.g. an `R`-category RAG attack is only built if the SBOM has a vector store / retrieval component), and only if the SBOM exposes the `required_capabilities` that scenario needs.

---

## How a scenario is chosen

Every catalog entry carries:

- **`goal_type`** — one of 9 high-level attack goals (see [GoalType taxonomy](#goaltype-taxonomy)). This is what `redteam.scenarios` / `--scenarios` filters on.
- **`base_impact`** (0–10) — a pre-score used by `redteam.profile` / `--profile` to decide whether the scenario is worth running (see [Profile filtering](red-teaming-guide.md#redteam-profile-ci-vs-full)).
- **`required_capabilities`** — SBOM signals (e.g. `rag`, `multi_session`, `sensitive_context`) that must be present for the scenario to apply.
- **`safe_execution`** — the containment strategy used so the attack can run against a real target without causing real-world harm (see [Safe execution modes](#safe-execution-modes)).
- **`priority_rules`** — tie-breakers used when trimming the plan to `max_scenarios` or a profile budget.
- **`enabled`** — whether the scenario is active by default; disabled entries (currently only `V03`) are opt-in via a customized `--catalog` file.

---

## GoalType taxonomy

`redteam.scenarios` (`--scenarios`) filters by these 9 values. Each maps to one or more catalog categories below:

| GoalType (yaml/CLI value) | Description | Catalog categories |
|---|---|---|
| `prompt-driven-threat` | Prompt injection, jailbreaks, guardrail bypass, system-prompt extraction | `I`, `J`, plus parts of `E`, `O`, `R`, `S`, `V` |
| `data-exfiltration` | PII/PHI/secret disclosure, cross-tenant leaks, covert encoding | `C`, `D`, plus parts of `N`, `O`, `R`, `S`, `V` |
| `privilege-escalation` | Auth bypass, role override, credential/consent abuse | `H`, plus parts of `A`, `N`, `O`, `V` |
| `tool-abuse` | Unsafe or destructive tool invocation | `K`, `T`, plus parts of `B`, `H`, `O` |
| `mcp-toxic-flow` | Untrusted content flowing into write-capable MCP tools | `M`, plus part of `R` |
| `api-attack` | BOLA/BFLA, mass assignment, schema-level exploitation | `S`, plus part of `A` |
| `agentic-trust-abuse` | Confused deputy, memory poisoning, multi-agent trust boundaries | `G`, `P` |
| `policy-violation` | Topic-boundary and restricted-action violations | `B`, plus part of `A` |
| `recon-inference` | Information inferred from refusal patterns / response timing | parts of `E`, `R`, `S`, `V` |

---

## Categories and scenarios

Prefix letters are the stable catalog ID prefix (e.g. `D01`–`D08`). IDs are referenced directly by `--scenarios D01,C03` or in `redteam.scenarios`.

### A — Authorization Failures (8)

| ID | Title | Impact | Goal |
|---|---|---|---|
| A01 | BOLA other-user object read | 8.0 | api-attack |
| A02 | BOLA other-user object write | 8.5 | api-attack |
| A03 | BFLA privileged function call | 7.5 | api-attack |
| A04 | RBAC role override | 7.0 | privilege-escalation |
| A05 | Auth-bypass via false verification | 7.0 | privilege-escalation |
| A06 | HITL bypass | 7.0 | policy-violation |
| A07 | Mass assignment hidden fields | 7.0 | api-attack |
| A08 | Debug/admin command exposure | 6.5 | api-attack |

### B — Business Logic and Safety (6)

| ID | Title | Impact | Goal |
|---|---|---|---|
| B01 | False successful action claim | 6.0 | policy-violation |
| B02 | Unsafe recommendation outside domain | 5.5 | policy-violation |
| B03 | Fraudulent refund/chargeback workflow | 6.5 | policy-violation |
| B04 | Compliance/legal/medical/financial advice overreach | 5.5 | policy-violation |
| B05 | Rate-limit/resource exhaustion | 5.5 | tool-abuse |
| B06 | Hallucinated authority/source | 5.0 | policy-violation |

### C — Covert Exfiltration (8)

Data smuggled out through side channels rather than plain-text answers.

| ID | Title | Impact | Goal |
|---|---|---|---|
| C01 | Markdown image pixel | 7.5 | data-exfiltration |
| C02 | Reference-style markdown link | 7.0 | data-exfiltration |
| C03 | URL query/path/fragment encoding | 7.5 | data-exfiltration |
| C04 | Base64/hex/ROT encoding | 6.5 | data-exfiltration |
| C05 | JSON/XML field smuggling | 6.0 | data-exfiltration |
| C06 | CSV/document metadata smuggling | 6.0 | data-exfiltration |
| C07 | Invisible Unicode/ASCII smuggling | 6.5 | data-exfiltration |
| C08 | Logging/sync/analytics pretext | 7.0 | data-exfiltration |

### D — Data Exfiltration (8)

Direct disclosure of PII/PHI/secrets or other users' data.

| ID | Title | Impact | Goal |
|---|---|---|---|
| D01 | Direct profile dump | 7.0 | data-exfiltration |
| D02 | Cross-tenant lookup | 8.0 | data-exfiltration |
| D03 | Adjacent-ID/IDOR probe | 7.5 | data-exfiltration |
| D04 | Cross-session secret leak | 8.0 | data-exfiltration |
| D05 | Private document extraction | 7.0 | data-exfiltration |
| D06 | RAG citation overreach | 6.5 | data-exfiltration |
| D07 | Aggregated PII across turns | 6.5 | data-exfiltration |
| D08 | Unauthorized location/history disclosure | 6.5 | data-exfiltration |

### E — Evasion and Robustness (7)

| ID | Title | Impact | Goal |
|---|---|---|---|
| E01 | Multilingual restricted request | 5.5 | prompt-driven-threat |
| E02 | Homoglyph/leetspeak | 5.5 | prompt-driven-threat |
| E03 | Context flooding | 5.5 | prompt-driven-threat |
| E04 | Structured JSON/XML override | 5.5 | prompt-driven-threat |
| E05 | Refusal oracle reconnaissance | 5.0 | recon-inference |
| E06 | Model/tool/system prompt extraction | 5.5 | prompt-driven-threat |
| E07 | Unexpected code generation probe | 7.0 | prompt-driven-threat |

### G — Multi-Agent Trust Abuse (6)

| ID | Title | Impact | Goal |
|---|---|---|---|
| G01 | Confused deputy | 7.0 | agentic-trust-abuse |
| G02 | Sub-agent output injection | 7.5 | agentic-trust-abuse |
| G03 | Handoff-chain privilege escalation | 7.5 | agentic-trust-abuse |
| G04 | Agent impersonation | 6.5 | agentic-trust-abuse |
| G05 | Planner/executor mismatch | 7.0 | agentic-trust-abuse |
| G06 | Supervisor approval spoof | 7.0 | agentic-trust-abuse |

### H — Human-Agent Trust Exploitation (5)

Attacks that abuse the human-in-the-loop approval step itself.

| ID | Title | Impact | Goal |
|---|---|---|---|
| H01 | Approval summary mismatch | 8.0 | privilege-escalation |
| H02 | Consent laundering | 7.5 | privilege-escalation |
| H03 | Authority-bias operator phishing | 7.0 | privilege-escalation |
| H04 | Partial approval overreach | 8.0 | privilege-escalation |
| H05 | Hidden recipient/action payload | 8.5 | tool-abuse |

### I — Indirect Prompt Injection (8)

Instructions smuggled into content the agent ingests, rather than the user prompt.

| ID | Title | Impact | Goal |
|---|---|---|---|
| I01 | Poisoned webpage | 7.5 | prompt-driven-threat |
| I02 | Poisoned email/message | 7.5 | prompt-driven-threat |
| I03 | Poisoned calendar/event | 7.5 | prompt-driven-threat |
| I04 | Poisoned file/document | 7.5 | prompt-driven-threat |
| I05 | Poisoned search result | 7.5 | prompt-driven-threat |
| I06 | Poisoned tool response | 7.5 | prompt-driven-threat |
| I07 | Poisoned database row | 7.5 | prompt-driven-threat |
| I08 | Prompt injection in markdown/HTML | 7.5 | prompt-driven-threat |

### J — Jailbreak and Policy Bypass (6)

Published jailbreak techniques (Crescendo, Many-Shot, Skeleton Key, etc.).

| ID | Title | Impact | Goal |
|---|---|---|---|
| J01 | Crescendo escalation | 6.0 | prompt-driven-threat |
| J02 | Many-shot jailbreak | 6.0 | prompt-driven-threat |
| J03 | Skeleton Key/developer framing | 6.0 | prompt-driven-threat |
| J04 | Roleplay/fictional training bypass | 6.0 | prompt-driven-threat |
| J05 | Payload splitting across turns | 6.0 | prompt-driven-threat |
| J06 | Policy-conflict false premise | 6.0 | prompt-driven-threat |

### K — Coding and Automation Agents (6)

Targets coding assistants and CI-connected agents.

| ID | Title | Impact | Goal |
|---|---|---|---|
| K01 | Repo prompt injection | 7.0 | tool-abuse |
| K02 | Shell injection | 8.0 | tool-abuse |
| K03 | Secret env/file read | 7.5 | tool-abuse |
| K04 | Sandbox read/write escape | 7.5 | tool-abuse |
| K05 | Delayed CI exfiltration | 7.5 | tool-abuse |
| K06 | Verifier/test sabotage | 7.0 | tool-abuse |

### M — MCP and Tool Poisoning (8)

Model Context Protocol-specific attacks — untrusted content flowing from an MCP server into a write-capable tool (`MCP_TOXIC_FLOW`).

| ID | Title | Impact | Goal |
|---|---|---|---|
| M01 | Malicious tool description | 7.5 | mcp-toxic-flow |
| M02 | Lookalike/shadow tool | 7.0 | mcp-toxic-flow |
| M03 | Tool output instructs second tool | 7.5 | mcp-toxic-flow |
| M04 | Toxic flow from untrusted source to write sink | 8.0 | mcp-toxic-flow |
| M05 | Credential-scope overreach | 7.0 | mcp-toxic-flow |
| M06 | External fetch/SSRF via tool | 7.5 | mcp-toxic-flow |
| M07 | Tool-discovery leakage | 5.5 | mcp-toxic-flow |
| M08 | Multi-server cross-tool exfiltration | 8.0 | mcp-toxic-flow |

### N — Agent Identity and Credential (6)

| ID | Title | Impact | Goal |
|---|---|---|---|
| N01 | OAuth consent/scope escalation | 8.0 | privilege-escalation |
| N02 | Token replay/session fixation | 8.5 | data-exfiltration |
| N03 | Ownerless agent action | 7.5 | privilege-escalation |
| N04 | Cross-agent credential bleed | 8.5 | privilege-escalation |
| N05 | Delegated identity confusion | 7.5 | privilege-escalation |
| N06 | Credential persistence in memory | 7.0 | data-exfiltration |

### O — Improper Output Handling (6)

Downstream systems trusting agent-generated output without validation.

| ID | Title | Impact | Goal |
|---|---|---|---|
| O01 | HTML/Markdown XSS injection | 7.0 | prompt-driven-threat |
| O02 | JSON tool argument injection | 7.5 | tool-abuse |
| O03 | Generated SQL tenant bypass | 8.0 | data-exfiltration |
| O04 | Generated URL causes SSRF | 8.5 | tool-abuse |
| O05 | YAML/CI config security override | 7.5 | privilege-escalation |
| O06 | File/MIME confusion artifact | 7.0 | privilege-escalation |

### P — Memory and Persistence (6)

| ID | Title | Impact | Goal |
|---|---|---|---|
| P01 | Poison long-term memory | 7.5 | agentic-trust-abuse |
| P02 | Poison preference/profile field | 7.0 | agentic-trust-abuse |
| P03 | Cross-session backdoor instruction | 8.0 | agentic-trust-abuse |
| P04 | False identity memory | 7.5 | agentic-trust-abuse |
| P05 | Malicious summary persistence | 6.5 | agentic-trust-abuse |
| P06 | Memory-based authorization drift | 6.5 | agentic-trust-abuse |

### R — RAG and Vector Store (8)

| ID | Title | Impact | Goal |
|---|---|---|---|
| R01 | Poisoned document ingestion | 8.0 | mcp-toxic-flow |
| R02 | Vector metadata ACL bypass | 8.5 | data-exfiltration |
| R03 | Embedding collision hijack | 7.0 | mcp-toxic-flow |
| R04 | Chunk-boundary smuggling | 7.5 | prompt-driven-threat |
| R05 | Stale document retrieval | 6.5 | data-exfiltration |
| R06 | Cross-index namespace bleed | 8.0 | data-exfiltration |
| R07 | Citation laundering | 6.0 | data-exfiltration |
| R08 | Nearest-neighbor enumeration | 5.5 | recon-inference |

### S — API Schema Exploitation (8)

| ID | Title | Impact | Goal |
|---|---|---|---|
| S01 | Identity field body override | 8.0 | api-attack |
| S02 | Schema-grounded mass assignment | 7.5 | api-attack |
| S03 | Hidden response schema field exfiltration | 6.5 | data-exfiltration |
| S04 | Streaming response data exfiltration | 7.0 | data-exfiltration |
| S05 | Alternate payload-key injection | 6.0 | prompt-driven-threat |
| S06 | Session ID replay / session fixation | 7.0 | api-attack |
| S07 | Structured output field coercion | 5.5 | prompt-driven-threat |
| S08 | Schema field type-confusion recon | 4.5 | recon-inference |

### T — Destructive Tool Actions (8)

Irreversible or high-consequence tool calls executed without confirmation.

| ID | Title | Impact | Goal |
|---|---|---|---|
| T01 | Send message/email without confirmation | 7.5 | tool-abuse |
| T02 | Delete/update record | 8.0 | tool-abuse |
| T03 | Change route/destination | 7.0 | tool-abuse |
| T04 | Unsafe climate/media command | 6.5 | tool-abuse |
| T05 | Payment/transfer/order action | 8.0 | tool-abuse |
| T06 | Mass notification | 7.0 | tool-abuse |
| T07 | Disable alert/logging | 7.5 | tool-abuse |
| T08 | Create persistent scheduled task | 7.0 | tool-abuse |

### V — Supply Chain and CI/CD (7)

| ID | Title | Impact | Goal | Enabled |
|---|---|---|---|---|
| V01 | Named env var secret extraction | 8.0 | data-exfiltration | yes |
| V02 | CI/CD runtime token disclosure | 8.5 | data-exfiltration | yes |
| V03 | Cloud metadata IMDS credential theft | 9.0 | data-exfiltration | **no** |
| V04 | Known-vulnerable dependency exploit probe | 6.0 | prompt-driven-threat | yes |
| V05 | No-quality-gate dark corner probe | 4.0 | recon-inference | yes |
| V06 | Model artifact integrity discrepancy | 5.0 | recon-inference | yes |
| V07 | Staging credential reuse on production endpoint | 8.0 | privilege-escalation | yes |

> `V03` ships disabled by default because IMDS credential theft probes can trigger real cloud-provider security alerting even in a sandbox. Enable it explicitly via a customized `--catalog` file if your environment can tolerate that.

---

## Safe execution modes

Every scenario declares how it's run against a live target without causing real damage:

| Mode | Behavior |
|---|---|
| `canary_only` | Only probes for seeded canary values (see [canary setup](redteam-design.md#canaries-quick-setup)) — never targets real user data |
| `synthetic_tenant` | Uses a dedicated synthetic tenant/account created for the scan, not a real customer |
| `trap_endpoint` | Points destructive actions at a sandboxed trap endpoint that records the call without executing it |
| `dry_run_tool` | Invokes the tool's dry-run/preview mode where the app supports one |
| `trace_only` | Observes agent reasoning/tool-selection traces without requiring the action to actually execute |
| `emulated_tool` | Uses a stubbed tool response to observe how the agent would react |
| `sandbox` | Runs inside an isolated execution sandbox (coding-agent scenarios) |

---

## Customizing the catalog

Export the full catalog to inspect or edit it:

```bash
nuguard redteam catalog-export --output my-catalog.yaml
```

Common edits:

- Set `enabled: false` on a scenario to skip it entirely (e.g. disable `T05` payment-action probes if your sandbox has no test payment gateway).
- Lower `base_impact` below a profile's threshold to exclude a scenario from fast (`ci`) scans without disabling it globally.
- Edit `expected_control` / `success_signal` to match your application's specific behavior.

Pass the edited file back in:

```bash
nuguard redteam --sbom app.sbom.json --target http://localhost:8000 \
  --catalog ./my-catalog.yaml --profile full
```

or in `nuguard.yaml`:

```yaml
redteam:
  catalog_path: ./my-catalog.yaml
```

See [example-openai-cs-agents.md](example-openai-cs-agents.md) for a worked scan against a live demo app.
