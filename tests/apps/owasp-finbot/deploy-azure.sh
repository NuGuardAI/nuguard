#!/usr/bin/env bash
# Deploy OWASP FinBot to Azure Container Instances (resource group demo-apps),
# mirroring owasp-juice-shop / owasp-vulnerableapp: public IP + DNS label.
#
# FinBot needs Redis, so this deploys a two-container group (app + redis sidecar,
# shared localhost). SQLite is used for the DB (ephemeral; resets on restart). Redis is pulled from the same ACR (import: az acr import --source public.ecr.aws/docker/library/redis:7-alpine) to avoid Docker Hub rate limits.
#
# Usage: deploy-azure.sh [--build] [--delete]
#   --build   Build/push the image to ACR first (az acr build from the checkout).
#   --delete  Delete the container group and exit.
#
# Env (or tests/apps/owasp-finbot/.env): OPENAI_API_KEY (optional; AI challenges
# need it). Only that key is read from .env — the file is not sourced wholesale.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RG="${RG:-demo-apps}"
LOCATION="${LOCATION:-eastus2}"
ACR="${ACR:-nuguardvulnerableappacr}"
NAME="${NAME:-owasp-finbot}"
DNS_LABEL="${DNS_LABEL:-finbot-demo}"
IMAGE="$ACR.azurecr.io/owasp-finbot:latest"
SRC_DIR="${SRC_DIR:-$SCRIPT_DIR/tmp/finbot-ctf}"
FQDN="$DNS_LABEL.$LOCATION.azurecontainer.io"

die() { echo "ERROR: $*" >&2; exit 1; }

BUILD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --build) BUILD=1; shift ;;
    --delete)
      az container delete -g "$RG" -n "$NAME" --yes
      exit 0 ;;
    -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

command -v az >/dev/null 2>&1 || die "Azure CLI (az) is required."
command -v openssl >/dev/null 2>&1 || die "openssl is required."

if [[ -z "${OPENAI_API_KEY:-}" && -f "$SCRIPT_DIR/.env" ]]; then
  OPENAI_API_KEY="$(sed -n 's/^OPENAI_API_KEY=//p' "$SCRIPT_DIR/.env" | tail -n1 | tr -d "\"'")"
fi
[[ -n "${OPENAI_API_KEY:-}" ]] || echo "WARNING: OPENAI_API_KEY not set — FinBot AI challenges will not work." >&2

if [[ $BUILD -eq 1 ]]; then
  [[ -f "$SRC_DIR/Dockerfile" ]] || die "$SRC_DIR is not a FinBot checkout (run deploy-local.sh once to clone it)."
  az acr build -r "$ACR" -t owasp-finbot:latest --target app "$SRC_DIR"
fi

ACR_PW="$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv)"
SECRET_KEY="$(openssl rand -hex 32)"

# Secrets live only in this 0600 temp file, removed on exit.
umask 077
YAML="$(mktemp "${TMPDIR:-/tmp}/finbot-aci.XXXXXX.yaml")"
trap 'rm -f "$YAML"' EXIT

cat > "$YAML" <<EOF
apiVersion: '2023-05-01'
location: $LOCATION
name: $NAME
type: Microsoft.ContainerInstance/containerGroups
properties:
  osType: Linux
  restartPolicy: Always
  imageRegistryCredentials:
  - server: $ACR.azurecr.io
    username: $ACR
    password: $ACR_PW
  ipAddress:
    type: Public
    dnsNameLabel: $DNS_LABEL
    ports:
    - protocol: tcp
      port: 8000
  containers:
  - name: owasp-finbot
    properties:
      image: $IMAGE
      ports:
      - port: 8000
      resources:
        requests:
          cpu: 1.0
          memoryInGB: 2.0
      environmentVariables:
      - {name: PORT, value: '8000'}
      - {name: HOST, value: 0.0.0.0}
      - {name: WEB_CONCURRENCY, value: '2'}
      - {name: DATABASE_TYPE, value: sqlite}
      - {name: DATABASE_URL, value: 'sqlite://data/finbot.db'}
      - {name: REDIS_URL, value: 'redis://localhost:6379'}
      - {name: DEBUG, value: 'true'}
      - {name: EMAIL_PROVIDER, value: console}
      - {name: MAGIC_LINK_BASE_URL, value: 'http://$FQDN:8000'}
      - {name: SECRET_KEY, secureValue: '$SECRET_KEY'}
      - {name: OPENAI_API_KEY, secureValue: '${OPENAI_API_KEY:-}'}
  - name: redis
    properties:
      image: $ACR.azurecr.io/redis:7-alpine
      resources:
        requests:
          cpu: 0.5
          memoryInGB: 0.5
EOF

az container create -g "$RG" --file "$YAML" \
  --query '{state:provisioningState,fqdn:ipAddress.fqdn,ip:ipAddress.ip}' -o json

echo "FinBot: http://$FQDN:8000  (set target.url in nuguard-azure.yaml)"
echo "Logs:   az container logs -g $RG -n $NAME --container-name $NAME"
