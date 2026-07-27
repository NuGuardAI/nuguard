# Why NuGuard: AI Red-Teaming Built for the Agentic Era

*Posted on nuguard.ai/why-nuguard*

---

The landscape of AI security tooling has matured quickly. A handful of open-source frameworks — Promptfoo, Microsoft's PyRIT, and NVIDIA's Garak — have become the reference points for teams trying to red-team their LLM applications before shipping. Each is genuinely useful. None was designed for what most AI applications actually look like in 2026: multi-agent systems with tool calls, handoffs, datastores, and Cognitive Policies that govern how each agent is allowed to behave.

This article summarizes what each tool does, where it excels, and where NuGuard goes further.

---

## The Field: Three Open-Source Frameworks

### Promptfoo

Promptfoo began as an LLM evaluation framework and evolved into the most application-aware open-source red-team tool available. It generates adversarial inputs using an LLM seeded with your application's purpose, runs them against a live endpoint, and grades responses with an LLM judge. Its plugin library (50+ vulnerability types), YAML-declarative configuration, and CI/CD-native runner make it the go-to choice for teams that want a scanner they can run in a pull-request gate.

Promptfoo added multi-turn support via its Simulated User provider and Hydra strategy (adaptive multi-turn with memory). It has some agentic surface coverage: OpenAI Agents SDK, MCP server connections, and RAG pipeline injection are all supported. In March 2026, OpenAI acquired Promptfoo, signalling the broader industry's recognition that application-level security testing is essential.

**Where it stops short:** Promptfoo generates tests from your application's *description*, not from a structural analysis of what your application actually is. There is no notion of an AI-SBOM — no automated mapping of which agents call which tools, which tools access which datastores, or which policy constraints each agent operates under. Remediation guidance is limited to OWASP/MITRE framework mappings; it does not generate component-specific fix recommendations.

---

### PyRIT (Microsoft)

PyRIT is a programmer's toolkit for composing custom red-team workflows. Rather than a turnkey scanner, it provides building blocks: Targets (LLM/HTTP endpoints), Converters (30+ prompt obfuscation techniques — Leetspeak, Base64, Unicode manipulation, translation), Orchestrators (six attack strategies including the sophisticated Crescendo gradual-escalation attack and Tree of Attacks with Pruning), and Scorers (binary/Likert/LLM-judge). Full conversation provenance is stored in SQLite or Azure SQL automatically.

PyRIT's depth in prompt mutation is unmatched — if your goal is to exhaustively probe a model's guardrails using encoding and obfuscation variants, it is the right tool. Microsoft has battle-tested it across 100+ internal red-team operations on Copilot and Phi-3.

**Where it stops short:** PyRIT is a library, not a product. Using it requires writing Python orchestration code for every workflow. There is no CLI, no YAML config, no turnkey scan. Remediation guidance is explicitly absent from the roadmap for the foreseeable future — PyRIT tells you what is vulnerable, not how to fix it. Agentic scenario coverage (tool-call interception, agent handoff exploitation, MCP flows) is emerging rather than production-ready.

---

### Garak (NVIDIA)

Garak is a systematic LLM vulnerability scanner — closer in spirit to Nessus or Nikto than to an application-layer security test suite. It runs a comprehensive battery of pre-defined probe modules against a model endpoint and produces a scored report. Its probe library covers jailbreaks, encoding-based bypasses, data leakage, toxicity, package hallucination, glitch tokens, and social bias. Multi-turn support (GOAT probe, agent-breaker probe) and NeMo Guardrails integration arrived in v0.15.0 (May 2026).

Garak's breadth-first scan is the fastest way to establish a baseline vulnerability posture for a newly deployed model. Its DEFCON-style visual severity ratings and confidence-interval scoring make results easy to communicate.

**Where it stops short:** Garak scans models, not AI applications. It has no concept of tools, datastores, agent graphs, Cognitive Policies, or API-layer attacks. Historically single-turn, its multi-turn and agentic capabilities are recent and cover a small fraction of its probe library. There is no remediation guidance. Tests are not customized to your application's actual architecture.

---

