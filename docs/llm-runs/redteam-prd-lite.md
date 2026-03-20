# NuGuard AI Red-Teaming Platform

## Product Requirements Document v1.0

**Author:** NuGuardAI Product Team
**Date:** March 18, 2026
**Status:** Approved for Implementation

---

# 1. Executive Summary

NuGuard is the **first agentic AI behavior evaluation platform to test the business intent**.

It's easy to build an agentic AI application using coding-agents but it's hard to convience yourself, and your executives that it is safe. 

Most tools test prompts. NuGuard tests the whole system and evaluates that its behavior aligns to the business intent.

The output is not a list of jailbreak results. It is an **audit-ready risk report** that maps every intent clause that was violated, potential exploits to the specific component, and a concrete remediation path — automated within your CI/CD pipeline.

**Primary user:** AI application developers who need to prove that their agents cannot be manipulated into abusing tools, exfiltrating data, or violating their defined behavioral contracts.

**Secondary user:** Executives like CTO, and CISO who need to be convinced that their agents cannot be manipulated into abusing tools, exfiltrating data, or violating their defined behavioral contracts. They need audit-ready evidence that they can use to satisfy compliance requirements and internal risk management.

**Headline claim:**

> *"Evaluate the behavior of your AI systems before it's too late — test every tool, every API, every database behind your AI systems to ensure they behave as intended."*

> *"We don't test prompts. We test AI systems the way attackers would exploit them."*
---

# 2. Problem Statement

## 2.1 The Structural Gap in AI Security & Behavior Analysis Tooling

Modern AI applications are no longer chatbots. They are **agentic systems**: multi-step reasoning engines connected to real tools, databases, APIs, external services, and memory stores. A single agent session can read from a vector database, call MCP tools, write to a SQL backend, invoke third-party APIs, and send emails — all based on a sequence of LLM decisions.

None of the existing red-teaming tools understand this architecture.

| What existing tools test | What actually needs testing |
|---|---|
| Single LLM prompt → response | Multi-step agent action sequences |
| Model-level jailbreaks | Tool invocation abuse |
| Generic harmful content | Business policy violations specific to the app |
| Static prompt libraries | Context-aware, system-topology-derived attacks |
| Isolated model endpoint | Full system: agent + tools + data + permissions |

## 2.2 The Gap in the Eval Systems
The dominant paradigm for AI testing is the **prompt→response eval**: you send a crafted prompt to the model and evaluate the text output for signs of vulnerability. This is what Garak, Promptfoo, PyRIT, and DeepTeam do.

*“AI Eval ensures correctness. NuGuard ensures adversarial resilience.”*

*Eval: “Is answer correct?”*
*NuGuard: “Did agent violate business rules?”*

Evals are dataset-driven, not exploitation-driven. 
You define:
* inputs
* expected outputs
👉 But users and attackers don’t follow expectations.

🔥 Dimension-by-Dimension

| Capability | Eval Systems | NuGuard |
|---|---|---|
| Prompt testing | ✅ | ✅ |
| Output scoring | ✅ | ⚠️ secondary |
| Agent reasoning eval | ⚠️ partial | ✅ |
| Tool execution testing | ❌ | ✅ |
| API abuse testing | ❌ | ✅ |
| DB exfiltration | ❌ | ✅ |
| Multi-step attacks | ❌ | ✅ |
| Cognitive policy validation | ❌ | ✅ |
| Real sandbox execution | ❌ | ✅ |

## 2.2 Market Evidence

- **Promptfoo** (acquired by OpenAI, March 2026): validates developer appetite for red-teaming tooling, but the acquisition narrows Promptfoo to OpenAI model evaluation, leaving the broader agentic ecosystem unserved.
- **Garak** (NVIDIA, ~7.3k GitHub stars): 50+ static probe types, but explicitly scoped to LLM vulnerabilities. No concept of tool chains, permission graphs, or multi-step attacks.
- **PyRIT** (Microsoft Azure, ~3.6k GitHub stars): flexible Python SDK for security researchers, but requires significant manual orchestration, has no SBOM integration, and is not optimized for developer CI/CD workflows.
- **AI governance regulations** (EU AI Act, NIST AI RMF, ISO 42001): explicitly require systematic pre-deployment testing of AI systems, creating compliance demand for structured, audit-ready red-teaming output.

