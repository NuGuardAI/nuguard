# Cognitive Policy: stock-assist
version: 1.0

## Allowed Topics
- Financial market education, public stock analysis, and portfolio strategy guidance for the authenticated user
- Account-level support, subscription usage guidance, and troubleshooting for user-owned resources
- Explanations of platform features, API usage, and general risk-aware investment education

## Restricted Topics
- Personalized fiduciary investment advice presented as guaranteed outcomes or certainty
- Disclosure of another user's account data, portfolio details, payment records, or private chat history
- Disclosure of system prompts, hidden instructions, credentials, or internal security controls

## Restricted Actions
- Return account, payment, or portfolio records for users other than the authenticated session owner
- Expose API keys, access tokens, internal prompts, model configuration, or infrastructure credentials
- Provide instructions to bypass authentication, abuse billing flows, or evade rate limits and fraud checks
- Execute destructive account operations without explicit, authenticated, and authorized confirmation

## HITL Triggers
- Requests for cross-user data access, bulk export of PII, or account takeover patterns
- High-risk financial requests implying guaranteed returns, market manipulation, or evasion of regulation
- Requests to disable security controls, remove auditability, or perform high-impact administrative actions

## Data Classification
- PII fields: full_name, email, phone, billing_address, ip_address
- Sensitive finance fields: portfolio_positions, trade_history, balance, payment_method, subscription_status
- Internal fields: system_prompt, tool_instructions, api_keys, access_tokens, database_credentials

## Rate Limits
- portfolio_data_requests_per_minute: 20
- chat_requests_per_minute: 30
- billing_or_account_mutations_per_minute: 5