## Tool Comparison at a Glance

| Capability | Promptfoo | PyRIT | Garak | **NuGuard** |
|---|:---:|:---:|:---:|:---:|
| AI-SBOM-driven test generation | — | — | — | ✅ |
| Dynamic, app-aware scenario creation | Partial | — | — | ✅ |
| Multi-turn contextual conversations | ✅ | ✅ | Partial | ✅ |
| Adaptive attack escalation | ✅ (Hydra) | ✅ (Crescendo, TAP) | Partial (GOAT) | ✅ |
| Multi-agent / tool-call scenarios | Partial | Emerging | Minimal | ✅ |
| MCP toxic-flow attacks | Partial | — | — | ✅ |
| Cognitive Policy evaluation | — | — | — | ✅ |
| Canary-based data exfiltration detection | — | — | — | ✅ |
| LLM judge with confidence levels | ✅ | ✅ | Partial | ✅ |
| Automated component-level remediation | — | — | — | ✅ |
| OWASP LLM / MITRE ATLAS mapping | ✅ | Partial | — | ✅ |
| Claude Code plugin (IDE integration) | — | — | — | ✅ |
| Behavior + red-team unified pipeline | — | — | — | ✅ |
| No-code / CLI experience | ✅ | — | ✅ | ✅ |

---

## What Makes NuGuard Different

### 1. The AI-SBOM: Knowing What You're Actually Testing

Every NuGuard red-team run begins with an AI Bill of Materials (AI-SBOM) — a structural model of your application generated by statically scanning your source code. The SBOM maps agents, tools, datastores, API endpoints, guardrails, models, and IAM roles, and encodes the relationships between them (which agent calls which tool, which tool reads which database, which guardrail protects which agent).

This is the foundation that no other tool has. Promptfoo and PyRIT generate attacks from a text description of your app's purpose. NuGuard generates attacks from a precise, machine-readable understanding of your app's actual component graph. An agent that has access to a tool that queries a SQL database will automatically receive SQL injection and IDOR attack scenarios. An agent with a system prompt containing customer PII references will automatically receive tailored data exfiltration scenarios. Nothing needs to be hand-configured.

```
nuguard sbom generate --from-repo ./my-agent-app
nuguard redteam --sbom ./app.sbom.json --target http://localhost:8080
```

### 2. Multi-Turn Contextual Conversations with Goal-Oriented Direction

NuGuard's guided conversation engine runs adaptive, multi-turn adversarial conversations steered by a ConversationDirector that scores progress toward an attack goal on a 1–5 scale each turn. The director selects from a library of proven escalation tactics (hypothetical framing, authority appeals, gradual rapport-building, jailbreak variants) based on the agent's resistance pattern, switching tactics when the current approach plateaus.

This is not just "stateful prompting" — it is a goal-directed adversarial agent. A breakthrough turn (progress = 5) is identified and reported separately from warmup turns, so finding evidence focuses on the exact exchange where the vulnerability manifested rather than an unstructured transcript dump.

The engine supports:
- Configurable max turns, milestone tracking, and success signal detection
- Per-turn LLM evaluation with high/medium/low confidence and evidence quotes
- Adaptive mutation of failed payloads for static chain attacks (not just guided conversations)

### 3. Multi-Tool Agentic Attack Scenarios

Modern AI applications are not single-model chatbots. They are agent graphs: a triage agent hands off to a booking agent, which calls a reservation tool, which queries a database. NuGuard's scenario generator understands this topology and builds attack scenarios that traverse it.

NuGuard covers attack scenarios that do not exist in any other open-source tool:

- **AGENTIC_TRUST_ABUSE** — exploit the trust a sub-agent places in instructions from an orchestrating agent
- **MCP_TOXIC_FLOW** — inject adversarial content via untrusted MCP server responses that flow into write-capable tools
- **TOOL_ABUSE** — craft parameter-injection payloads from the tool's own parameter schema (name, type, description) to coerce unauthorized behavior
- **PRIVILEGE_ESCALATION via handoff chain** — craft a scenario that chains a low-privilege entry point through a sequence of agent handoffs to reach a privileged tool

