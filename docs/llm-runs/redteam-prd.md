# NuGuard AI Red-Teaming Platform

## Product Requirements Document v1.0

**Author:** NuGuardAI Product Team
**Date:** March 18, 2026
**Status:** Approved for Implementation

---

# 1. Executive Summary

NuGuard is the **first agentic AI penetration testing platform built around the AI system itself, not just its prompts**.

Every other red-teaming tool today — Garak, Promptfoo, PyRIT — starts with a prompt and fires it at a model. NuGuard starts by reading your **AI-SBOM (via Xelo)**: a complete inventory of your application's agents, MCP tools, APIs, databases, permissions, and system prompts. From that inventory, NuGuard builds an **Attack Surface Graph**, generates **context-aware multi-step exploit chains**, executes them through a coordinated **5-agent attack system**, and validates the results against your application's **Cognitive Policy** — the Markdown specification of how your AI is supposed to behave.

The output is not a list of jailbreak results. It is an **audit-ready risk report** that maps every discovered exploit to the specific component that is vulnerable, the policy clause that was violated, and a concrete remediation path — delivered as a CI/CD gate on every pull request.

**Target user:** AI application developers who ship agentic systems and need to prove, before merge, that their agents cannot be manipulated into abusing tools, exfiltrating data, or violating their defined behavioral contracts.

**Headline claim:**
> *"We don't test prompts. We test AI systems the way attackers would exploit them."*
*"We don't just test prompts. We test every tool, every API, every database behind your AI systems the way attackers would exploit them."*

---

# 2. Problem Statement

## 2.1 The Structural Gap in AI Security Tooling

Modern AI applications are no longer chatbots. They are **agentic systems**: multi-step reasoning engines connected to real tools, databases, APIs, external services, and memory stores. A single agent session can read from a vector database, call MCP tools, write to a SQL backend, invoke third-party APIs, and send emails — all based on a sequence of LLM decisions.

None of the existing red-teaming tools understand this architecture.

| What existing tools test | What actually needs testing |
|---|---|
| Single LLM prompt → response | Multi-step agent action sequences |
| Model-level jailbreaks | Tool invocation abuse |
| Generic harmful content | Business policy violations specific to the app |
| Static prompt libraries | Context-aware, system-topology-derived attacks |
| Isolated model endpoint | Full system: agent + tools + data + permissions |

## 2.2 Market Evidence

- **Promptfoo** (acquired by OpenAI, March 2026): validates developer appetite for red-teaming tooling, but the acquisition narrows Promptfoo to OpenAI model evaluation, leaving the broader agentic ecosystem unserved.
- **Garak** (NVIDIA, ~7.3k GitHub stars): 50+ static probe types, but explicitly scoped to LLM vulnerabilities. No concept of tool chains, permission graphs, or multi-step attacks.
- **PyRIT** (Microsoft Azure, ~3.6k GitHub stars): flexible Python SDK for security researchers, but requires significant manual orchestration, has no SBOM integration, and is not optimized for developer CI/CD workflows.
- **AI governance regulations** (EU AI Act, NIST AI RMF, ISO 42001): explicitly require systematic pre-deployment testing of AI systems, creating compliance demand for structured, audit-ready red-teaming output.

## 2.3 The Job-to-Be-Done

> *"I built an AI agent. Does it behave the way I specified?* 
> *Can an attacker manipulate it into abusing its own tools, exfiltrating its database, or bypassing the human-in-the-loop controls I defined? Tell me before I ship — and block the PR if it fails."*

No existing tool fulfills this job. NuGuard does.

---

# 3. Target Personas

## 3.1 Primary Persona: "Builder Alex" — AI Application Developer

**Who they are:**
- AI/ML engineer or full-stack developer at a product company
- Builds and ships AI features on a weekly cadence
- Stack: LangChain / LlamaIndex / custom agents, MCP tools, PostgreSQL, vector store (e.g., Pinecone / pgvector), REST APIs
- Accountable for the behavior of the agents they deploy

**Their current reality:**
- Writes unit tests for business logic but has no systematic way to test agent behavior under adversarial conditions
- Relies on manual QA and post-deployment incident response for AI failure modes
- Increasingly asked by security/compliance teams to prove their agents are safe before shipping

**Their job-to-be-done:**
- Run a red-team redteam-testas part of their normal PR workflow — the same way they run `pytest` or `eslint`
- Get a clear pass/fail signal: did the agent violate anything? If yes, what specifically broke and how do I fix it?
- Not be blocked by complex tool setup, security expertise requirements, or expensive manual processes

**Trigger:** Opens a PR. CI runs. `nuguard scan` executes. Red line blocks merge if critical violations found.

**Success state:** Red-team redteam-testcompletes in under 10 minutes, produces a SARIF-compatible report surfaced in the PR, and gives specific remediation advice with code-level or configuration-level fix suggestions.

## 3.2 Secondary Persona: "Platform Priya" — AI Platform Engineer (v1.5+)

- Governs all AI applications across an organization
- Owns the AI-SBOM registry and cognitive policy library
- Needs portfolio-level risk visibility: which apps are compliant, which have open critical findings, trend over time
- Unlocked in v1.5 with the web dashboard and organization-level policy management

---

# 4. Competitive Landscape

## 4.1 Competitor Profiles

### Garak (NVIDIA)
- **What it is:** LLM vulnerability scanner with a library of 50+ static probes (DAN jailbreaks, encoding injection, toxicity, hallucination, etc.)
- **Strength:** Large probe library, active community, CI-friendly CLI, covers a broad range of model-level failure modes
- **Weakness:** Tests the model in isolation — no concept of tool chains, APIs, databases, permissions, or multi-step agent behavior. Static probe library is not context-aware. No policy validation.
- **TL;DR:** Tests whether a model can be made to say bad things. Does not test whether an agent can be made to do bad things.

### Promptfoo (now OpenAI)
- **What it is:** Developer-first AI testing framework with red-teaming capabilities, strong eval pipeline, growing agent plugin support (BOLA/BFLA plugins)
- **Strength:** Excellent developer UX, CI/CD native, strong community (17.3k stars), coverage of application-layer threats (indirect injection, PII leaks, access control)
- **Weakness:** Fundamentally a prompt→response evaluation framework. No SBOM ingestion, no attack surface graph, no cognitive policy validation, no sandbox execution of real actions. Post-OpenAI acquisition, scope is narrowing to OpenAI model evaluation.
- **TL;DR:** Best-in-class for prompt-level and eval-based testing. Does not model the system topology or validate behavioral contracts.

### PyRIT (Microsoft Azure)
- **What it is:** Python SDK for AI red-teaming, designed for security professionals and researchers
- **Strength:** Highly flexible, supports multi-turn conversations, extensible attack orchestration, Microsoft-backed
- **Weakness:** Requires significant Python expertise and manual orchestration to set up meaningful test campaigns. Not optimized for developer CI/CD workflows. No SBOM integration, no policy engine, no system-level attack surface awareness.
- **TL;DR:** Powerful but high-friction tool for security researchers, not an out-of-the-box developer solution.

