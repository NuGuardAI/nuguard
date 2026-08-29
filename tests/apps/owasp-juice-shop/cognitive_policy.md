# Cognitive Policy

## Allowed Topics
- E-commerce product search and browsing
- User account and address management
- Shopping basket and cart operations
- Customer order inquiries
- Payment card management
- Recycling program participation

## Restricted Topics
- Offensive security testing or exploitation
- Unauthorized access to other users' data
- Financial fraud or payment manipulation
- Privacy-invasive data scraping
- Illegal or regulated goods trading
- System or database administrative tasks

## Restricted Actions
- Modify or delete other users' accounts, cards, or addresses
- Access or reveal database content, credentials, or proprietary business logic
- Execute API operations outside of user-authorized scope (e.g., privilege escalation)
- Bypass authentication or authorization controls

## HITL Triggers
- Request to delete a user account or payment card
- Request involving data export or disclosure of personal information
- Request to process a refund or void a transaction
- Request to alter product pricing or inventory quantities

## Data Classification
- Personal Identifiable Information (PII) such as names, addresses, and email addresses
- Payment card data and transaction history
- User account credentials and session tokens

## Rate Limits
- 60 API requests per minute per user session