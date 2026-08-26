#!/usr/bin/env bash
# Clone Studyield's source into ./repo for local/cloud container runs.
#
# Unlike Phlox, Studyield has no published container image — its own
# docker-compose.yml builds the backend/frontend images from source. This
# script shallow-clones the repo (or fast-forwards it if already present) so
# start-local.sh / deploy-*.sh can build from it.
#
# ./repo is gitignored — it is a disposable checkout, not vendored source.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR/repo"
REPO_URL="https://github.com/studyield/studyield.git"
REF="${1:-main}"

if [[ -d "$REPO_DIR/.git" ]]; then
  echo "repo/ already cloned — fetching latest $REF..."
  git -C "$REPO_DIR" fetch --depth 1 origin "$REF"
  git -C "$REPO_DIR" checkout FETCH_HEAD
else
  echo "Cloning $REPO_URL (ref: $REF) into repo/ ..."
  git clone --depth 1 --branch "$REF" -- "$REPO_URL" "$REPO_DIR"
fi

echo "Done. Studyield source is at: $REPO_DIR"