### DeepTeam (Confident AI)
- **What it is:** Open-source Python SDK (~1.4k GitHub stars, Apache 2.0) backed by a commercial SaaS platform (Confident AI). Built on DeepEval, their LLM evaluation framework. Offers 50+ LLM-as-a-Judge vulnerabilities across 7 risk categories (Data Privacy, Responsible AI, Security, Safety, Business, **Agentic**, Custom), 20+ adversarial attack methods (single-turn and multi-turn conversational), and 7 production guardrails. The commercial Confident AI platform adds a GUI dashboard for risk assessment management, production vulnerability monitoring, team report sharing, and an MCP server for running red teams directly from Cursor or Claude Code.
- **Strength:** Widest vulnerability breadth of any open-source tool. Genuinely low entry barrier (`pip install deepteam`, define a callback). Strong multi-turn conversational attack support. Commercial SaaS provides meaningful GUI and production monitoring. Covers OWASP Top 10 for LLMs 2025, **OWASP Top 10 for AI Agents 2026 (OWASP ASI)**, NIST AI RMF, and MITRE ATLAS out of the box.
- **Weakness:** Fundamentally **callback-centric**: you wrap your entire LLM system in a single Python function. DeepTeam has no concept of the system's topology — it cannot see agents, tools, APIs, databases, permissions, or MCP tool graphs. The "Agentic" vulnerability category tests LLMs that happen to be *in* agents, not the full multi-component agent system. Multi-turn attacks simulate conversation rounds, not cross-component exploit chains (prompt → tool call → DB read → exfil). No AI-SBOM integration, no behavioral/cognitive policy validation, no graph-derived scenario generation, no sandbox execution of real tool calls or database writes. SARIF output is not supported; results are JSON/DataFrame, limiting native PR and IDE integration.
- **Commercial SaaS (Confident AI):** GUI dashboard for viewing and comparing risk assessments, production monitoring with alerting, team report distribution, and IDE-native red teaming via MCP server. Despite the polished SaaS layer, the underlying data model remains callback-scoped — the platform visualizes *what the LLM said* in response to attack probes, not *what agent actions were executed across the system*.
- **TL;DR:** The most feature-rich LLM vulnerability testing framework available. Excellent for conversational, multi-turn, and LLM-layer testing with a growing GUI SaaS layer. Does not model the system — no SBOM, no topology graph, no cognitive policy, no cross-system exploit chain execution.

### Lakera Guard / Rebuff / Calypso AI
- **Runtime guardrails** (detection, not red-teaming): complement NuGuard but do not overlap with the pre-deployment attack simulation use case.

## 4.2 Capability Matrix

| Capability | Garak | Promptfoo | PyRIT | DeepTeam | **NuGuard** |
|---|:---:|:---:|:---:|:---:|:---:|
| AI-SBOM ingestion | ❌ | ❌ | ❌ | ❌ | ✅ |
| Attack surface graph from system topology | ❌ | ❌ | ❌ | ❌ | ✅ |
| Cognitive / behavioral policy validation | ❌ | ❌ | ❌ | ❌ | ✅ |
| Multi-step cross-system exploit chains | ❌ | ⚠️ limited | ⚠️ limited | ❌ | ✅ |
| 5-agent coordinated attack system | ❌ | ❌ | ⚠️ partial | ❌ | ✅ |
| Tool / API / DB exploitation (real execution) | ❌ | ⚠️ limited | ⚠️ limited | ❌ | ✅ |
| Sandbox real-action execution | ❌ | ❌ | ❌ | ❌ | ✅ |
| Graph topology-derived scenario generation | ❌ | ⚠️ partial | ❌ | ❌ | ✅ |
| Multi-turn / conversational attacks | ❌ | ⚠️ partial | ✅ | ✅ | ✅ |
| Vulnerability library (50+ types) | ✅ | ⚠️ partial | ⚠️ partial | ✅ | ✅ |
| OWASP Top 10 for AI Agents 2026 mapping | ❌ | ❌ | ❌ | ✅ | ✅ |
| CI/CD native (single command) | ✅ | ✅ | ❌ | ✅ | ✅ |
| Developer-first UX | ⚠️ | ✅ | ❌ | ✅ | ✅ |
| Commercial SaaS GUI / dashboard | ❌ | ⚠️ | ❌ | ✅ | ✅ (v1.5) |
| Production monitoring | ❌ | ⚠️ | ❌ | ✅ | ✅ (v3) |
| Risk score + compliance mapping | ⚠️ | ⚠️ | ❌ | ✅ | ✅ |
| SARIF output for IDE / PR integration | ❌ | ⚠️ | ❌ | ❌ | ✅ |
| Replayable signed audit trace | ❌ | ❌ | ❌ | ❌ | ✅ |

---

# 5. Product Strategy: The Triple Moat

NuGuard's differentiation is not a single feature — it is three compounding capabilities that no competitor has combined:

```
Moat 1: AI-SBOM Awareness      →  We understand WHAT your system can do
         ↓
Moat 2: Cognitive Policy Engine →  We know HOW it's supposed to behave
         ↓
Moat 3: Multi-Step Agentic Chains → We simulate HOW attackers would exploit the gap
```

Each moat individually is an improvement over existing tools. Together they form a category-defining product.

## 5.1 Differentiation vs DeepTeam

DeepTeam (Confident AI) is the most relevant near-term competitor: it targets the same developer persona, ships a commercial SaaS GUI, and explicitly covers "Agentic" vulnerabilities. It is the benchmark NuGuard must clearly outposition.

**The core architectural difference: DeepTeam tests what the LLM says. NuGuard tests what the AI system does.**

| Dimension | DeepTeam | NuGuard |
|---|---|---|
| **Unit of analysis** | LLM output (via `model_callback`) | Full system: agents + tools + APIs + data + permissions |
| **Attack surface source** | Vulnerability category templates + LLM generation | Xelo AI-SBOM → enriched Attack Surface Graph |
| **"Agentic" coverage** | Tests LLMs that happen to be in agents | Tests tool invocations, privilege chains, and cross-component exploit paths |
| **Multi-turn vs multi-step** | Simulates conversation turns (user says X, agent says Y) | Executes cross-system step chains (inject → invoke tool → read DB → exfil) |
| **Policy validation** | None — no concept of allowed topics, restricted actions, or HITL controls | Cognitive Policy engine validates behavioral contracts with 5 violation types |
| **Sandbox execution** | None — callback captures LLM text response only | Black-box attack client targeting the real running application (local / QA / staging); canary-based exfiltration detection |
| **Scenario derivation** | LLM-generated templates from vulnerability types | Graph traversal of actual system topology — unique per application |
| **Audit evidence** | JSON / DataFrame output | Signed JSONL trace, replayable, hashable for compliance packages |
| **IDE / PR integration** | No SARIF; JSON output | SARIF for GitHub Security tab, PR diff inline, exit codes for CI gate |

