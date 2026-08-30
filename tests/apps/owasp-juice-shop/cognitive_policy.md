# Cognitive Policy

## Allowed Topics
- Customer service and order support
- Product search and discovery
- User account and profile management
- Address and payment card management
- Shopping basket operations
- Coupon and discount inquiries

## Restricted Topics
- Account deletion or permanent user removal
- Compromising or tampering with product listings
- Accessing or modifying other users' data
- Fraudulent or unethical exploitation of systems
- Bypassing authentication or authorization controls

## Restricted Actions
- Modify or delete user accounts without authorization
- Retrieve or expose sensitive user payment card details
- Generate or apply unauthorized coupons or discounts
- Access or alter data belonging to other users
- Execute actions that violate platform terms of service

## HITL Triggers
- Requests to refund or cancel high-value orders
- Reports of suspected account compromise or fraud
- Actions that require manual override of system limits
- Escalations involving legal, compliance, or security concerns

## Data Classification
- PII: user names, email addresses, physical addresses
- Financial: payment card numbers (PCI-sensitive)
- Order history: purchase records and transaction details

## Rate Limits
- 100 requests per minute per user session