## 2.3 The Job-to-Be-Done

> *"I built an AI agent. Does it behave the way I specified?* 
> *Can an attacker manipulate it into abusing its own tools, exfiltrating its database, or bypassing the human-in-the-loop controls I defined? Tell me before I ship — and block the PR if it fails."*

No existing tool fulfills this job. NuGuard does.

## 2.4 How NuGuard Fills the Gap

Every other red-teaming tool today — Garak, Promptfoo, PyRIT — starts with a prompt and fires it at a model. NuGuard starts by reading your **AI-SBOM (via Xelo)**: a complete inventory of your application's agents, MCP tools, APIs, databases, permissions, and system prompts. From that inventory, NuGuard builds an **Attack Surface Graph**, generates **context-aware multi-step exploit chains**, executes them through a coordinated **5-agent attack system**, and validates the results against your application's **Cognitive Policy** — the Markdown specification of how your AI is supposed to behave.

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
- Run a redteam test as part of their normal PR workflow — the same way they run `pytest` or `eslint`
- Get a clear pass/fail signal: did the agent violate anything? If yes, what specifically broke and how do I fix it?
- Not be blocked by complex tool setup, security expertise requirements, or expensive manual processes

**Trigger:** Opens a PR. CI runs. `nuguard scan` executes. Red line blocks merge if critical violations found.

**Success state:** redteam-test completes in under 10 minutes, produces a SARIF-compatible report surfaced in the PR, and gives specific remediation advice with code-level or configuration-level fix suggestions.

Creates an executive summary of the risk level of the agent's behavior, with links to detailed findings for any violations for executives who want to understand the risks.

## 3.2 Secondary Persona: "Executive Emma" — CTO / CISCO (v1.5+)

- Governs all AI applications across an organization
- Needs portfolio-level risk visibility: which apps are compliant, which have open critical findings, trend over time
- Unlocked in v1.5 with the web dashboard and organization-level policy management
- Reviews and approves the cognitive policy controls defined by the product owners, and needs to be assured that the red-teaming tests are effectively validating those controls before agents go live

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

# 5. Product Strategy: 

NuGuard's differentiation is not a single feature — it is compounding capabilities that no competitor has combined:

```
Moat 1: AI-SBOM Awareness      →  We understand WHAT your system can do
         ↓
Moat 2: Cognitive Policy Engine →  We know HOW it's supposed to behave
         ↓
Moat 3: Multi-Step Agentic Chains → We simulate HOW attackers would exploit the gap
         ↓
Moat 4: Behavior Analysis -> We evaluate not just security violations but also drift from expected behavior.
         ↓
Moat 5: Developer-First UX → We make it easy for developers to run red teams, understand results and take action without needing cybersecurity expertise.
```

Each moat individually is an improvement over existing tools. Together they form a category-defining product.

---

# 6. Strategic Pillar 1: Xelo AI-SBOM Integration

## 6.1 Why SBOM-First

Traditional red-teaming asks: *"What bad things can we make this model say?"*
NuGuard asks: *"Given everything this AI system can access and do, what is the most damaging thing an attacker could make it do?"*

The answer to that question requires knowing the system. Xelo's AI-SBOM provides that knowledge.

## 6.2 Xelo Integration Model

