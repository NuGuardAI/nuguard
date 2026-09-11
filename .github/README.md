<h1 align="center">nuguard</h1>

<p align="center">
  <strong>AI-SBOM generation, static analysis, and automated red-teaming / adversarial-attack-generation for AI agents and applications.</strong>
</p>

<p align="center">
  <a href="../LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache 2.0"></a>
  <a href="https://github.com/NuGuardAI/nuguard/actions/workflows/pr-tests.yml"><img src="https://github.com/NuGuardAI/nuguard/actions/workflows/pr-tests.yml/badge.svg?branch=Develop" alt="PR Tests"></a>
  <a href="https://pypi.org/project/nuguard/"><img src="https://img.shields.io/pypi/v/nuguard.svg" alt="PyPI"></a>
  <a href="https://pypi.org/project/nuguard/"><img src="https://img.shields.io/pypi/pyversions/nuguard.svg" alt="Python versions"></a>
  <a href="https://github.com/NuGuardAI/nuguard/stargazers"><img src="https://img.shields.io/github/stars/NuGuardAI/nuguard.svg?style=social" alt="GitHub Stars"></a>
</p>

<p align="center">
  <a href="#what-it-does">What it does</a> ·
  <a href="#see-it-in-action">See it in action</a> ·
  <a href="#framework-coverage">Framework coverage</a> ·
  <a href="#comparison">Comparison</a> ·
  <a href="#getting-started">Getting started</a> ·
  <a href="#faq">FAQ</a>
</p>

---

NuGuard is an open source AI application safety & security toolkit. It generates an AI Software Bill of Materials (AI-SBOM) for your agentic application, statically analyzes it for structural risk in the AI Stack and the software infrastructure. It then red-teams a sandboxed instance with a catalog of 100+ adversarial scenarios — prompt injection, tool abuse, data exfiltration, and more — so you find the issues before an attacker does. An automated judge evaluates the findings based on their impact and provides actionable remediation guidance.

## What It Does

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../documentation/docs/assets/what-it-does-dark.svg">
    <img src="../documentation/docs/assets/what-it-does-light.svg" alt="AI SBOM: Agents, Tools, API End Points, Models. Analyze: static risk scan, OWASP/MITRE mapping. Policy: Application's Intended Behavior. Behavior: Functional tests, allowed topics. Red-team: 100+ adversarial scenarios, sandboxed. Data exfil findings. Remediate: fixes for specific application components. Export: text, JSON, Markdown, SARIF." width="920">
  </picture>
</p>

## See It In Action

A real scan of a live fintech agent — Pinnacle Bank Assistant — walking through all five NuGuard stages: AI-SBOM, Cognitive Policy, Static Analysis, Behavior, and Red-Team. No mocks, no slides — real findings, including a live transcript of the agent leaking another customer's flagged fraud transactions on a routine question.

<p align="center">
  <a href="../documentation/docs/pinnacle-bank-demo.html">
    <img src="../documentation/docs/assets/pinnacle-bank-demo.gif" alt="Animated walkthrough of the NuGuard pipeline against Pinnacle Bank Assistant: SBOM discovery (159 nodes), Cognitive Policy (19 controls, 4 enforcement gaps), Static Analysis (621 findings), Behavior testing (risk score 59.8/100), and Red-Team (risk score 40.3/100, 37 findings, including a cross-account data leak)." width="1000">
  </a>
</p>

[**→ Open the interactive demo**](../documentation/docs/pinnacle-bank-demo.html) — scroll through the full walkthrough yourself.

## Framework Coverage

NuGuard's AI-SBOM extractor understands framework-specific code, not just generic regex — it recognizes agent/tool/model declarations natively across:

| Language | Frameworks |
|---|---|
| **Python** | LangChain, LangGraph, OpenAI Agents SDK, CrewAI (code + YAML), AutoGen (code + YAML), Google ADK, LlamaIndex, Agno, AWS BedrockAgentCore, Azure AI Agent Service, Azure Semantic Kernel, Guardrails AI, MCP Server (FastMCP + low-level) |
| **TypeScript / JavaScript** | LangChain.js, LangGraph.js, OpenAI Agents (TS), Azure AI Agents (TS), Agno (TS), MCP Server (TS) |
| **Go** | LangChainGo, Eino, Genkit, Anthropic SDK, OpenAI SDK, Google GenAI, MCP Server, net/http, Gorilla Mux, gqlgen |
| **C#** | Azure Semantic Kernel, ASP.NET Core, ML.NET |

Beyond the AI stack itself, the supply-chain/infrastructure analysis covers:

| Category | Coverage |
|---|---|
| **Infrastructure & Configuration** | Terraform, CloudFormation, Azure Bicep, Kubernetes manifests, GCP Deployment Manager, GitHub Actions, Dockerfiles, Nginx configs |
| **Data & Storage** | SQL schemas (PHI/PII classification), SQLAlchemy models, Django models, Pydantic models, prompt files (`.txt`/`.md`/`.jinja`) |
| **Output formats** | SARIF, CycloneDX, SPDX, Markdown |

