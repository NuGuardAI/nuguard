# Maintainer update draft — not posted

Thanks for confirming the plan. I have a pinned backend-only first fixture and three
passing deterministic controls: customer isolation with an allowed-owner control,
rejection of model-supplied conversation IDs, and a ticket-confirmation interrupt
with the database ticket count unchanged. The backend builds, and the graph test
package passes. No paid model calls were made.

Two scope issues need your guidance before I claim #747's acceptance criteria:

- These tested paths preserve ownership and confirmation. They are negative controls,
  not two live exploits; I have not removed protections to manufacture findings.
- The no-LLM scan finds the agent/frameworks but emits zero TOOL nodes for the
  registered ticket/handoff implementations. A small registry-pattern adapter
  appears necessary before a meaningful tool-path campaign.

Would you welcome the first fixture as a protected negative-control baseline and
an adapter follow-up, or is there a particular upstream path you want exercised
for the two-class live evidence? MySQL/Qdrant e2e and live-model measurements are
still outstanding and will remain separate from these direct Go tests.
