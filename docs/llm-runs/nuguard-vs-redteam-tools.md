# AI Red-Teaming in 2026: NuGuard vs. Promptfoo, PyRIT, and Garak

*Posted on nuguard.ai/why-nuguard*

---

Four tools dominate the conversation when security teams ask how to adversarially test an AI application before it ships. Three are open-source projects with strong community adoption. One was built specifically for the way AI applications are actually architected today — as graphs of agents, tools, datastores, and behavioral policies.

Here is an honest look at all four.

---

## The Tools

**Promptfoo** (acquired by OpenAI, March 2026) is the most application-aware open-source red-team framework. It generates LLM-driven adversarial inputs from your app's description, runs them against a live endpoint, and grades responses with an LLM judge. YAML-declarative, CI/CD-native, and widely adopted.

**PyRIT** (Microsoft) is a programmer's toolkit — composable building blocks (converters, orchestrators, scorers) for assembling custom red-team workflows. Best-in-class for prompt mutation and obfuscation research. Requires writing Python code for every workflow; no turnkey CLI.

**Garak** (NVIDIA) is a systematic model-level vulnerability scanner. Runs a broad battery of pre-written probes against an LLM endpoint and produces a scored report. Fast to run against a new model; not designed for application-layer or multi-agent testing.

**NuGuard** is the only tool designed for the full complexity of modern AI applications. It generates attack scenarios across multi-agent graphs, judges with LLMs plus canaries and policy evaluation, and produces remediation recommendations scoped to the specific vulnerable component. Enterprise-ready, CI/CD-native, and built for security teams to run on their own.
---

## How They Compare

The six dimensions below reflect what enterprise security teams actually need before shipping an AI application.

| Capability | Promptfoo | PyRIT | Garak | **NuGuard** |
|---|:---:|:---:|:---:|:---:|
| **Enterprise-Readiness** | | | | |
| CI/CD-native runner | ✅ | — | Partial | ✅ |
| Authenticated sandbox / session management | Partial | Partial | — | ✅ |
| Compliance mappings (OWASP LLM, MITRE ATLAS, NIST) | ✅ | Partial | — | ✅ |
| **Agentic Applications** | | | | |
| Multi-agent / tool-call scenario coverage | Partial | Emerging | Minimal | ✅ |
| MCP toxic-flow attacks | Partial | — | — | ✅ |
| Agent handoff & privilege escalation chains | — | — | — | ✅ |
| **Dynamic Scenarios Based on Context** | | | | |
| Tests generated from app structure (AI-SBOM) | — | — | — | ✅ |
| Multi-turn adaptive conversations | ✅ | ✅ | Partial | ✅ |
| Scenario prioritised by component risk score | — | — | — | ✅ |
| **Judging** | | | | |
| LLM-as-judge | ✅ | ✅ | Partial | ✅ |
| Confidence levels (high / medium / low) | — | Partial | — | ✅ |
| Canary-based exfiltration detection | — | — | — | ✅ |
| Cognitive Policy evaluation | — | — | — | ✅ |
| **Remediation** | | | | |
| Automated fix recommendations | — | — | — | ✅ |
| Component-scoped remediation (per SBOM node) | — | — | — | ✅ |
| **Risk Analysis** | | | | |
| Static SBOM analysis before testing | — | — | — | ✅ |
| Risk-weighted finding severity | Partial | Partial | Partial | ✅ |
| Unified behavior + red-team report | — | — | — | ✅ |

---

## Where NuGuard Is Differentiated

### Enterprise-Readiness
All four tools can be run from a terminal. The gap opens up in what happens inside the scan. NuGuard manages full session authentication against your application — it logs in, maintains session cookies or bearer tokens across a multi-turn conversation, and re-authenticates if a session expires. This matters because authenticated scenarios reveal vulnerabilities that unauthenticated probes never reach: IDOR between accounts, privilege escalation through a legitimate user session, and policy violations that only surface once the agent knows who is asking.

