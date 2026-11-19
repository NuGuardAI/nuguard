#!/usr/bin/env bash
# Local OWASP FinBot: https://github.com/GenAI-Security-Project/finbot-ctf
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<HELP
Usage: $(basename "$0") [--docker|--source] [--port PORT]

  --docker      Build/run upstream Compose (requires Compose >= 2.24.4).
  --source      Install with uv, initialize the database, run the Python app.
  --port PORT   Localhost port (default: PORT from environment/.env, or 8000).

Auto mode uses Docker when its daemon and Compose are available, otherwise source.
Set OPENAI_API_KEY in the environment or this directory's .env for AI challenges.
Source mode needs Redis at REDIS_URL (default: redis://localhost:6379).
SRC_DIR and REF override the checkout location and initial branch (default: main).
Existing checkouts are reused without pulling or resetting local changes.
HELP
}

die() { echo "ERROR: $*" >&2; exit 1; }

# Help and argument errors work without credentials or dependencies.
MODE=auto
CLI_PORT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --docker) MODE=docker; shift ;;
    --source) MODE=source; shift ;;
    --port)
      [[ $# -ge 2 ]] || die "--port requires a value."
      CLI_PORT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown argument: $1 (see --help)." ;;
  esac
done

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.env"
  set +a
fi

SRC_DIR="${SRC_DIR:-$SCRIPT_DIR/tmp/finbot-ctf}"
REF="${REF:-main}"
PORT="${CLI_PORT:-${PORT:-8000}}"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] || die "Port must be an integer from 1 to 65535."
PORT=$((10#$PORT))
(( PORT >= 1 && PORT <= 65535 )) || die "Port must be between 1 and 65535."
export PORT
export MAGIC_LINK_BASE_URL="${MAGIC_LINK_BASE_URL:-http://127.0.0.1:$PORT}"

docker_ready() {
  command -v docker >/dev/null 2>&1 &&
    docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

if [[ "$MODE" == auto ]]; then
  if docker_ready; then MODE=docker; else MODE=source; fi
fi
if [[ "$MODE" == docker ]]; then
  docker_ready || die "Docker mode requires a running Docker daemon and Docker Compose."
else
  command -v uv >/dev/null 2>&1 || die "Source mode requires uv: https://docs.astral.sh/uv/"
fi

if [[ ! -d "$SRC_DIR/.git" ]]; then
  command -v git >/dev/null 2>&1 || die "Git is required to clone FinBot."
  mkdir -p "$(dirname "$SRC_DIR")"
  git clone --depth 1 --branch "$REF" https://github.com/GenAI-Security-Project/finbot-ctf.git "$SRC_DIR"
fi
SRC_DIR="$(cd "$SRC_DIR" && pwd)"
[[ -f "$SRC_DIR/run.py" && -f "$SRC_DIR/docker-compose.yml" ]] || die "$SRC_DIR is not a FinBot checkout."

# Upstream Compose requires .env even when credentials are supplied externally.
if [[ ! -f "$SRC_DIR/.env" ]]; then
  (umask 077; cp "$SRC_DIR/.env.example" "$SRC_DIR/.env")
fi
ENV_FILE="$SRC_DIR/.env"
[[ ! -f "$SCRIPT_DIR/.env" ]] || ENV_FILE="$SCRIPT_DIR/.env"

echo "Starting OWASP FinBot at http://127.0.0.1:$PORT ($MODE)"
if [[ "$MODE" == docker ]]; then
  # !override replaces upstream's wildcard port binding (Compose >= 2.24.4).
  OVERRIDE_FILE="$(mktemp "${TMPDIR:-/tmp}/finbot-compose.XXXXXX.yaml")"
  trap 'rm -f "$OVERRIDE_FILE"' EXIT
  cat > "$OVERRIDE_FILE" <<'YAML'
services:
  app:
    ports: !override
      - "127.0.0.1:${PORT}:8000"
    env_file: !override
      - ${FINBOT_ENV_FILE}
    environment:
      PORT: "8000"
      MAGIC_LINK_BASE_URL: ${MAGIC_LINK_BASE_URL}
  postgres:
    ports: !override
      - "127.0.0.1:${POSTGRES_PORT:-5432}:5432"
YAML
  # Shell credentials override env_file, without clearing keys stored only there.
  if [[ -v OPENAI_API_KEY ]]; then
    # Write the interpolation expression, never the key itself.
    sed -i '/      PORT: "8000"/a\      OPENAI_API_KEY: ${OPENAI_API_KEY}' "$OVERRIDE_FILE"
  fi
  export FINBOT_ENV_FILE="$ENV_FILE"
  compose=(docker compose --project-directory "$SRC_DIR" --env-file "$ENV_FILE"
    -f "$SRC_DIR/docker-compose.yml" -f "$OVERRIDE_FILE")
  database_type="${DATABASE_TYPE:-}"
  if [[ -z "$database_type" ]]; then
    database_type="$("${compose[@]}" config --environment | sed -n 's/^DATABASE_TYPE=//p')"
  fi
  [[ "$database_type" != postgresql ]] || compose+=(--profile postgres)
  "${compose[@]}" up --build
else
  cd "$SRC_DIR"
  export HOST=127.0.0.1
  uv sync --frozen
  uv run python scripts/db.py setup
  exec uv run python run.py
fi