Attack scenarios are sorted by a risk-weighted priority score that combines the SBOM node's injection risk, data sensitivity, and privilege level — so the highest-impact scenarios execute first.

### 4. Cognitive Policy Evaluation

NuGuard's red-team engine is the only tool that evaluates your AI application against its own behavioral contract. You define a Cognitive Policy — a structured document specifying what each agent is allowed to discuss, what actions it may take, what data it may access, and what it must refuse — and NuGuard's policy engine evaluates every response against it in real time during a red-team run.

A scenario that extracts PII from an agent explicitly prohibited from sharing user data is a policy violation finding, not just an LLM "judge" heuristic. This distinction matters for compliance: it ties findings directly to the behavioral requirements your product and legal teams have signed off on.

### 5. Canary-Based Data Exfiltration Detection

NuGuard injects canary values into your application's data layer before running exfiltration scenarios. If an agent response contains a canary (or an encoded variant — Base64, Hex, URL-encoded), NuGuard registers a confirmed canary hit finding. This is forensic-grade proof of data exfiltration, not a pattern-match heuristic. It catches encoding-obfuscated leaks that keyword detectors miss.

### 6. Automated, Component-Level Remediation

After a red-team run, NuGuard does not just hand you a list of CVE numbers and a MITRE mapping. It generates concrete remediation artefacts tied to the specific SBOM nodes that were exploited:

- **System prompt patches** — exact additions or guardrail instructions for each agent
- **Tool hardening** — specific parameter validation, authorization checks, or rate-limit recommendations for each vulnerable tool
- **Architectural changes** — structural changes like adding a guardrail node, separating read/write tool privileges, or introducing a human-in-the-loop checkpoint

Remediations are grouped by component and prioritized by severity. A finding against the `booking_agent`'s system prompt produces a remediation recommendation scoped to `booking_agent`, not a generic "harden your prompts" instruction.

### 7. Claude Code Plugin: Security Testing in the IDE

NuGuard ships as a first-class Claude Code plugin, bringing AI security testing into the developer workflow without leaving the editor. From within Claude Code, a developer can:

- Run a full SBOM scan on the current repository
- Launch a behavior or red-team test against a locally running app
- Review findings and remediation recommendations inline
- Configure NuGuard target URL, credentials, and scan profiles

This is a fundamental shift in where security testing happens. Instead of a separate CI pipeline step owned by a security team, AI red-teaming becomes part of the inner development loop — the same loop in which the vulnerability was introduced.

### 8. Unified Behavior + Red-Team Pipeline

NuGuard's behavior module tests your application against a library of intent-happy-path and invariant scenarios before any adversarial testing begins. This establishes a behavioral baseline: which agents respond correctly to normal requests, which topics they cover, which tools they exercise. The behavior report includes:

- AI-SBOM component coverage evidence (which turns exercised which agents and tools)
- Cognitive Policy topic coverage (which policy topics were tested and matched)
- Scenario-level pass/fail with full Request → Response transcripts

The red-team module then targets exactly the components and topics where the behavior baseline revealed the highest risk. The unified validation report brings both together into a single document with a consistent scenario-detail section, making the full picture auditable in one place.

---

## Summary: The Right Tool for the Job

| If you need… | Use |
|---|---|
| A quick scan of a new model against known jailbreaks | Garak |
| Deep prompt mutation and obfuscation research | PyRIT |
| CI/CD-integrated application-level red-teaming with YAML config | Promptfoo |
| AI-SBOM-driven security testing for a multi-agent application, with adaptive conversations, Cognitive Policy evaluation, canary exfiltration detection, and automated remediation | **NuGuard** |

Promptfoo, PyRIT, and Garak are strong tools that solve real problems. They were built for LLM applications, and they do that job well. NuGuard was built for the next generation of AI systems — the ones where the attack surface is not a single model endpoint but a graph of agents, tools, datastores, and policies that only a structural understanding of the application can map.

---

*NuGuard is available at [nuguard.ai](https://nuguard.ai). The Claude Code plugin ships with every installation. Reach out at security@nuguard.ai to discuss your use case.*
