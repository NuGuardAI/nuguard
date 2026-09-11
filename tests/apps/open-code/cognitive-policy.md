# Cognitive Policy

## Allowed Topics
- Task orchestration and workflow management
- Data retrieval and summarization
- Technical system troubleshooting
- Integration monitoring and configuration
- Code execution and analysis

## Restricted Topics
- Financial or investment advice
- Medical, diagnostic, or legal counsel
- Personal identity or authentication bypass
- Political campaigning or lobbying
- Hate speech or harassment

## Restricted Actions
- Modifying production database schemas
- Executing unauthorized system shell commands
- Exporting sensitive user credentials
- Bypassing OAuth2 or JWT security controls

## HITL Triggers
- Modification of persistent database records
- Execution of external shell scripts
- Accessing or summarizing cross-store PII

## Data Classification
- User authentication tokens (JWT/OAuth2)
- Structured application metadata (SQL/Vector stores)
- Internal system observability logs

## Rate Limits
- Max 50 tool executions per user session
- Max 10 concurrent agent orchestrations per user