<p align="center">
  <img src="../documentation/docs/nuguard-logo.png" alt="NuGuard" width="120">
</p>

<h1 align="center">NuGuard</h1>

<p align="center">
  <strong>AI-SBOM generation, static analysis, and automated red-teaming / adversarial-attack-generation for AI agents and LLM applications.</strong>
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
  <a href="#framework-coverage">Framework coverage</a> ·
  <a href="#comparison">Comparison</a> ·
  <a href="#getting-started">Getting started</a> ·
  <a href="#faq">FAQ</a>
</p>

---

NuGuard is an open source AI application security toolkit. It generates an AI Bill of Materials (AI-SBOM) for your agentic application, statically analyzes it for structural risk, then red-teams a running instance with a catalog of 125 adversarial scenarios — prompt injection, tool abuse, data exfiltration, and more — so you find the finding before an attacker does.

## What It Does

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../documentation/docs/assets/what-it-does-dark.svg">
    <img src="../documentation/docs/assets/what-it-does-light.svg" alt="SBOM: from local code or a remote repo. Analyze: static risk scan, no running app needed. Policy: propose, lint, and compliance-check. Behavior: runtime test vs declared intent. Red-team: 125 adversarial scenarios, live or sandboxed. Canaries: seeded values for high-confidence exfil findings. Remediate: fix suggestions with code snippets. Export: text, JSON, Markdown, SARIF." width="920">
  </picture>
</p>

## Example

<p align="center">
  <img src="../documentation/docs/assets/demo.svg" alt="NuGuard SBOM generation and red-team run against a live vulnerable fintech app: 122-node SBOM generated, then 9 findings (7 HIGH, 2 LOW) including SQL injection and IDOR, risk score 67.8/100." width="700">
</p>

[**→ View the full report**](../documentation/docs/assets/demo-redteam-report.md) — every scenario, turn-by-turn transcripts, and the generated remediation plan.

## Framework Coverage

<table>
<tr>
<td width="35%" valign="middle">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../documentation/docs/assets/owasp-coverage-dark.svg">
  <img src="../documentation/docs/assets/owasp-coverage-light.svg" alt="Donut chart: 10 out of 10 OWASP LLM Top 10 categories covered." width="260">
</picture>

</td>
<td width="65%" valign="middle">

The attack catalog (`catalog.yaml`) tags every scenario against the OWASP Top 10 for LLM Applications (2025) — all 10 categories are covered.

- **125** red-team scenarios
- **18** attack categories, spanning prompt injection, tool abuse, data exfiltration, supply chain, and more
- Scenarios can map to more than one OWASP category, so per-category counts sum to more than 125
- MITRE ATLAS and NIST AI RMF mapping — planned for a future catalog release

</td>
</tr>
</table>

## Comparison

<p align="left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../documentation/docs/assets/comparison-dark.svg">
    <img src="../documentation/docs/assets/comparison-light.svg" alt="Capability comparison cards. NuGuard: AI-SBOM generation, supply-chain analysis, policy engine, adaptive multi-turn attacks, OWASP LLM Top 10 mapping, auto remediation — all six. Garak: adaptive multi-turn attacks only. Promptfoo: adaptive multi-turn attacks and OWASP LLM Top 10 mapping." width="700">
  </picture>
</p>

## Hosted Version

<p align="center">
  <a href="http://nuguard.ai"><strong>NuGuard.ai</strong></a> — a managed SaaS version of NuGuard, with additional features and support on top of everything in this repo.
</p>

## Getting Started

### 🚀 Ready to run NuGuard?

Installation, the full CLI surface, a quick-start walkthrough, and the configuration reference — all in one guide.

[![Read the Getting Started guide](https://img.shields.io/badge/→_Read_the_Getting_Started_Guide-111111?style=for-the-badge)](../documentation/docs/getting-started.md)

### ⚙️ Need advanced red-team config?

Canaries, guided conversations, and finding triggers — covered in the Red-Team Design doc.

[![Read the Red-Team Design doc](https://img.shields.io/badge/→_Read_the_Red--Team_Design_Doc-111111?style=for-the-badge)](../documentation/docs/redteam-design.md#canaries-quick-setup)

## Contributing

### 🤝 Want to contribute?

Dev setup, running tests and lint, and the pull request process are covered in the Contributing guide.

[![Read the Contributing guide](https://img.shields.io/badge/→_Read_the_Contributing_Guide-111111?style=for-the-badge)](CONTRIBUTING.md)

## Repo Notes

- The repository currently contains example outputs and benchmark fixtures under `tests/output/`
- Some red-team and benchmark tests are opt-in and gated by environment variables
- LLM-assisted features depend on provider credentials being available via environment variables

## FAQ

**Do I need a live app to get findings?**
No. `nuguard sbom` + `nuguard analyze` find structural and supply-chain risk statically. `nuguard behavior` and `nuguard redteam` need a running target.

**Which LLM providers are supported for LLM-assisted features?**
Configured via the `llm` section of `nuguard.yaml`; provider credentials are read from environment variables. Azure-backed setups are exercised directly in this repo's own test suite.

**What if I don't want to run all 125 scenarios?**
Filter by category or profile, or set `enabled: false` per scenario in a catalog exported with `nuguard redteam catalog-export`.

## License

[Apache 2.0](../LICENSE).

## Star History

<a href="https://star-history.com/#NuGuardAI/nuguard&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=NuGuardAI/nuguard&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=NuGuardAI/nuguard&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=NuGuardAI/nuguard&type=Date" />
  </picture>
</a>

---

<sub>
<strong>Docs:</strong>
<a href="../documentation/docs/getting-started.md">Getting started</a> ·
<a href="../documentation/docs/quick-start.md">Quick start</a> ·
<a href="../documentation/docs/cli-reference.md">CLI reference</a> ·
<a href="../documentation/docs/policy-engine-guide.md">Policy engine</a> ·
<a href="../documentation/docs/static-analysis-guide.md">Static analysis</a> ·
<a href="../documentation/docs/redteam-design.md">Red-team design</a> ·
<a href="../documentation/docs/plugin-guide.md">Claude plugin</a> ·
<a href="../documentation/docs/publishing.md">Publishing</a> ·
<a href="../documentation/docs/troubleshooting.md">Troubleshooting</a> ·
<a href="SECURITY.md">Security</a> ·
<a href="CONTRIBUTING.md">Contributing</a>
</sub>
