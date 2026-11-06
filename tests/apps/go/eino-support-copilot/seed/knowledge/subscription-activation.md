# Subscription activation guide

## Activation timing

After a generated payment reaches `paid`, a Pro subscription can remain in `processing` for up to fifteen minutes while the subscription-sync service applies the change. The customer should not be charged again during this window.

## Synchronization delays

When subscription-sync is degraded, activation may take longer. Confirm the payment, account state, and service incident before answering. P0 and P1 cases require human review before the draft is sent.

## Status meanings

- `processing`: payment is confirmed and activation is still being applied.
- `active`: the paid plan is available.
- `failed`: a support agent must inspect the generated account before suggesting another payment.
