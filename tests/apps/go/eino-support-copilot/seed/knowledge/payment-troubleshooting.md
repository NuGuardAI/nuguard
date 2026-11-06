# Payment troubleshooting

This document applies only to the generated DemoCloud dataset bundled with the project.

## Payment states

- `pending`: the payment provider has not confirmed a final outcome.
- `paid`: funds were confirmed and the linked subscription can begin synchronization.
- `failed`: the attempt did not complete and no activation should be inferred.
- `refunded`: the original payment was reversed.

## Investigation flow

Start with the account ID supplied by the customer. Query the latest payment and compare its `linked_subscription` with the account plan. When a payment ID is available, pass it to the payment tool to avoid ambiguity.

Never ask for or expose a full card number, security code, bank credential, or other sensitive payment data. The read-only payment tool intentionally returns only a generated payment ID, state, amount, currency, payment time, and linked subscription.

## Escalation policy

Escalate for human review when a customer reports duplicate charges, the available facts conflict, or a high-priority account remains past due after a confirmed payment. A draft response must distinguish observed tool facts from recommended next steps.
