#!/usr/bin/env bash
# Grant the seeded Studyield test accounts (student-alpha/beta/gamma) an
# unlimited-usage plan so NuGuard behavior/redteam runs don't get 403'd by
# the app's free-plan quota (PLAN_LIMITS.free.ai_requests = 10 in
# repo/backend/src/modules/subscription/subscription.service.ts).
#
# Postgres runs as a container in the ACI group with no persistent volume, so
# this must be re-run after every redeploy/restart (seed-users.js re-creates
# the accounts on 'free' each time; there is no billing-free API to upgrade a
# plan short of a real Stripe checkout).
#
# Usage: ./grant-test-plan.sh [resource-group] [container-group]
set -euo pipefail

RESOURCE_GROUP="${1:-demo-apps}"
CONTAINER_GROUP="${2:-studyield-nuguard-test}"

SQL="UPDATE subscriptions s SET plan='monthly', status='active' FROM users u WHERE s.user_id = u.id AND u.email LIKE '%seed@example-student.test'; SELECT u.email, s.plan, s.status FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id WHERE u.email LIKE '%seed%';"
B64=$(printf '%s' "$SQL" | base64 -w0)

# az container exec's --exec-command splits on whitespace and mangles nested
# quotes, so the multi-word remote pipeline is smuggled through as a single
# whitespace-free token using ${IFS}, which the remote shell re-expands.
CMD="sh -c echo\${IFS}${B64}\${IFS}|\${IFS}base64\${IFS}-d\${IFS}|\${IFS}psql\${IFS}-U\${IFS}postgres\${IFS}-d\${IFS}studyield_dev"

az container exec \
  --resource-group "$RESOURCE_GROUP" \
  --name "$CONTAINER_GROUP" \
  --container-name postgres \
  --exec-command "$CMD"
