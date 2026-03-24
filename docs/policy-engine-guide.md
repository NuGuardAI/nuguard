# NuGuard Policy Engine Guide

This guide explains how NuGuard’s policy engine works, what inputs it expects, which checks it performs, and how to use it from the CLI.


## What the Policy Engine Does

NuGuard’s policy engine handles four related jobs:

1. Parse a cognitive policy from Markdown into a structured model
2. Lint the policy for completeness and common mistakes
3. Cross-check the policy against an AI-SBOM for structural gaps
4. Assess an AI-SBOM against a compliance framework such as OWASP LLM Top 10

## Core Concepts

### Cognitive Policy

A cognitive policy is a Markdown document that describes how an AI application should behave.

The parser recognizes these sections:

- `Allowed Topics`
- `Restricted Topics`
- `Restricted Actions`
- `Human in the Loop` / `HITL Triggers`
- `Data Classification`
- `Rate Limits`


### AI-SBOM

The policy engine uses the AI-SBOM as the evidence source for structural checks and compliance assessment.
The SBOM should be generated with `nuguard sbom generate` and contain detailed inventory and metadata about the AI system’s components.

## Parsing

Policy parsing is rule-based, not LLM-based.

### Recognized heading aliases

Examples:

- `Allowed Topics` and `Permitted Topics`
- `Restricted Topics` and `Forbidden Topics`
- `Restricted Actions` and `Prohibited Actions`
- `Human in the Loop`, `HITL Triggers`, and `Human Approval Required`

### Example policy

```md
# Cognitive Policy

## Allowed Topics
- Customer support
- Order status

## Restricted Topics
- Medical advice
- Legal advice

## Restricted Actions
- Refund without user confirmation
- Account deletion without verification

## HITL Triggers
- Password reset requests
- Financial changes over $500

## Data Classification
- PII: name, email, phone
- Internal: user_id, tenant_id

## Rate Limits
- requests_per_minute: 60
- tokens_per_day: 100000
```

## Linting


Run it with:

```bash
nuguard policy validate --file cognitive_policy.md
```

### Current lint rules

- `POLICY-001`: no allowed topics and no restricted topics
- `POLICY-002`: HITL triggers exist but restricted actions are empty
- `POLICY-003`: a HITL trigger is too short and likely too vague
- `POLICY-004`: a rate limit value is invalid (`<= 0`)
- `POLICY-005`: all sections are empty
- `POLICY-006`: duplicate entries within a section

### Exit behavior

- `0`: no issues
- `1`: warnings only
- `2`: errors present
- `3`: CLI or file error

## Policy-to-SBOM Cross-Check

Run it with:

```bash
nuguard policy check --policy cognitive_policy.md --sbom app.sbom.json
```

This compares declared policy requirements against the structural evidence in the SBOM.

### Current structural checks

- `CHECK-001`: HITL triggers exist but no `GUARDRAIL` node exists in the SBOM
- `CHECK-002`: a restricted action does not match any `TOOL` node in the SBOM
- `CHECK-003`: policy declares data classification requirements but `DATASTORE` nodes lack classification metadata
- `CHECK-004`: policy declares rate limits but `API_ENDPOINT` nodes do not expose rate-limit metadata
- `CHECK-005`: HITL triggers exist but there are no `AUTH` nodes in the SBOM

### Why this matters

This step helps catch policy drift, for example:

- the policy says a human approval gate should exist, but the system inventory shows no guardrail component
- the policy restricts an action, but the named tool is not actually present in the SBOM
- the policy talks about sensitive data handling, but the SBOM has no datastore classification evidence

## Compliance Assessment

Compliance assessment is a more complex form of cross-checking where the policy engine evaluates how well the SBOM aligns with a set of controls defined by a framework such as OWASP LLM Top 10.

Run it with:

```bash
nuguard policy check --sbom app.sbom.json --framework owasp-llm-top10
```

Supported built-in frameworks currently include:

- `owasp-llm-top10`
- `nist-ai-rmf`
- `eu-ai-act`


### Assessment pipeline

The implemented flow is:

1. Build an assessment-ready snapshot from the SBOM
2. Load framework controls
3. Evaluate each control deterministically from SBOM evidence
4. Optionally fall back to an LLM when a control is not assessable from the SBOM alone
5. Aggregate the results into a weighted compliance score


### LLM fallback

The `--llm` flag enables LLM fallback for controls that cannot be fully assessed from SBOM evidence alone.

Example:

```bash
nuguard policy check \
  --sbom app.sbom.json \
  --framework owasp-llm-top10 \
  --llm
```

## CLI Usage

### Validate a policy

```bash
nuguard policy validate --file cognitive_policy.md
```

### Cross-check policy and SBOM

```bash
nuguard policy check \
  --policy cognitive_policy.md \
  --sbom app.sbom.json
```

### Run framework assessment

```bash
nuguard policy check \
  --sbom app.sbom.json \
  --framework owasp-llm-top10
```

### Run both together

```bash
nuguard policy check \
  --policy cognitive_policy.md \
  --sbom app.sbom.json \
  --framework owasp-llm-top10
```

### Use `nuguard.yaml`

If `sbom` and `policy` are already defined in `nuguard.yaml`, you can run:

```bash
nuguard policy check
```

## Output Behavior

### Text mode

`--format text` prints:

- a policy lint table for `validate`
- a gap table for policy-to-SBOM cross-checks
- assessment summaries for framework runs

### JSON mode

`--format json` is intended for automation and integrations.

## Typical Workflow

Recommended order:

1. Start a starter policy:

```bash
nuguard init
```

2. Fill in the policy content
3. Generate an SBOM:

```bash
nuguard sbom generate --source . --output app.sbom.json
```

4. Lint the policy:

```bash
nuguard policy validate --file cognitive_policy.md
```

5. Cross-check policy and SBOM:

```bash
nuguard policy check --policy cognitive_policy.md --sbom app.sbom.json
```

6. Run a framework assessment if needed:

```bash
nuguard policy check --sbom app.sbom.json --framework owasp-llm-top10
```

## Troubleshooting Notes

Common issues:

- the policy file exists but contains only headers, producing an “all sections empty” error
- the SBOM lacks `GUARDRAIL`, `TOOL`, `DATASTORE`, or `API_ENDPOINT` evidence expected by the policy
- the policy uses names that do not match SBOM component names closely enough
- the assessment framework name is misspelled

Related docs:

- [docs/quick-start.md](/workspaces/nuguard-oss/docs/quick-start.md)
- [docs/cli-reference.md](/workspaces/nuguard-oss/docs/cli-reference.md)
- [docs/troubleshooting.md](/workspaces/nuguard-oss/docs/troubleshooting.md)
- [docs/sbom-schema.md](/workspaces/nuguard-oss/docs/sbom-schema.md)
