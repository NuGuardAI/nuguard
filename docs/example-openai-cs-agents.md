# Example: OpenAI Customer Service Agents Demo

**Repository:** [NuGuardAI/openai-cs-agents-demo](https://github.com/NuGuardAI/openai-cs-agents-demo)  
**Framework:** OpenAI Agents SDK  
**NuGuard CLI:** `nuguard`

This guide shows how to run NuGuard against the OpenAI customer-service demo application. It covers the core CLI flow:

- generate an AI-SBOM
- run static analysis
- initialize and validate a cognitive policy
- run a red-team scan against a live target

## About the Example Application

The example repository is a multi-agent airline customer-service assistant built with the OpenAI Agents SDK. It includes:

- multiple specialized agents
- function tools wired to those agents
- prompt instructions and guardrails
- a FastAPI backend and web frontend

That makes it a good example for NuGuard because the repository contains agent logic, tools, prompts, APIs, and deployment signals in one app.

## Prerequisites

Install NuGuard:

```bash
pip install nuguard
```

Check the CLI:

```bash
nuguard --help
```

## 1. Generate an AI-SBOM

You can scan the demo repository directly from Git without cloning it first:

```bash
nuguard sbom generate \
  --from-repo https://github.com/NuGuardAI/openai-cs-agents-demo \
  --ref main \
  --output openai-cs-agents.sbom.json
```

Validate the generated file:

```bash
nuguard sbom validate --file openai-cs-agents.sbom.json
```

What you should expect from this SBOM:

- `AGENT` nodes for the service agents
- `TOOL` nodes for function tools
- `PROMPT` nodes for agent instructions and guardrails
- `API_ENDPOINT`, `MODEL`, `GUARDRAIL`, and deployment-related nodes

## 2. Run Static Analysis

Run the static analysis pipeline against the generated SBOM:

```bash
nuguard analyze \
  --sbom openai-cs-agents.sbom.json \
  --format markdown
```

Useful variants:

```bash
nuguard analyze --sbom openai-cs-agents.sbom.json --format json
nuguard analyze --sbom openai-cs-agents.sbom.json --format sarif
```

If you have the repository checked out locally, include `--source` so IaC and dependency-oriented tools have filesystem context:

```bash
git clone https://github.com/NuGuardAI/openai-cs-agents-demo

nuguard analyze \
  --sbom openai-cs-agents.sbom.json \
  --source ./openai-cs-agents-demo \
  --format markdown
```

This is the path that helps optional scanners such as Trivy, Grype, Semgrep, and Checkov operate on the local source tree.

## 3. Initialize a Cognitive Policy

Create a starter policy file:

```bash
nuguard init
```

That generates a template `cognitive_policy.md` with the recognized section headers. From there, fill in the behavioral requirements for the customer-service agents, for example:

- what personal data the agents may disclose
- when a human escalation is required
- what actions must never be performed without confirmation
- topic boundaries for airline support workflows

Validate the policy structure:

```bash
nuguard policy validate --file cognitive_policy.md
```

Cross-check the policy against the SBOM:

```bash
nuguard policy check \
  --policy cognitive_policy.md \
  --sbom openai-cs-agents.sbom.json
```

## 4. Use Project Config

If you plan to run multiple commands, start from the example config:

```bash
cp nuguard.yaml.example nuguard.yaml
```

Typical fields to set for this example:

```yaml
redteam:
  target: http://localhost:8000
  target_endpoint: /chat

llm:
  redteam:
    provider: openai
    model: gpt-4.1
  eval:
    provider: openai
    model: gpt-4.1-mini
```

Important red-team note:

- the redteam LLM must not have content filters or guardrails that block adversarial prompts
- the eval LLM can be any generally capable model

## 5. Red-Team the Live App

For red-teaming, you need the demo app running locally or in a staging environment.

Generate canary data from the tracked example if you want stronger exfiltration detection:

```bash
cp canary.example.json canary.json
```
Update the canary values to match the expected format but with unique identifiers, for example:

```json
{
  "customer_id": "canary-customer-123",
  "email": "canary-email-123@example.com"
}

Then run:

```bash
nuguard redteam \
  --sbom openai-cs-agents.sbom.json \
  --target http://localhost:8000 \
  --policy cognitive_policy.md \
  --canary ./canary.json \
  --output redteam_report.md \
  --format markdown
```

In this example, NuGuard will use the SBOM to generate scenarios around:

- prompt injection and goal redirection
- guardrail bypass
- tool abuse
- sensitive data exfiltration
- policy violations in customer-support conversations

## 6. Recommended End-to-End Flow

For the OpenAI customer-service demo, the most useful sequence is:

1. `nuguard sbom generate` to map the app's agent and tool surface.
2. `nuguard analyze` to catch structural and dependency issues.
3. `nuguard init` and `nuguard policy check` to define and verify expected behavior.
4. `nuguard redteam` against a running instance to test the actual conversation paths.

## Related Docs

- [Quick Start Guide](./quick-start.md)
- [CLI Reference](./cli-reference.md)
- [AI SBOM Schema](./sbom-schema.md)
- [Static Analysis Guide](./static-analysis-guide.md)
- [Policy Engine Guide](./policy-engine-guide.md)
- [Red-Teaming Guide](./red-teaming-guide.md)