See the [full framework matrix](../documentation/docs/index.html#frameworks) for details.

## Comparison

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../documentation/docs/assets/comparison-dark.svg">
    <img src="../documentation/docs/assets/comparison-light.svg" alt="Capability comparison cards. NuGuard: AI-SBOM generation, supply-chain analysis, policy engine, adaptive multi-turn attacks, OWASP LLM Top 10 mapping, auto remediation — all six. Garak: adaptive multi-turn attacks only. Promptfoo: adaptive multi-turn attacks and OWASP LLM Top 10 mapping." width="700">
  </picture>
</p>

## Getting Started

Install, then generate an SBOM, statically analyze it, and red-team a live target:

```bash
pip install nuguard

nuguard init --target <your-app-url>
nuguard sbom generate --source <path-to-your-app> --output app.sbom.json
nuguard analyze --sbom app.sbom.json --format markdown
nuguard redteam --config nuguard.yaml --format markdown --output reports/redteam.md
```

### 🚀 Ready to run NuGuard?

Installation, the full CLI surface, and the configuration reference — all in one guide.

[![Read the Quick Start guide](https://img.shields.io/badge/→_Read_the_Quick_Start_Guide-111111?style=for-the-badge)](../documentation/docs/quick-start.md)


### 🤖 Using Claude Code?

Install the NuGuard plugin and run SBOM, analysis, behavior, and red-team scans directly from Claude Code or Claude Desktop.

[![Read the Plugin Guide](https://img.shields.io/badge/→_Read_the_Plugin_Guide-111111?style=for-the-badge)](../documentation/docs/plugin-guide.md)

## Hosted Version

> **Running NuGuard at organizational scale?** The managed SaaS adds what a CISO or VP Engineering needs on top of everything in this repo — no infra to stand up or maintain.

<table align="center">
<tr>
<td align="center" width="120">
<img src="../documentation/docs/assets/logo-sm.png" alt="NuGuard.ai" width="72">
</td>
<td>

### [NuGuard.ai](http://nuguard.ai) — Managed SaaS for Security & Engineering Leaders

- 🔐 **RBAC** — role-based access across teams and business units
- 📊 **Executive dashboards** — risk posture and trends, board-ready
- 📋 **Audit-ready reports** — compliance-mapped to OWASP & MITRE ATLAS
- 🔗 **Enterprise integrations** — ServiceNow AI Control Tower, and more
- 🛟 **Managed support** — dedicated onboarding and SLAs

**Free trial available — no credit card required.**

[![Start Free Trial →](https://img.shields.io/badge/Start_Free_Trial_→-111111?style=for-the-badge)](http://nuguard.ai)

</td>
</tr>
</table>

## Contributing

### 🤝 Want to contribute?

Dev setup, running tests and lint, and the pull request process are covered in the Contributing guide.

[![Read the Contributing guide](https://img.shields.io/badge/→_Read_the_Contributing_Guide-111111?style=for-the-badge)](CONTRIBUTING.md)

Release publication is managed by repository maintainers. See
[Governance](../documentation/GOVERNANCE.md) for ownership and the
[release runbook](../documentation/releasing.md) for the maintained procedure.

## Repo Notes

- The repository currently contains example applications under `tests/apps/`
- LLM-assisted features depend on provider credentials being available via environment variables

## FAQ

**I have some questions, how do I reach out to folks behind this repo?**
You can contact us at [oss@nuguard.ai](mailto:oss@nuguard.ai)
For bug reporting, use the [issues](https://github.com/nuguard-ai/nuguard/issues) page on GitHub.

**Do I need a live app to get findings?**
No. `nuguard sbom` + `nuguard analyze` find structural and supply-chain risk statically. 
`nuguard behavior` and `nuguard redteam` need a running target typically in a sandbox.

**Which LLM providers are supported for LLM-assisted features?**
Configured via the `llm` section of `nuguard.yaml`; provider credentials are read from environment variables. Lite LLM is used to abstract any llm provider.

**What if I don't want to run all redteam scenarios?**
Filter by category or profile, or set `enabled: false` per scenario in a catalog exported with `nuguard redteam catalog-export`.

## License

[Apache 2.0](../LICENSE).

---

<sub>
<strong>Docs:</strong>
<a href="../documentation/docs/quick-start.md">Getting started / Quick start</a> ·
<a href="../documentation/docs/cli-reference.md">CLI reference</a> ·
<a href="../documentation/docs/policy-engine-guide.md">Policy engine</a> ·
<a href="../documentation/docs/static-analysis-guide.md">Static analysis</a> ·
<a href="../documentation/docs/redteam-guide.md">Red-team Guide</a> ·
<a href="../documentation/docs/plugin-guide.md">Claude plugin</a> ·
<a href="../documentation/docs/troubleshooting.md">Troubleshooting</a> ·
<a href="SECURITY.md">Security</a> ·
<a href="CONTRIBUTING.md">Contributing</a> ·
<a href="../documentation/GOVERNANCE.md">Governance</a>
</sub>
