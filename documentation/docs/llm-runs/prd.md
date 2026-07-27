# AI Red-Teaming Platform — Product Requirements Document (PRD)

## 1. Overview

A platform to perform **end-to-end red-teaming of AI-powered applications**, focusing on:
- Multi-endpoint API workflows (not just /chat)
- Auth/session-aware testing
- Domain-specific attack scenarios
- Business-logic and data isolation validation

Positioning:
> "Burp Suite for AI applications with domain-aware adversarial testing"

---

## 2. Goals

### Primary Goals
- Detect **cross-user / cross-tenant data leakage**
- Validate **authorization and identity boundaries**
- Test **AI-specific risks (prompt injection, tool abuse, hallucinated actions)**
- Support **real application workflows**

### Non-Goals
- Model training or fine-tuning
- Static code analysis
- Traditional vuln scanning (delegated to other tools)

---

## 3. Target Users

- Security engineers (AppSec, AI security)
- Red teams
- Compliance teams (HIPAA, PCI, SOC2)
- AI platform teams

---

## 4. Key Features

### 4.1 Target Definition
- OpenAPI import
- Manual workflow DSL
- Session/state modeling
- Variable extraction/substitution

### 4.2 Auth Support
- API keys / Bearer tokens
- Username/password
- OAuth2 / OIDC
- SSO (browser automation)
- Session cookies

---

### 4.3 Scenario Engine

#### Base Attack Classes
- Prompt injection
- Data exfiltration
- AuthZ bypass
- Tool misuse
- Memory poisoning
- Retrieval abuse

#### Domain Packs
- Healthcare
- Fintech
- ITSM

---

### 4.4 Execution Engine
- Multi-step workflows
- Async handling (polling, jobs)
- Identity switching
- Retry + rate limiting

---

### 4.5 Evaluation Engine

#### Types
- Deterministic (regex, rules)
- Semantic (LLM judge)
- Trace-based (API/tool calls)
- Differential (role/tenant comparison)

---

### 4.6 Coverage Reporting
- Endpoint coverage
- Identity coverage
- Scenario coverage
- Data domain coverage

---

### 4.7 Analyst Features
- Replay attacks
- Diff runs across builds
- CI/CD integration
- Finding lifecycle tracking

---

## 5. MVP Scope

- REST APIs only
- YAML DSL workflows
- 3 domain packs (Healthcare, Fintech, ITSM)
- 5 detector types
- Basic UI + CLI
- CI integration

---

## 6. Success Metrics

- % of critical issues found pre-production
- False positive rate < 15%
- Scenario execution success rate > 85%
- Time to reproduce issue < 2 min

---

## 7. Risks

- Complex auth flows (SSO)
- Flaky async workflows
- Over-reliance on LLM judges
- High setup cost for users

---

## 8. Future Roadmap

- GraphQL/WebSocket support
- Agent/tool tracing (LangChain, etc.)
- Autonomous scenario generation
- Attack graph visualization