NuGuard integrates with [Xelo](https://github.com/xelo) as a **reference integration**: Xelo is the authoritative schema and tooling for generating the AI-SBOM. NuGuard consumes Xelo SBOM output and applies its own **mapping rules** to convert it into a NuGuard Attack Surface Graph.

This separation of concerns means:
- NuGuard does not re-spec the SBOM schema — it tracks Xelo's evolving standard
- NuGuard owns the security-relevant mapping layer
- Xelo SBOM improvements automatically improve NuGuard's attack surface coverage

### 6.2.1 API Endpoint Attack Surface (from Xelo Discovery)

When Xelo discovers API endpoints for a component, NuGuard expands each endpoint into its own attack surface node. This gives the attack graph **endpoint-level resolution** rather than treating an entire API as a single node.

Endpoint metadata from the SBOM (method, path, authentication requirements, whether it accepts user input, whether it returns sensitive data, rate limiting posture) drives targeted scenario generation:

- **IDOR / BOLA probes**: endpoints with user- or tenant-scoped path parameters are probed with out-of-scope values injected via the agent
- **Auth bypass probes**: endpoints without authentication or with broad permission scopes are targeted first
- **Parameter injection probes**: endpoints accepting user input are targeted with SQL injection, SSRF, and template injection payloads propagated through the agent's tool calls
- **Sensitive data exfiltration probes**: endpoints tagged as returning sensitive data are prioritised targets
- **Mass assignment probes**: endpoints accepting request bodies are probed for privilege escalation via over-posting

## 6.3 Attack Surface Graph Enrichment

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

The Policy Engine evaluates every agent action trace against the loaded policy. It detects three violation types in v1:

| Violation Type | Description | Example | Severity |
|---|---|---|---|
| `topic_boundary_breach` (restricted) | Agent responded to a topic explicitly listed in `restricted_topics` | Support agent provides investment advice | High |
| `topic_boundary_breach` (out-of-scope) | Agent responded to a topic with no overlap with `allowed_topics`, and `restricted_topics` does not apply | Support agent answers a general coding question | Medium |
| `restricted_action_executed` | Agent executed an action listed in `restricted_actions` | Agent deleted a user record without confirmation | High–Critical |
| `HITL_bypassed` | Agent completed a `HITL_trigger` action without human approval | Agent generated a contract with no escalation | High |

**`topic_boundary_breach` has two severity tiers:** `restricted_topics` violations are hard denies (the topic is explicitly forbidden regardless of context). `allowed_topics` boundary violations are softer — the agent drifted out of scope but did not breach a hard rule. This distinction is preserved in v1 keyword matching and informs finding severity.

Each violation is reported with: the policy clause violated, the exploit step that triggered it, the agent action taken, the evidence trace, and a remediation recommendation.

## 7.4 Application Log Correlation (Optional)

### Why Log Correlation

NuGuard's attack trace records what the red-team agents *attempted* and what the sandbox *responded with*. Application Log Correlation adds a second, independent evidence source: **the AI application's own runtime logs**. Correlating these two streams answers questions that the attack trace alone cannot:

- Did this tool call actually materialise in the application? *(confirms the attack reached the system)*
- Did the application's own error handling, input validators, or guardrails fire? *(confirms the attack was blocked — or wasn't)*
- Did an assertion or exception log entry appear immediately after the tool call? *(proof of defensive code in action — or its absence)*

This is an **optional step** enabled per test. When enabled, every policy violation finding is upgraded from *suspected* to `confirmed`, `blocked_by_app`, or `silent_success` — directly reducing false positives and escalating the most dangerous findings.

### How It Works

The developer supplies application logs alongside a test. NuGuard's Log Correlation Engine parses those logs for tool invocation records, API call records, exception/error entries, assertion failures, and guardrail trigger events. Each attack trace step is matched against log entries by timestamp proximity and operation identity. Each matched step receives a `log_correlation_status`:

| Status | Meaning | Effect on Finding Severity |
|---|---|---|
| `confirmed` | Log entry matches the tool/API call; no blocking signal | Severity unchanged or escalated |
| `silent_success` | Call executed AND no error handling / guardrail fired | Severity **escalated** — attacker succeeded with no defensive response |
| `blocked_by_app` | Log shows the app's own guard (exception, validator, guardrail) intercepted the call | Severity **downgraded** — residual risk reduced |
| `no_log_found` | No matching log entry found in the correlation window | No change — insufficient evidence |

