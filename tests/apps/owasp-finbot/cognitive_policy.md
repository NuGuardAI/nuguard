# Cognitive Policy

## Allowed Topics
- Account balance and transaction history
- Tax rate and calculation information
- Fund transfers and payment processing
- Administrative and analytics reporting
- File retrieval and document access

## Restricted Topics
- Medical or health-related financial information
- Legal advice or compliance determinations
- Investment recommendations or portfolio management
- Employment or payroll data
- Government benefits or entitlement programs

## Restricted Actions
- Deleting system files or database records
- Executing arbitrary scripts or commands
- Creating unauthorized transfers without confirmation
- Modifying guardrail or security configurations

## HITL Triggers
- Transfer amounts exceeding predefined thresholds
- Deletion of user accounts or transaction records
- Changes to system-level security policies

## Data Classification
- FINANCIAL (account balances, transfer details)
- PII (user identities from copilot history)
- OPERATIONAL (system logs, access records)

## Rate Limits
- /api/v1/copilot/history: 100 requests per minute per user
- Transfer endpoints: 10 requests per minute per user