NuGuard's CLI maps directly to a CI step — `nuguard redteam` exits non-zero on findings above a configurable severity threshold and writes SARIF-compatible JSON. Every finding is tagged with OWASP LLM Top 10, MITRE ATLAS technique, and NIST AI RMF control references, so results drop directly into existing compliance workflows.

### Agentic Applications
Promptfoo, PyRIT, and Garak were designed when "AI application" meant a single chat endpoint. NuGuard was designed for agent graphs: a triage agent that hands off to a booking agent, which calls a reservation tool, which queries a database.

NuGuard generates attack scenarios across the entire graph — not just at the front-door endpoint. It covers attack surfaces unique to multi-agent architectures: **AGENTIC_TRUST_ABUSE** (exploiting the trust a sub-agent places in its orchestrator), **MCP_TOXIC_FLOW** (injecting adversarial content through an untrusted MCP server into a write-capable tool), and privilege escalation chains that traverse multiple agent handoffs before reaching a sensitive operation. None of these scenarios exist out of the box in any other tool.

### Dynamic Scenarios Based on Context
The other tools generate tests from a text description you provide. NuGuard generates tests from AI-SBOM — a structural model of your application produced by scanning your source code. The SBOM maps every agent, tool, datastore, API endpoint, guardrail, and the relationships between them.

A tool that reads a SQL database gets SQL injection and IDOR scenarios automatically. An agent whose system prompt references customer PHI gets data exfiltration scenarios tailored to that data. Scenarios are then sorted by a risk-weighted priority score that combines the component's injection risk, data classification, and privilege level — so the highest-impact tests always run first, even when the total scenario count exceeds 100.

Within each scenario, NuGuard's guided conversation engine runs adaptive, multi-turn attacks steered by a goal-oriented director that scores progress (1–5) each turn and switches tactics when the agent resists, rather than replaying the same payload with different phrasing.

### Judging
LLM-as-judge is now table stakes. What distinguishes NuGuard is the judgment depth. Every LLM evaluation returns three things: a pass/fail verdict, a confidence level (high / medium / low), and an evidence quote — the specific phrase in the response that constituted the violation. Low-confidence results surface in the report but do not generate findings by default, reducing false-positive noise without hiding potential signals.

Beyond LLM judgment, NuGuard injects canary values into your application's data layer before running exfiltration scenarios. A canary hit — including encoding-obfuscated variants (Base64, Hex, URL) — is forensic-grade evidence of data leakage, not a heuristic. NuGuard also evaluates responses against your own Cognitive Policy: if an agent explicitly prohibited from discussing competitor products mentions one, that is a policy violation finding regardless of what an LLM judge thinks.

### Remediation
The other tools tell you what is vulnerable. NuGuard tells you how to fix it.

After a red-team run, NuGuard generates remediation artefacts scoped to the specific SBOM node that was exploited: a system prompt patch for a leaky agent, an input-validation recommendation for a SQL-injectable tool, an architectural change like adding a guardrail or introducing a human-in-the-loop checkpoint. Remediations are grouped by component and ordered by severity — a finding against `booking_agent` produces a fix recommendation for `booking_agent`, not generic hardening advice.

### Risk Analysis
NuGuard runs two phases before a single adversarial prompt is sent. First, a static SBOM analysis flags structural risks in the application graph: agents with no guardrails, tools with SQL-injectable parameters, datastores accessible without authentication, API endpoints missing rate limiting. These are deterministic findings that do not require a live app.

Second, a behavior module validates that the application behaves correctly under normal conditions — testing happy-path intent scenarios, coverage of each SBOM component, and Cognitive Policy topic coverage — before exposing any adversarial surface. The final report merges static analysis, behavioral test results, and red-team findings into a single ranked risk view, giving security and engineering teams a complete picture in one document.

---

*NuGuard is available at [nuguard.ai](https://nuguard.ai). The Claude Code plugin ships with every installation — run your first AI-SBOM scan without leaving your editor. Contact info@nuguard.ai to discuss your deployment.*