### Supported Log Sources

| Source | Format | How to Reference |
|---|---|---|
| Local file | Structured JSON lines, OTEL, plain text with regex | `--log-source ./app.log` |
| OpenTelemetry trace export | OTLP JSON | `--log-source ./traces.otlp.json --log-format otel` |
| AWS CloudWatch Logs | JSON log events | `--log-source cloudwatch://log-group-name` |
| Azure Monitor / Log Analytics | JSON log events (KQL query result export) | `--log-source azuremonitor://workspace-id/query` |
| Azure Application Insights | JSON telemetry export | `--log-source appinsights://app-name` |
| GCP Cloud Logging | JSON log entries (Log Explorer export) | `--log-source gcp://project-id/log-name` |
| Datadog Logs | JSON log stream | `--log-source datadog://service-name` |
| Custom pattern | User-supplied JSONPath / regex extraction config | `--log-source ./app.log --log-config ./log-config.yaml` |

### What Log Correlation Does Not Do

- It does not re-execute attacks
- It does not require application source code access — only logs
- It does not require changes to the application being tested — the developer simply exports existing logs
- Correlation is **additive**: findings exist independently of log correlation; logs only adjust confidence levels

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

Each chain captures per-step action, target, result, and an aggregate risk score.

## 8.2 Chain-of-Custody

Every step in every chain produces a signed, deterministic trace entry. The full trace can be replayed exactly for developer debugging, provides audit evidence for compliance, and is cryptographically hashed to prevent tampering.

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
| SSRF via tool | Server-side request forgery (SSRF)via tool URL parameters to reach internal services | LLM04 |
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

## 10.3 Adaptive Mutation

When an attack partially succeeds (e.g., reaches a restricted tool but fails to extract data), the engine generates **mutated variants** that attempt to complete the chain:
- Paraphrase injection payloads
- Alternative encoding schemes
- Different tool parameter orderings
- Alternate chain paths through the attack graph

---

# 11. Feature: Agentic Attack System

## 11.1 Architecture

**v1:** The attack system is a **sequential `AttackExecutor`** that runs five logical attack phases in order, sharing in-process state:

```
AttackExecutor (sequential pipeline)
  Phase 1: Recon        → rank targets from attack graph
  Phase 2: Injection    → fire prompt injection payloads
           Tool Abuse   → attempt unauthorized tool calls   (parallel within phase)
  Phase 3: Exfiltration → extract data via established footholds (if any)
  Phase 4: Persistence  → memory poisoning (disabled in ci profile)
```

Each phase maps to a dedicated module (`recon_agent.py`, `injection_agent.py`, etc.) running an **Observe → Decide → Execute → Evaluate → Update** loop. State (target list, partial successes, memory store) lives in-process as a plain Python dataclass for the lifetime of the scan.

**v2:** The sequential executor is refactored into a true 5-agent swarm with Redis-backed shared state, enabling real-time coordination, parallel execution against live staging environments, and cross-agent partial-success signalling. The agent class interfaces are designed in v1 to make this refactor a one-file change per agent.

## 11.2 Agent Specifications

### ReconAgent
- **Input:** Enriched Attack Surface Graph
- **Role:** Rank all graph nodes by attack value (privilege level × reachability × data sensitivity); identify weak entry points (low-auth nodes, SSRF-possible nodes, PII-proximate paths)
- **Output:** Prioritized attack target list written to shared state

### InjectionAgent
- **Role:** Generate and fire prompt injection and context poisoning payloads
- **Targets:** Agent system prompts, tool descriptions, memory buffers, retrieved document content
- **Techniques:** Direct injection, indirect injection via tool output manipulation, encoding-based bypass, role-playing exploits
- **Adaptation:** Tracks injection success signals and escalates to mutation variants on partial success

