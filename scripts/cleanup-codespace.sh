#!/usr/bin/env bash
# Codespace disk cleanup script for NuGuard workspace

set -e

echo "=== Codespace Disk Cleanup ==="
echo "Initial disk space:"
df -h /workspaces / 2>/dev/null || df -h / 2>/dev/null || true
echo ""

echo "1. Removing test report log files older than 1 day..."
find tests/apps -type f -path '*/reports/*' -name '*.log' -mtime +0 -delete 2>/dev/null || true

echo "2. Cleaning temporary files and local caches..."
rm -rf /tmp/pytest-of-* /tmp/tmp* ~/.codex/.tmp/* ~/.npm/_npx 2>/dev/null || true
rm -rf ~/.cache/grype/db/grype-db-download* 2>/dev/null || true
rm -rf .pytest_cache .ruff_cache 2>/dev/null || true

echo "3. Cleaning package manager caches (uv, pip, npm)..."
if command -v uv >/dev/null 2>&1; then
    uv cache clean --force 2>/dev/null || uv cache clean 2>/dev/null || true
fi
if command -v pip >/dev/null 2>&1; then
    pip cache purge 2>/dev/null || true
fi
if command -v npm >/dev/null 2>&1; then
    npm cache clean --force 2>/dev/null || true
fi

echo "4. Pruning Docker containers, unused images, and build cache..."
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker system prune -f --volumes 2>/dev/null || true
    docker image prune -a -f 2>/dev/null || true
    docker builder prune -a -f 2>/dev/null || true
fi

echo "5. Cleaning APT cache..."
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get clean 2>/dev/null || true
fi

echo ""
echo "=== Cleanup Complete ==="
echo "Final disk space:"
df -h /workspaces / 2>/dev/null || df -h / 2>/dev/null || true
