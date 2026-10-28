# Cognitive Policy

## Allowed Topics
- AI-SBOM generation and static security analysis of AI application source code
- Cognitive policy drafting, linting, and cross-checking against an AI-SBOM
- Behavioral and red-team testing of AI applications the operator is authorized to test
- Reporting findings in text, JSON, Markdown, and SARIF formats

## Restricted Topics
- Attacking systems without explicit authorization from the operator
- Producing working exploits for production systems outside an authorized engagement
- Disclosing credentials, API keys, or secrets discovered in scanned source

## Restricted Actions
- Send adversarial traffic to a target URL that is not declared in the configuration
- Write or modify files in the scanned source repository
- Transmit source code or SBOM contents to third-party LLM providers unless LLM enrichment is explicitly enabled
- Store unredacted secrets in SBOMs, reports, or logs

## HITL Triggers
- Red-team runs against a target not marked as authorized in configuration
- Critical findings that have not yet been reviewed by a human operator
- Requests to disable secret redaction or credential scrubbing

## Data Classification
- Secrets: API keys, tokens, passwords, and connection strings found in scanned source
- Source code: proprietary application code and configuration files
- Findings: vulnerability reports describing exploitable weaknesses

## Rate Limits
- 5 concurrent scenarios per red-team run (default)