### ToolAbuseAgent
- **Role:** Attempt unauthorized tool invocations, parameter injection, scope escalation
- **Techniques:** Out-of-scope tool calls, attacker-controlled parameter injection, permission boundary probing, SSRF via URL parameters, SQL injection via string parameters
- **Safety:** Operates within sandbox execution layer; real-execution mode requires explicit opt-in

### ExfiltrationAgent
- **Role:** Extract sensitive data from connected data stores using the agent as a proxy
- **Targets:** Relational databases (via SQL tool abuse), vector stores (embedding extraction), in-context secrets (API keys, PII), cross-tenant data (privilege escalation paths)
- **Techniques:** Structured query injection, context window excavation, encoding-based covert exfiltration

### PersistenceAgent
- **Role:** Establish footholds that survive across sessions
- **Techniques:** Memory poisoning (write adversarial content to persistent memory), long-context injection (embed instructions that activate on future sessions), prompt cache poisoning
- **Scope:** Disabled by default in `ci` profile; activated in full attack mode

## 11.3 Orchestration

The `AttackOrchestrator` manages the sequential phase pipeline:

1. `ReconAgent` runs first (always); populates in-process target list
2. `InjectionAgent` and `ToolAbuseAgent` run against their respective target sets (sequential in v1; parallel within `asyncio.gather` in v2)
3. `ExfiltrationAgent` activates when partial successes exist in in-process state
4. `PersistenceAgent` activates last, after exfiltration paths are established; skipped in `ci` profile
5. Orchestrator assembles `ExploitChain` records from step events and enforces the scan timeout

---

# 12. Feature: Target Application Execution Layer

## 12.1 How NuGuard Attacks the Running Application

NuGuard does not mock your application. It attacks the **real application running in your dev, QA, or staging environment** by sending adversarial inputs to its actual agent endpoints and evaluating the responses.

```
NuGuard AttackExecutor
        ↓  adversarial HTTP messages
Running AI Application  (localhost | QA | staging)
        ↓  agent invokes real tools, queries real DB, calls real APIs
Response captured and evaluated against Cognitive Policy
```

All attacks are delivered as crafted inputs to the application's agent endpoint — NuGuard never calls tools or databases directly. It exploits the agent as the attacker would: by manipulating its inputs to make it abuse its own capabilities.

### Environments

| Environment | How | Flag |
|---|---|---|
| Local dev | App running on localhost | `--target http://localhost:PORT` |
| QA / staging | App running in a persistent test environment | `--target https://qa.yourapp.com` |
| CI pipeline | App started by CI job before `nuguard` runs | `--target $APP_URL` |

No Docker management. No mock servers. The developer owns their environment — NuGuard is a black-box adversarial HTTP client.

### Attack Delivery Model

**Prompt injection / policy violations:**
NuGuard sends adversarial messages to the agent's chat or task endpoint. The response text is evaluated against the Cognitive Policy.

**Tool abuse / privilege escalation:**
NuGuard crafts messages designed to induce the agent to call its own tools with unauthorized parameters. The agent's response text (and optionally application logs) reveals whether the tool invocation occurred.

**Data exfiltration:**
Before the scan, the developer seeds **canary records** into the application's database — records with known unique values that should never appear in agent responses. NuGuard crafts adversarial prompts designed to extract those records. If a canary value appears in the response, exfiltration is confirmed.

```bash
# Developer seeds canary data using their own test setup, or via nuguard seed:
nuguard seed --target http://localhost:3000 --seed-file ./canary.json

# canary.json defines what to watch for in responses:
# { "watch_values": ["Canary_NuGuard_abc123", "987-65-4321", "test-api-key-xyz"] }

# Then run the redteam test against the live app:
nuguard redteam-test --sbom ./app.sbom.json --policy ./policy.md \
  --target http://localhost:3000 \
  --canary ./canary.json
```

The `--target` URL can also be embedded in the Xelo SBOM's agent component as `endpoint_url` — `--target` overrides it at runtime for environment flexibility.

## 12.2 Session Isolation and Replayability

