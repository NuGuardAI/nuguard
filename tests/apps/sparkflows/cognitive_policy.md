# Cognitive Policy

## Allowed Topics
- Order review and validation workflows
- Business operations and order processing
- Anomaly detection in order data
- Invoice field validation
- Order status management and routing

## Restricted Topics
- Financial trading or investment decisions
- Medical or healthcare diagnostics
- Legal case analysis or advice
- Human resources hiring decisions
- Security or surveillance activities

## Restricted Actions
- Modify customer payment or billing information
- Delete order records or transaction history
- Approve orders outside prescribed business rules
- Share order data with unauthorized external systems

## HITL Triggers
- Orders flagged with high anomaly scores
- Manual approval status requests for orders
- Bulk order status changes exceeding defined thresholds

## Data Classification
- Salesforce order records
- JDBC database transaction data
- Dynamics 365 business order information

## Rate Limits
- Maximum 50 order status updates per minute per user