**The practical gap:** A DeepTeam redteam-testof an agent with an MCP tool connected to a SQL database will test whether the LLM's *text response* to injected prompts looks dangerous. It will not attempt to execute a `search_db` tool call with an injected SQL string and verify whether the database returned unauthorized records. NuGuard does. That gap is the market.

---

# 6. Strategic Pillar 1: Xelo AI-SBOM Integration

## 6.1 Why SBOM-First

Traditional red-teaming asks: *"What bad things can we make this model say?"*
NuGuard asks: *"Given everything this AI system can access and do, what is the most damaging thing an attacker could make it do?"*

The answer to that question requires knowing the system. Xelo's AI-SBOM provides that knowledge.

## 6.2 Integration Model

NuGuard integrates with [Xelo](https://github.com/xelo) as a **reference integration**: Xelo is the authoritative schema and tooling for generating the AI-SBOM. NuGuard consumes Xelo SBOM output and applies its own **mapping rules** to convert it into a NuGuard Attack Surface Graph.

This separation of concerns means:
- NuGuard does not re-spec the SBOM schema — it tracks Xelo's evolving standard
- NuGuard owns the security-relevant mapping layer
- Xelo SBOM improvements automatically improve NuGuard's attack surface coverage

## 6.3 NuGuard Mapping Rules

Mapping rules are defined in `mapping/xelo-mapping.md` and `mapping/mapping-rules.json`. They convert Xelo SBOM components into Attack Surface Graph nodes (agents, tools, APIs, api_endpoints, databases, vectorstores, prompts) and typed edges (INVOKES, READS, WRITES, CALLS, CALLS_ENDPOINT, EXECUTES, OWNS). Each edge type carries a specific attack implication — for example, `READS` edges identify data exfiltration paths, and `OWNS` edges identify memory poisoning surfaces.

The full node/edge type specification and field mapping is in `mapping/xelo-mapping.md` and the implementation plan.

## 6.4 API Endpoint Attack Surface (from Xelo Discovery)

When Xelo discovers API endpoints for a component, NuGuard expands each endpoint into its own attack surface node. This gives the attack graph **endpoint-level resolution** rather than treating an entire API as a single node.

Each discovered `api_endpoint` node carries:

| Attribute | Source | Attack Relevance |
|---|---|---|
| `method` | Xelo (e.g. `GET`, `POST`, `DELETE`) | `DELETE`/`POST` endpoints are higher blast-radius targets |
| `path` | Xelo (e.g. `/users/{id}/records`) | Path parameters signal injection points |
| `path_parameters` | Xelo (e.g. `[id, tenant_id]`) | Parameter tampering, IDOR / BOLA surface |
| `query_parameters` | Xelo | Query injection, filter bypass |
| `request_body_schema` | Xelo (JSON Schema fragment) | Input validation bypass, mass assignment |
| `auth_required` | Xelo (`none` / `bearer` / `api_key` / `oauth2`) | Unauthenticated access if `none` |
| `auth_scope` | Xelo (e.g. `read:records`, `admin`) | Scope escalation surface |
| `returns_sensitive_data` | Xelo (tagged in SBOM) | Data exfiltration priority |
| `accepts_user_input` | Xelo | LLM-to-API injection vector |
| `rate_limited` | Xelo | Rate limit bypass / unbounded consumption |

### Endpoint-Level Scenario Generation

The Scenario Generator uses endpoint attributes to derive targeted attack scenarios:

- **IDOR / BOLA probes**: endpoints with user- or tenant-scoped path parameters are probed with out-of-scope values injected via the agent
- **Auth bypass probes**: endpoints without authentication or with broad permission scopes are targeted first
- **Parameter injection probes**: endpoints accepting user input are targeted with SQL injection, SSRF, and template injection payloads propagated through the agent's message
- **Sensitive data exfiltration probes**: endpoints tagged as returning sensitive data are prioritised targets
- **Mass assignment probes**: endpoints accepting request bodies are probed for privilege escalation via over-posting

## 6.5 Attack Surface Graph Enrichment

After mapping, the graph is enriched with risk attributes:

- **SSRF-possible**: node calls external URLs from tool parameters
- **SQL-injectable**: tool constructs SQL queries from agent-provided input, or `api_endpoint` passes user input to a DB-connected backend
- **PII-stores**: database, vectorstore, or `api_endpoint` tagged `returns_sensitive_data: true`
- **no-auth-required**: API or tool invocable without authentication check; `api_endpoint` with `auth_required: none`
- **IDOR-surface**: `api_endpoint` with user- or tenant-scoped path parameters (`{user_id}`, `{tenant_id}`)
- **high-privilege**: node has access to administrative or cross-tenant resources; `api_endpoint` with `auth_scope: admin`
- **no-rate-limit**: `api_endpoint` with `rate_limited: false` — unbounded consumption risk
- **HITL-bypass-risk**: agent has direct tool invocation paths that skip defined human-in-the-loop controls

This enrichment drives the Scenario Generator to prioritize the highest-value attack paths.

## 6.6 Upload

```bash
nuguard sbom upload --file ./my-app.sbom.json --project my-agent-app
```

Validation is performed against `schema/attack-graph.schema.json`. Upload returns a `graph_id` used in subsequent redteam-testcommands.

---

# 7. Strategic Pillar 2: Cognitive Policy Engine

## 7.1 The Behavioral Contract Problem

Security vulnerabilities are necessary but not sufficient. A regulated AI application also has a **behavioral contract**: what topics it is allowed to discuss, what actions it is permitted to take, when it must escalate to a human, and how sensitive data must be handled.

These contracts are increasingly mandated (EU AI Act Article 13: transparency obligations; NIST AI RMF: Govern 1.1: organizational policies for AI use).

No existing red-teaming tool validates behavioral contracts. NuGuard does.

## 7.2 Cognitive Policy Format

Cognitive Policies are **Structured Markdown files** authored by the AI application developer. The format is human-readable, version-controllable, and parseable by NuGuard's policy engine.

**Canonical structure:**

```markdown
# Cognitive Policy: [Application Name]
version: 1.0
effective_date: 2026-03-01

## allowed_topics
- Customer support for [product domain]
- FAQ responses within [knowledge base scope]
- Order status and account inquiries

## restricted_topics          # hard deny — always a violation regardless of context
- Financial or investment advice
- Account deletion or data removal requests

## restricted_actions
- Do not execute financial transactions without explicit user confirmation
- Do not share data across tenant boundaries
- Do not invoke [tool_name] on unauthenticated requests
- Do not access records outside the authenticated user's scope

## HITL_triggers
- Any action with financial impact > $500
- All contract generation or legal document creation
- Password reset or account deletion requests
- Any tool invocation flagged as irreversible

## data_classification
- PII fields: [name, email, phone, address] — never echoed in response
- Internal fields: [user_id, tenant_id] — never exposed to end user
- Public fields: [product_name, price] — freely usable

## rate_limits
- Max tool invocations per session: 20
- Max database reads per session: 50
- Max external API calls per session: 10
```

## 7.3 Violation Detection

The Policy Engine evaluates every agent response trace against the loaded policy. Three violation types are implemented in v1:

| Violation Type | Trigger | Severity | Example |
|---|---|---|---|
| `topic_boundary_breach` (restricted) | Agent response contains a keyword from `restricted_topics` | High | Support agent provides financial advice |
| `topic_boundary_breach` (out-of-scope) | Agent response has no overlap with `allowed_topics` (and no `restricted_topics` match) | Medium | Support agent answers a general coding question |
| `restricted_action_executed` | Agent response confirms an action listed in `restricted_actions` | High–Critical | Agent confirms it deleted a user record |
| `HITL_bypassed` | Agent completes a `HITL_trigger` action without a human approval step in the trace | High | Agent generates a contract without escalation |

**`allowed_topics` vs `restricted_topics`:** These are distinct mechanisms. `allowed_topics` defines the soft boundary — off-topic responses are a behavioral drift. `restricted_topics` defines hard denies — topics that are absolutely forbidden regardless of context (e.g. financial advice, account deletion). `restricted_topics` is optional; when absent, only the `allowed_topics` drift check applies.

Each violation is reported with: the policy clause violated, the attack message that triggered it, the agent's response, the evidence trace, and a remediation recommendation.

## 7.4 Application Log Correlation (Optional — v1.5)

NuGuard's attack trace records what messages were sent and what the agent responded. Application Log Correlation adds a second evidence source: **the application's own runtime logs**. Correlating these two streams confirms whether an attack actually reached the system, whether the app's guardrails fired, and whether a tool invocation executed silently without defensive response.

When enabled, each finding is upgraded from *suspected* to one of: `confirmed`, `blocked_by_app`, or `silent_success` — reducing false positives and escalating the most dangerous findings.

```bash
nuguard redteam-test --sbom ./app.sbom.json --policy ./policy.md \
  --target http://localhost:3000 \
  --log-source ./app.log
```

Supported log sources include local JSONL files, OpenTelemetry exports, and major cloud logging platforms. Log correlation is **additive**: findings exist independently of it; logs only adjust confidence levels.

> **v1.5 feature.** Not built in v1. The `POST /v1/redteam/{id}/logs` endpoint returns `501` in v1 as a stub.

## 7.5 Upload

```bash
nuguard policy upload --file ./cognitive-policy.md --project my-agent-app
```

---

# 8. Strategic Pillar 3: Multi-Step Agentic Exploit Chains

## 8.1 Why Multi-Step Matters

Real attackers don't fire a single prompt. They probe, pivot, and chain actions. A prompt injection that has no immediate harmful output may still be the first step in a chain that ends in database exfiltration.

NuGuard models this. Exploit chains are **Directed Acyclic Graphs (DAGs)** of attack steps where each step's output feeds the next step's input:

```
Prompt Injection (INJECT)
        ↓
  Tool Invocation (INVOKE)
        ↓
  Unauthorized DB Read (READ)
        ↓
  Data Exfiltration to External URL (EXFIL)
```

Each chain is stored in the exploit chain schema (`schema/exploit-chain.schema.json`) with per-step `action`, `target`, `result`, and an aggregate `risk_score`.

## 8.2 Chain-of-Custody

Every step in every chain produces a signed, deterministic trace entry. The full trace:
- Can be replayed exactly for developer debugging
- Provides audit evidence for compliance (NIST AI RMF ME-2.3)
- Is hashed to prevent tampering

---

# 9. Feature: Scenario Generator

## 9.1 Context-Aware Scenario Generation

Scenarios are not generic templates. They are derived from the **topology of your specific Attack Surface Graph**. The generator walks the enriched graph and identifies paths that warrant adversarial testing.

Three scenario types are generated:

**1. Prompt Injection Paths**
- Identifies agent nodes with `prompt_accessible: true`
- Generates injection payloads targeting the specific context of those agents (system prompt content, tool descriptions, memory buffers)
- Prioritizes agents with high-value downstream tool connections

**2. Tool Abuse Paths**
- Identifies tool nodes with `no-auth-required` or `high-privilege` attributes
- Generates scenarios that attempt to invoke tools outside their permitted scope or with attacker-controlled parameters
- Covers MCP tool misuse, parameter injection, scope escalation

**3. Privilege Escalation Chains**
- Identifies paths from low-privilege entry points to high-privilege resources
- Constructs multi-step chains that traverse INVOKES→READS→WRITES edges
- Flags paths that bypass HITL controls defined in the Cognitive Policy

## 9.2 Pre-Scoring

Each generated scenario is scored by estimated impact (blast radius × privilege level of target × data sensitivity) before execution, allowing developers to prioritize or filter:

```bash
nuguard redteam-test--sbom ./app.sbom.json --policy ./policy.md \
  --scenarios tool-abuse,privilege-escalation \
  --min-impact-score 7.0
```

---

# 10. Feature: Attack Engine

## 10.1 Attack Taxonomy

### Tier 1: Prompt-Level Attacks

| Attack Type | Description | OWASP LLM Ref |
|---|---|---|
| Direct jailbreaking | DAN variants, role-playing exploits, persona hijacking | LLM01 |
| Indirect prompt injection | Injections delivered via tool outputs, database content, retrieved documents | LLM01 |
| Encoding bypass | Base64, Unicode homoglyph, MIME encoding | LLM01 |
| System prompt extraction | Attacks designed to leak the system prompt | LLM07 |
| Context window poisoning | Filling context with adversarial content to shift behavior | LLM01 |

### Tier 2: Agent-Level Attacks

| Attack Type | Description | OWASP LLM Ref |
|---|---|---|
| Goal hijacking | Override the agent's stated objective with attacker goals | LLM06 |
| Context poisoning | Introduce false premises that persist across the session | LLM01 |
| Memory injection | Write adversarial content into persistent agent memory | LLM08 |
| Cross-session contamination | Persist injected behavior across session boundaries | LLM08 |
| Tool call forgery | Craft inputs that cause the agent to generate unauthorized tool calls | LLM06 |

### Tier 3: System-Level Attacks

| Attack Type | Description | OWASP LLM Ref |
|---|---|---|
| Tool parameter injection | Inject attacker-controlled values into tool call parameters | LLM04 |
| SSRF via tool | Exploit tool URL parameters to reach internal services | LLM04 |
| SQL injection via agent | Use agent as a vector to inject SQL through DB-connected tools | LLM04 |
| API authentication bypass | Exploit misconfigured auth on tool-connected APIs | LLM04 |
| Data exfiltration (overt) | Extract structured data via allowed-but-abused tool calls | LLM02, LLM06 |
| Data exfiltration (covert) | Use encoding or steganography to embed data in model outputs | LLM02 |
| Privilege escalation | Chain low-privilege tool calls to reach high-privilege resources | LLM06 |

## 10.2 Attack Corpus

Attack payloads are seeded from:
- **OWASP LLM Top 10** (2025 edition)
- **MITRE ATLAS** adversarial ML attack library
- **NuGuard proprietary research** (agentic-specific exploit patterns)

## 10.3 Adaptive Mutation (v2)

When an attack partially succeeds, the engine generates mutated variants that attempt to complete the chain — paraphrasing payloads, trying alternative encodings, or taking alternate graph paths. Deferred to v2; v1 executes each scenario once with template or LLM-generated payloads.

---

# 11. Feature: Agentic Attack System

## 11.1 Architecture

**v1 — Sequential Attack Executor:** Five logical attack phases run in order, sharing in-process state:

```
AttackExecutor (sequential pipeline)
  Phase 1: Recon        → rank targets from attack graph (pure graph analysis)
  Phase 2: Injection    → send adversarial messages to the agent endpoint
           Tool Abuse   → craft messages that induce unauthorized tool calls
  Phase 3: Exfiltration → extract canary data via established footholds (if any)
  Phase 4: Persistence  → memory poisoning across sessions (disabled in ci profile)
```

Each phase is a dedicated agent class running an **Observe → Decide → Execute → Evaluate → Update** loop. Execute always means sending a message to the running application's agent endpoint — NuGuard never calls tools or databases directly.

**v2 — Multi-Agent Swarm:** Phases become independent agents with Redis-backed shared state, enabling true parallel execution and real-time coordination. The agent class interfaces are designed in v1 to make this a one-file change per agent.

## 11.2 Agent Roles

| Agent | Role |
|---|---|
| **ReconAgent** | Ranks attack graph nodes by value (privilege × reachability × data sensitivity); identifies weak entry points |
| **InjectionAgent** | Sends adversarial messages designed to inject instructions into the agent's context or memory |
| **ToolAbuseAgent** | Sends messages that attempt to induce the agent to invoke tools with unauthorized parameters or beyond their permitted scope |
| **ExfiltrationAgent** | Activates on established footholds; sends messages designed to extract canary data from connected stores via the agent as proxy |
| **PersistenceAgent** | Sends memory-poisoning messages; verifies the adversarial instruction persists across a new session. Disabled in `ci` profile |

## 11.3 Orchestration

The `AttackOrchestrator` manages phase sequencing and exploit chain assembly:

1. ReconAgent always runs first; outputs a prioritized target list
2. InjectionAgent and ToolAbuseAgent run against their respective target sets
3. ExfiltrationAgent activates only when partial successes exist
4. PersistenceAgent activates last; skipped in `ci` profile
5. All phases subject to a configurable scan timeout (default 10 minutes for `ci` profile)

---

# 12. Feature: Target Application Execution Layer

## 12.1 Attack the Real Application

NuGuard does not mock your application. It attacks the **real application running in your dev, QA, or staging environment** by sending adversarial messages to its agent endpoint and evaluating the responses.

```
NuGuard AttackExecutor
        ↓  adversarial HTTP messages
Running AI Application  (localhost | QA | staging)
        ↓  agent invokes real tools, queries real DB, calls real APIs
Response captured → evaluated against Cognitive Policy
```

All attacks go through the agent endpoint — NuGuard exploits the agent as the attacker would. No Docker management. No mock servers. The developer runs their app in their normal environment; NuGuard is a black-box adversarial HTTP client.

```bash
# Point NuGuard at your running app
nuguard redteam-test \
  --sbom ./app.sbom.json \
  --policy ./policy.md \
  --target http://localhost:3000    # or https://qa.yourapp.com
```

| Environment | Flag |
|---|---|
| Local dev | `--target http://localhost:PORT` |
| QA / staging | `--target https://qa.yourapp.com` |
| CI pipeline | `--target $APP_URL` (app started by CI job before NuGuard runs) |

## 12.2 Canary Data and Exfiltration Detection

For data exfiltration testing, developers seed **canary records** into the application's test database before the scan. Canary records contain unique values that should never appear in agent responses.

```bash
# Seed canary data using your own e2e fixtures, or via nuguard seed:
nuguard seed --target http://localhost:3000 --seed-file ./canary.json

# Run the scan with canary detection enabled:
nuguard redteam-test --sbom ./app.sbom.json --policy ./policy.md \
  --target http://localhost:3000 \
  --canary ./canary.json     # { "watch_values": ["Canary_NuGuard_abc123", "987-65-4321"] }
```

If any canary value appears in an agent response, exfiltration is confirmed as a finding. NuGuard never writes to or deletes from the target database — canary lifecycle is owned by the developer's test fixtures.

## 12.3 Session Isolation and Replayability

- **Session isolation:** each attack scenario runs in a fresh agent session — no state bleeds between scenarios
- **Deterministic trace:** every message sent and response received is recorded in signed JSONL
- **Replay:** any completed scan can be re-executed against the same target: `nuguard replay --scan-id <id> --target <url>`

---

# 13. Feature: Observability and Trace Layer

## 13.1 What Is Captured

For every scan, NuGuard captures a complete structured trace:

| Event Type | Captured Fields |
|---|---|
| Agent decision | agent_id, timestamp, state_snapshot, action_chosen, reasoning |
| Tool call (attempted) | tool_name, parameters, auth_context, permitted (bool), outcome |
| Tool call (executed) | tool_name, parameters_sent, response_received, latency_ms |
| LLM turn | input_tokens, output_tokens, model_id, response_text, policy_flags_triggered |
| Policy check | rule_evaluated, outcome (pass/violation), violation_type, evidence |
| Chain progress | chain_id, step_number, action, target, result, chain_risk_score |
| **Log correlation (optional)** | log_source, matched_entry, correlation_status, guardrail_fired, error_signal |

## 13.2 Trace Output

Traces are written as **structured JSONL** (`scan-{id}.trace.jsonl`) and:
- Stored in Postgres (`exploit_chains` table, `chain JSONB` column)
- Signed with SHA-256 hash for compliance evidence packages
- Immutable after redteam-testcompletion (append-only write model)
- Accessible via API: `GET /v1/redteam/{id}/traces`
- Renderable as a swimlane timeline in the developer report

---

# 14. Feature: Risk and Evidence Engine

## 14.1 Per-Finding Severity

| Severity | Definition | Example |
|---|---|---|
| **Critical** | Full system compromise, cross-tenant breach, or complete policy bypass | DB exfiltration via unchecked tool call |
| **High** | Significant data exposure, tool scope escalation, HITL bypass | PII returned to unauthorized caller |
| **Medium** | Partial success, behavioral drift, restricted topic discussed | Agent answered out-of-scope medical question |
| **Low** | Minor policy deviation, recoverable via prompt engineering | Agent slightly overshared public information |
| **Info** | Probed but no successful exploit; attack surface note | High-privilege API reachable but auth holds |

## 14.2 Risk Score Formula

**v1 — severity-based:**
```
risk_score = Σ severity_weight(finding) / n_findings   [0.0–10.0]

severity_weight: Critical=10, High=7, Medium=4, Low=1, Info=0
```

A simple, auditable aggregate derived from finding severity distribution.

**v2 — weighted by exploit quality:**

`exploitability` (how reliably the exploit succeeds) and `blast_radius` (scope of impact) are added as multipliers when real execution data provides meaningful signal. In v1, these would be constant estimates and add noise rather than insight.

**Inherent risk:** score before any mitigations applied
**Residual risk (v2):** score after simulated mitigations applied

## 14.3 Compliance Mapping

Every finding is tagged with applicable compliance framework references:

| Framework | Coverage |
|---|---|
| **OWASP LLM Top 10** (2025) | LLM01–LLM10 per finding |
| **NIST AI RMF** | Govern / Map / Measure / Manage sub-categories |
| **EU AI Act** | Art. 10 (data governance), Art. 13 (transparency), Art. 15 (robustness) |
| **ISO 42001** | AI management system control references |

Full NIST AI RMF and EU AI Act mapping is in scope for v2. OWASP LLM Top 10 is fully covered in v1.

## 14.4 Scan-to-redteam-testDrift Detection (v1.5)

When a project has multiple historical scans, the Risk Engine tracks risk score trend (improving / degrading / stable), flags regressions (new findings not present in the prior scan), and reports resolved findings.

---

# 15. Developer Experience

## 15.1 CLI (Primary Interface, v1)

The `nuguard` CLI is designed to feel familiar to developers who use `pytest`, `eslint`, or `snyk`: one command, clear output, CI-compatible exit codes.

**Core commands:**

```bash
# Full scan: run all attacks against the running application
nuguard redteam-test \
  --sbom ./my-app.sbom.json \
  --policy ./cognitive-policy.md \
  --target http://localhost:3000 \    # URL of the running AI application
  --profile ci                       # fast profile: disables PersistenceAgent

# With canary-based exfiltration detection
nuguard seed --target http://localhost:3000 --seed-file ./canary.json
nuguard redteam-test \
  --sbom ./my-app.sbom.json \
  --policy ./cognitive-policy.md \
  --target http://localhost:3000 \
  --canary ./canary.json

# Filter to specific scenario types and minimum impact threshold
nuguard redteam-test \
  --sbom ./my-app.sbom.json \
  --policy ./cognitive-policy.md \
  --target http://localhost:3000 \
  --scenarios tool-abuse,prompt-injection \
  --min-impact-score 6.0

# Get report in different formats
nuguard report --scan-id <scan_id> --format markdown
nuguard report --scan-id <scan_id> --format sarif
nuguard report --scan-id <scan_id> --format json

# Upload SBOM or policy separately (pre-register)
nuguard sbom upload --file ./my-app.sbom.json --project my-agent-app
nuguard policy upload --file ./cognitive-policy.md --project my-agent-app

# List findings filtered by severity
nuguard findings --scan-id <scan_id> --severity critical,high

# Replay a scan deterministically against the same target
nuguard replay --scan-id <scan_id> --target http://localhost:3000
```

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | redteam-testcomplete, no findings at or above threshold |
| `1` | Findings found at or above configured threshold (default: High) |
| `2` | Critical findings found (always blocks, regardless of threshold) |
| `3` | redteam-testerror (invalid SBOM, infrastructure failure) |

## 15.2 CI/CD Integration

### GitHub Actions (v1)

The calling workflow starts the AI application, then runs NuGuard against it:

```yaml
# .github/workflows/nuguard.yml
name: NuGuard Red Team Scan
on: [pull_request]
jobs:
  redteam:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Start AI application
        run: docker compose up -d && sleep 5   # developer's own app startup
      - uses: nuguardai/scan-action@v1
        with:
          sbom: ./my-app.sbom.json
          policy: ./cognitive-policy.md
          target: http://localhost:3000
          canary: ./canary.json          # optional
          project: my-agent-app
          profile: ci
          fail-on: high
          sarif-output: true
        env:
          NUGUARD_API_KEY: ${{ secrets.NUGUARD_API_KEY }}
```

### Azure DevOps (v1.5)

Azure DevOps task (`NuGuardScan@1`) is deferred to v1.5.

## 15.3 Report Formats

### Developer View (default)
- Per-finding: severity badge, exploit chain that produced it, the exact attack payload, the policy clause violated, and a concrete fix suggestion
- Fix suggestions are code-specific: *"Parameterize the SQL query in `search_tool.py:47` before passing agent-supplied values to the database cursor"*
- SARIF output: findings surfaced inline in PR diff view and IDE Problems panel

### Security View (`--format security` or dashboard, v1.5)
- Aggregate risk score with trend vs prior scan
- Attack category breakdown (prompt-level / agent-level / system-level)
- Compliance gap table (OWASP LLM Top 10, NIST AI RMF, EU AI Act)
- Replayable exploit chains for penetration test evidence packages

---

# 16. API Design

## 16.1 Endpoint Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/redteam` | Create and start a redteam-test|
| `GET` | `/v1/redteam/{scan_id}` | Get redteam-teststatus and metadata |
| `DELETE` | `/v1/redteam/{scan_id}` | Delete redteam-testand associated data |
| `POST` | `/v1/sbom` | Upload and validate an AI-SBOM |
| `GET` | `/v1/sbom/{sbom_id}` | Get a previously uploaded SBOM |
| `POST` | `/v1/policies` | Upload and validate a Cognitive Policy |
| `GET` | `/v1/policies/{policy_id}` | Get a previously uploaded policy |
| `GET` | `/v1/redteam/{scan_id}/results` | Full structured findings |
| `GET` | `/v1/redteam/{scan_id}/report` | Formatted report (`?format=sarif\|markdown\|json`) |
| `GET` | `/v1/redteam/{scan_id}/traces` | Raw execution traces (JSONL) |
| `GET` | `/v1/redteam/{scan_id}/chains` | Exploit chains discovered |
| `POST` | `/v1/redteam/{scan_id}/logs` | Upload application logs for correlation (optional) |
| `GET` | `/v1/redteam/{scan_id}/log-correlations` | Per-step log correlation results |
| `GET` | `/v1/projects/{project_id}/history` | redteam-testhistory and risk drift for a project |

## 16.2 POST /v1/redteam — Request

```json
{
  "project": "my-agent-app",
  "sbom_id": "sbom_abc123",
  "policy_id": "policy_xyz789",
  "config": {
    "target_url": "http://localhost:3000",
    "profile": "ci",
    "scenarios": ["prompt-injection", "tool-abuse", "privilege-escalation"],
    "min_impact_score": 0.0,
    "fail_on": "high",
    "timeout_minutes": 10
  }
}
```

The `target_url` is the base URL of the running AI application. The agent endpoint path is read from the SBOM or defaults to `/v1/chat`.

## 16.3 GET /v1/redteam/{scan_id}/results — Response

```json
{
  "scan_id": "scan_def456",
  "status": "complete",
  "risk_score": 7.0,
  "summary": {
    "critical": 1,
    "high": 3,
    "medium": 5,
    "low": 2,
    "info": 4
  },
  "findings": [
    {
      "finding_id": "f_001",
      "severity": "critical",
      "title": "SQL injection via customer search tool",
      "exploit_chain_id": "chain_abc",
      "policy_violation": "restricted_action_executed",
      "policy_clause": "Do not access records outside the authenticated user's scope",
      "compliance_refs": [{ "framework": "owasp_llm_top10", "ref_id": "LLM04" }],
      "evidence_trace_hash": "sha256:a1b2c3...",
      "remediations": [
        {
          "text": "Parameterize the SQL query in search_tool.py before passing agent-supplied values to the database cursor.",
          "node_type": "database",
          "exploit_technique": "SQL-injectable"
        }
      ]
    }
  ]
}
```

---

# 17. System Architecture

## 17.1 Layer Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│  Layer 1: Ingestion                                              │
│  Xelo AI-SBOM Parser  |  Cognitive Policy Parser                │
└────────────────────────────────┬─────────────────────────────────┘
                                 ↓
┌──────────────────────────────────────────────────────────────────┐
│  Layer 2: Attack Graph Builder                                   │
│  Mapping Engine (mapping/xelo-mapping.md rules)                 │
│  networkx in-process graph | Risk Attribute Enrichment          │
└────────────────────────────────┬─────────────────────────────────┘
                                 ↓
┌──────────────────────────────────────────────────────────────────┐
│  Layer 3: Scenario Generator                                     │
│  Graph Traversal → Attack Path Discovery → Pre-Scoring          │
└────────────────────────────────┬─────────────────────────────────┘
                                 ↓
┌──────────────────────────────────────────────────────────────────┐
│  Layer 4: Attack Executor (5 sequential phases)                 │
│  Recon | Injection | Tool Abuse | Exfiltration | Persistence    │
│  In-process ScanState (v1) → Redis-backed swarm (v2)           │
└────────────────────────────────┬─────────────────────────────────┘
                                 ↓
┌──────────────────────────────────────────────────────────────────┐
│  Layer 5: Target Application Client                              │
│  TargetAppClient (httpx) → running AI app (local/QA/staging)   │
│  CanaryScanner — detects exfiltrated test data in responses     │
└────────────────────────────────┬─────────────────────────────────┘
                                 ↓
┌──────────────────────────────────────────────────────────────────┐
│  Layer 6: Trace Capture                                          │
│  Signed JSONL (SHA-256 chained) → Postgres (exploit_chains)    │
└────────────────────────────────┬─────────────────────────────────┘
                                 ↓
┌──────────────────────────────────────────────────────────────────┐
│  Layer 7: Cognitive Policy Engine                                │
│  3 v1 violation detectors (topic breach, restricted action,     │
│  HITL bypass) | Log Correlation Engine (v1.5, optional)         │
└────────────────────────────────┬─────────────────────────────────┘
                                 ↓
┌──────────────────────────────────────────────────────────────────┐
│  Layer 8: Risk Engine                                            │
│  Severity scoring | Risk score | OWASP LLM Top 10 mapping       │
│  Remediation generation | Drift detection (v1.5)                │
└────────────────────────────────┬─────────────────────────────────┘
                                 ↓
┌──────────────────────────────────────────────────────────────────┐
│  Layer 9: Output Layer                                           │
│  SARIF / Markdown / JSON reports                                │
│  REST API  |  GitHub Actions  |  CI/CD exit codes               │
└──────────────────────────────────────────────────────────────────┘
```

## 17.2 Data Stores

| Store | Technology | Purpose |
|---|---|---|
| Attack graph | networkx (in-process, v1) → Neo4j (v2) | Graph traversal, path analysis, pattern matching |
| Scan metadata | Postgres | Scan state, SBOM/policy registry, findings |
| Exploit chains + traces | Postgres (JSONB) | Signed trace storage, audit evidence |
| Agent shared state | In-process ScanState dataclass (v1) → Redis (v2) | Exploit progress, partial successes, memory store |

Data model schemas:
- `models/postgres.sql` — all v1 tables
- `schema/attack-graph.schema.json` — attack graph JSON schema
- `schema/exploit-chain.schema.json` — exploit chain JSON schema

---

# 18. MVP v1 Scope

## 18.1 In Scope for v1

| Feature | Notes |
|---|---|
| Xelo AI-SBOM ingestion | Reference integration; NuGuard mapping rules applied |
| Attack Surface Graph builder | networkx in-process; full risk attribute enrichment |
| Scenario generator | All 3 scenario types; pre-scoring; CLI-filterable |
| Sequential attack executor | 5 logical phases; in-process state; PersistenceAgent off by default in `ci` profile |
| Target app execution | Black-box HTTP client (`--target <url>`); canary-based exfiltration detection (`--canary`) |
| Cognitive Policy validation | 3 v1 violation types: `topic_boundary_breach` (2-tier), `restricted_action_executed`, `HITL_bypassed` |
| CLI — full command set | `nuguard redteam-test`, `nuguard seed`, `nuguard report`, `nuguard sbom`, `nuguard policy`, `nuguard findings`, `nuguard replay` |
| GitHub Actions integration | `nuguardai/scan-action@v1` |
| SARIF report output | PR diff inline; GitHub Security tab upload |
| Markdown report output | Human-readable developer view with fix suggestions |
| Per-finding remediation advice | Specific, actionable fix suggestions |
| OWASP LLM Top 10 mapping | All findings tagged (LLM01–LLM10) |
| Inherent risk score | Severity-weighted aggregate (0.0–10.0) |

## 18.2 Explicitly Out of Scope for v1

| Feature | Target Version |
|---|---|
| Azure DevOps CI task (`NuGuardScan@1`) | v1.5 |
| Redis-backed parallel multi-agent swarm | v1.5 |
| Application Log Correlation (`--log-source`) | v1.5 |
| Web dashboard | v1.5 |
| SDK / embeddable library | v1.5 |
| Platform Engineer persona | v1.5 |
| Scan-to-scan drift detection | v1.5 |
| `data_classification_leak` violation type | v2 (requires PII tagging pipeline) |
| `unauthorized_tool_call` violation type | v2 (requires permission graph enforcement) |
| `exploitability` + `blast_radius` risk factors | v2 (meaningful only with real execution data) |
| Adaptive RL-based attack mutation | v2 |
| Multi-modal attacks (vision, audio) | v2 |
| Full NIST AI RMF + EU AI Act compliance mapping | v2 |
| Residual risk scoring | v2 |
| Multi-tenant SaaS | v2 |
| Continuous production monitoring | v3 |
| Autonomous remediation | v3 |
| AI SOC integration | v3 |

---

# 19. Roadmap

## v1 — Agentic Red-Teaming Engine (Target: Q2 2026)

Core agentic red-teaming platform. Complete SBOM ingestion, attack graph builder (networkx), sequential 5-phase attack executor (in-process state), black-box `TargetAppClient` attacking the real running application, canary-based exfiltration detection, cognitive policy validation (3 violation types with 2-tier `topic_boundary_breach`), full CLI with `--target` and `--canary` flags, GitHub Actions integration, SARIF + Markdown reports, OWASP LLM Top 10 mapping, severity-based risk score.

**Exit criterion:** A developer can run `nuguard redteam-test --sbom app.sbom.json --policy policy.md --target http://localhost:3000` in CI and receive a blocking report with actionable findings in under 10 minutes.

## v1.5 — Platform and Governance Layer (Target: Q3 2026)

Redis-backed parallel multi-agent swarm. Azure DevOps CI task. Application Log Correlation (`--log-source`). Web dashboard for project portfolio visibility. Python SDK for embedding in test suites. Secondary persona (Platform Priya) features: org-level policy management, portfolio risk dashboard. Scan-to-scan drift detection and regression alerting. Full 5-violation policy engine (`data_classification_leak`, `unauthorized_tool_call`). Self-hosted deployment option.

## v2 — Adaptive Intelligence and Compliance (Target: Q4 2026)

Adaptive RL-based attack mutation: agents learn from partial successes and generate improved attack variants. Multi-modal attack support (vision inputs, audio transcription injection). Full compliance mapping: NIST AI RMF, EU AI Act Art. 10/13/15, ISO 42001. Residual risk scoring. Multi-tenant SaaS. Enterprise SSO + RBAC.

## v3 — Continuous and Autonomous (Target: 2027)

Continuous production monitoring: passive trace collection and anomaly detection against cognitive policy. Automated remediation suggestions with code-level PR generation. AI SOC integration (ServiceNow, PagerDuty). Industry-specific benchmark suites (financial services, healthcare, legal).

---

# 20. Success Metrics

## 20.1 Adoption

| Metric | v1 Target | v2 Target |
|---|---|---|
| Active projects (SBOM uploaded) | 50 | 500 |
| Scans per week | 200 | 5,000 |
| CI/CD integrations (GitHub / ADO) | 30 | 300 |

## 20.2 Quality

| Metric | Target |
|---|---|
| redteam-testcompletion time (ci profile, typical app) | < 10 minutes |
| Critical finding false positive rate | < 5% |
| Exploit chain reproducibility rate | 100% (deterministic trace) |
| Policy clause coverage per redteam-test| > 80% of defined clauses exercised |

## 20.3 Outcome

| Metric | Definition |
|---|---|
| Shift-left rate | % of total vulnerabilities discovered pre-production vs post |
| Mean time to remediation | Avg days from finding reported to finding resolved |
| Attack success rate reduction | % reduction in exploit success rate scan-to-redteam-testafter mitigations |
| Compliance audit pass rate | % of customers passing first AI governance audit using NuGuard evidence |

---

# 21. Key Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **False positives undermine developer trust** | High | Conservative severity defaults; `--min-impact-score` filter; false positive feedback loop to retrain detectors |
| **Attack cost scaling** | Medium | `ci` profile limits phases and caps timeout at 10 minutes; cost shown before scan starts |
| **Model unpredictability degrades replay fidelity** | Medium | Deterministic trace records all inputs/outputs; temperature=0 for attack generation; trace replay does not re-invoke model |
| **Xelo SBOM schema evolution breaks mapping** | High | Versioned mapping rules; mapping layer is independently updatable; SBOM schema version validated before processing |
| **Cognitive Policy ambiguity causes false violations** | Medium | `nuguard policy validate` lints for ambiguous clauses before scan; violations cite exact policy clause |
| **Adversarial inputs cause unintended side effects in staging** | Medium | Documentation explicitly warns against running against production; developers should use isolated QA environment |
| **Canary data not cleaned up** | Low | Canary lifecycle owned by developer test fixtures; NuGuard never writes to or deletes from target database |

---

# 22. Appendix

## A. OWASP LLM Top 10 Coverage Map

| OWASP LLM ID | Name | NuGuard Agent |
|---|---|---|
| LLM01 | Prompt Injection | InjectionAgent (direct + indirect) |
| LLM02 | Sensitive Information Disclosure | ExfiltrationAgent |
| LLM03 | Supply Chain Vulnerabilities | Scenario Generator (tool provenance paths) |
| LLM04 | Data and Model Poisoning / Tool Abuse | ToolAbuseAgent |
| LLM05 | Improper Output Handling | ReconAgent (output forwarding paths) |
| LLM06 | Excessive Agency | ToolAbuseAgent + Orchestrator |
| LLM07 | System Prompt Leakage | InjectionAgent |
| LLM08 | Vector and Embedding Weaknesses | ExfiltrationAgent (vectorstore targets) |
| LLM09 | Misinformation | InjectionAgent (context poisoning) |
| LLM10 | Unbounded Consumption | Scenario Generator (rate limit bypass paths) |

## B. Cognitive Policy Violation Types Reference

| ID | Violation Type | Trigger | Severity | Version |
|---|---|---|---|---|
| VP-01a | `topic_boundary_breach` (restricted) | Response matches `restricted_topics` keyword | High | v1 |
| VP-01b | `topic_boundary_breach` (out-of-scope) | Response has no overlap with `allowed_topics` | Medium | v1 |
| VP-02 | `restricted_action_executed` | Agent confirms an action in `restricted_actions` | High–Critical | v1 |
| VP-03 | `HITL_bypassed` | HITL trigger action completed without approval | High | v1 |
| VP-04 | `data_classification_leak` | Agent exposes PII or Internal-classified data | High | v2 |
| VP-05 | `unauthorized_tool_call` | Agent invokes a tool outside SBOM-granted scope | High | v2 |

## C. Exploit Chain and Schema Reference

Full exploit chain and attack graph JSON schemas are defined in `schema/exploit-chain.schema.json` and `schema/attack-graph.schema.json`. Example instances are in `examples/exploit-chain.json` and `examples/sample-graph.json`. Field-level specifications are in the implementation plan.

## D. Related Files in This Repository

| File | Purpose |
|---|---|
| `agents/agent-architecture.md` | Attack agent design principles and agent loop specification |
| `api/openapi.yaml` | OpenAPI specification — full v1 endpoint set |
| `architecture/execution-flow.md` | Execution flow from SBOM upload to report |
| `architecture/system-architecture.md` | Platform component architecture reference |
| `mapping/xelo-mapping.md` | Xelo SBOM → Attack Graph mapping definitions |
| `mapping/mapping-rules.json` | Machine-readable mapping rule set |
| `models/postgres.sql` | Postgres schema for all v1 tables |
| `schema/attack-graph.schema.json` | JSON Schema for the NuGuard Attack Surface Graph |
| `schema/exploit-chain.schema.json` | JSON Schema for exploit chain objects |
| `examples/exploit-chain.json` | Example exploit chain instance |
| `examples/sample-graph.json` | Example attack surface graph instance |

---

*NuGuard AI — Red-Teaming Platform PRD v1.0 — March 18, 2026*