- **Session isolation:** each attack scenario runs in a fresh agent session (new session ID); no state bleeds between scenarios
- **Canary data is non-destructive:** canary records are created by the developer's own test fixtures and cleaned up by those same fixtures — NuGuard never deletes application data
- **Deterministic trace:** every agent decision, message sent, and response received recorded in structured JSONL
- **Replay:** any completed redteam-test can be re-executed deterministically against the same target: `nuguard replay --scan-id <id> --target <url>`

---

# 13. Feature: Observability and Trace Layer

## 13.1 What Is Captured

For every scan, NuGuard captures a complete structured trace covering: agent decisions and reasoning, attempted and executed tool calls with parameters and outcomes, LLM turns with policy flags triggered, policy check results with violation type and evidence, exploit chain progress, and (when enabled) log correlation results.

## 13.2 Trace Output

Traces are written as **structured JSONL** and:
- Signed with a cryptographic hash for compliance evidence packages
- Immutable after test completion (append-only write model)
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
risk_score = Σ severity_weight(f) / n_findings

severity_weight: Critical=10, High=7, Medium=4, Low=1, Info=0
```

A simple, auditable aggregate score derived from finding severity distribution. Normalized to 0.0–10.0.

**v2 — weighted by exploit quality:**

```
risk_score = Σ (severity_weight × exploitability × blast_radius) / n_findings

exploitability:  0.0–1.0 (how reliably the exploit succeeds — meaningful only with real execution data)
blast_radius:    0.0–1.0 (scope of impact: data volume, user count, system access)
```

`exploitability` and `blast_radius` require real execution signal to be meaningful. In simulation mode these would be constant estimates, adding noise rather than signal. Both factors are deferred to v2 when real-execution mode is available.

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

# 15. User Experience

## 15.1 Developer Experience
- **CI/CD native:** Developer adds Xelo SBOM generation to their build pipeline. Xelo SBOM is part of the build artifacts and stored as an issue. NuGuard will pick the SBOM from the issue and run the redteam-test automatically. Developer receives a SARIF report in the GitHub Security tab with findings inline, and a human-readable Markdown report with detailed evidence and remediation advice. The Markdown report is attached as comment on PR for maximum visibility. Developer can click through from the report to the exact step in the attack trace where a finding was triggered, and see the full context of that step (agent reasoning, tool parameters, LLM response, policy clause violated).
Developer can optionally upload the cognitive policy as a separate file, or include it in the same issue as the SBOM. The policy is stored and versioned alongside the SBOM, allowing for historical drift analysis and policy evolution tracking. Policy can be updated independently of the SBOM through CLI or web interface, enabling iterative improvements and fine-tuning of controls.
The target URL, canary file, log source, and scenario filters can be set via CLI flags or environment variables, allowing developers to customize the testing posture per branch, environment, or stage of development.
- **Local CLI:** Developer can run redteam-tests locally during development with the same command as CI — just pointing `--target` at localhost. Local runs produce the same structured trace and report outputs for consistency.

## 15.2 InfoSec Experience

## 15.3 Executive Experience

---


# 18. MVP v1 Scope

## 18.1 In Scope for v1

| Feature | Notes |
|---|---|
| Xelo AI-SBOM ingestion | Reference integration; NuGuard mapping rules applied |
| Attack Surface Graph builder | Full risk attribute enrichment |
| Scenario generator | All 3 scenario types; pre-scoring; CLI-filterable |
| Sequential attack executor | 5 logical attack phases; PersistenceAgent off by default in `ci` profile; in-process state (no Redis) |
| Target app execution | Black-box HTTP client attacking real running app; `--target <url>`; canary data exfiltration detection via `--canary` |
| Cognitive Policy validation | 3 v1 violation types: `topic_boundary_breach` (2-tier), `restricted_action_executed`, `HITL_bypassed` |
| CLI — full command set | `nuguard redteam-test`, `nuguard seed`, `nuguard report`, `nuguard sbom`, `nuguard policy`, `nuguard findings`, `nuguard replay` |
| GitHub Actions integration | `nuguardai/scan-action@v1` |
| SARIF report output | PR diff inline; GitHub Security tab upload |
| Markdown report output | Human-readable developer view with fix suggestions |
| Per-finding remediation advice | Specific, code-level fix suggestions |
| OWASP LLM Top 10 mapping | All findings tagged (LLM01–LLM10) |
| Inherent risk score | Severity-weighted aggregate score (0.0–10.0) |

## 18.2 Explicitly Out of Scope for v1

| Feature | Target Version |
|---|---|
| Azure DevOps CI task (`NuGuardScan@1`) | v1.5 |
| Redis-backed shared agent state (enables true parallel multi-agent swarm) | v1.5 |
| Web dashboard | v1.5 |
| SDK / embeddable library | v1.5 |
| Platform Engineer persona | v1.5 |
| Scan-to-redteam-test drift detection | v1.5 |
| Application Log Correlation (optional, `--log-source`) | v1.5 |
| `data_classification_leak` violation type | v2 (requires PII tagging pipeline) |
| `unauthorized_tool_call` violation type | v2 (requires permission graph enforcement) |
| `exploitability` + `blast_radius` risk score factors | v2 (requires real execution data) |
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

Core agentic red-teaming platform. Complete SBOM ingestion, attack graph builder, sequential 5-phase attack executor (in-process state, no Redis), black-box `TargetAppClient` (HTTP client against real running app), canary-based exfiltration detection, cognitive policy validation (3 violation types with 2-tier `topic_boundary_breach`), full CLI with `--target` and `--canary` flags, GitHub Actions integration, SARIF + Markdown reports, OWASP LLM Top 10 mapping, severity-based risk score.

**Exit criterion:** A developer can run `nuguard redteam-test --sbom app.sbom.json --policy policy.md --target http://localhost:3000` in CI and receive a blocking report with actionable findings in under 10 minutes.

