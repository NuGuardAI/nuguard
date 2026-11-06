# Service status interpretation

Service status data in this repository is generated for portfolio demonstrations.

## Status definitions

- `operational`: automated checks show no current customer impact.
- `degraded`: the service is working with slower or partial behavior.
- `outage`: a critical path is unavailable.

## Subscription synchronization

The `subscription-sync` service moves confirmed billing changes into active account plans. A degraded state may delay activation without changing the underlying payment result. Support should combine service status with account and payment facts rather than treating an incident as proof of an individual account outcome.

## Communication guidance

State what is known, describe the generated incident impact scope, and give a bounded expectation only when it is supported by the relevant guide. Do not invent a root cause, recovery time, or completed remediation.

P0 and P1 cases, conflicting evidence, and partial tool failures always require human review before a final customer response.
