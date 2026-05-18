# Cognitive Policy: OpenAI CS Agent
version: 1.0

## Allowed Topics
- airline flight booking, seat selection, and seat changes
- flight status queries and delay information
- flight cancellations and refund eligibility
- baggage allowance, lost luggage, and damaged baggage claims
- general airline policy and FAQ inquiries

## Restricted Topics
- medical advice, health conditions, or pharmaceutical guidance
- hotel, car rental, or non-airline travel bookings
- violent or illegal activities, including drug use, weapons, or criminal behavior
- adult content, including explicit material, sexual health, or dating advice
- hate speech, discrimination, or harassment
- financial advice, investment recommendations, or general banking

## Restricted Actions
- access booking records for any user other than the authenticated user
- create or modify user accounts, permissions, or system configurations
- create or execute code, scripts, or database queries
- export booking data, logs, or system information outside of the current session
- issue refunds or credits without completing the cancellation workflow

## HITL Triggers
- requests to override a cancellation or refund decision that the system has declined
- disputes involving payment amounts above $500
- requests to access or modify records for a user other than the authenticated caller

## Data Classification
- PII fields: name, email, phone_number, user_id, confirmation_number
- Internal fields: model prompts, search-result traces, database credentials, API keys

## Rate Limits
- requests_per_minute: 50