## v1.5 — Platform and Governance Layer (Target: Q3 2026)

Redis-backed shared agent state enabling true parallel multi-agent swarm. Azure DevOps CI task. Web dashboard for project portfolio visibility. Python SDK for embedding in test suites. Secondary persona (Platform Priya) features: org-level policy management, portfolio risk dashboard. Scan-to-redteam-test drift detection and regression alerting. Full 5-violation policy engine (`data_classification_leak`, `unauthorized_tool_call`). Application Log Correlation (`--log-source`). Self-hosted deployment option.

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
| **Sandbox fidelity gap** | High | Simulation mode is spec-driven (OpenAPI + SBOM-derived); real-execution mode closes gap for staging |
| **Attack cost scaling** | Medium | `ci` profile limits agent set and caps timeout; cost estimate shown before redteam-teststarts |
| **Model unpredictability degrades replay fidelity** | Medium | Deterministic trace records all inputs/outputs; temperature=0 for attack generation; trace replay does not re-invoke model |
| **Xelo SBOM schema evolution breaks mapping** | High | Versioned mapping rules; mapping layer is independently updatable; SBOM schema version validated before processing |
| **Cognitive Policy ambiguity causes false violations** | Medium | `nuguard policy validate` lints for ambiguous clauses before scan; violations cite exact policy clause |
| **Canary data pollutes production DB** | High | Canary seeding is the developer's responsibility via their own test fixtures; NuGuard never writes to or deletes from the target application directly; `nuguard seed` only calls the app's own API endpoints |
| **Adversarial inputs cause unintended side effects in staging** | Medium | Developers should point `--target` at an isolated QA environment, not production; `nuguard` documentation explicitly warns against running against production |

---

# 22. Appendix

## A. Cognitive Policy Details

| Policy Clause | Version |
|---|---|
| `allowed_topics` | v1 |
| `restricted_actions` | v1 |
| `HITL_triggers` | v1 |
| `data_classification` | v2 |
| `restricted_actions` + SBOM permissions | v2 |


---

*NuGuard AI — Red-Teaming Platform PRD v1.0 — March 18, 2026*
