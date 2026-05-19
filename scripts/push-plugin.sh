#!/usr/bin/env bash
set -e

# Visual Anchors for Terminal Logs
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}🚀 Starting Claude Code Plugin pre-flight checks...${NC}"

# 1. Validate Project Architecture Layout
if [ ! -d ".claude-plugin" ] || [ ! -f ".claude-plugin/plugin.json" ]; then
    echo -e "${RED}❌ Error: Missing .claude-plugin/plugin.json manifest structure.${NC}"
    exit 1
fi

# 2. Run Local Sync & Marketplace Verification Checks
echo -e "${YELLOW}📦 Syncing plugin marketplace catalog...${NC}"
if pnpm run sync-marketplace; then
    echo -e "${GREEN}✅ Marketplace catalog synced successfully.${NC}"
else
    echo -e "${RED}❌ Sync failed. Please check your pnpm configurations.${NC}"
    exit 1
fi

echo -e "${YELLOW}🔍 Running structural validation suite...${NC}"
if ./scripts/validate-all-plugins.sh; then
    echo -e "${GREEN}✅ Structure and schemas validated successfully.${NC}"
else
    echo -e "${RED}❌ Structural validation failed.${NC}"
    exit 1
fi

# 3. Stage & Push to Remote Repository
CURRENT_BRANCH=$(git branch --show-current)
echo -e "${YELLOW}🌿 Staging tracking changes on branch: ${CURRENT_BRANCH}...${NC}"

git add .

# Check if there are active changes to commit
if git diff-index --quiet HEAD --; then
    echo -e "${YELLOW}ℹ️ No new code changes detected to commit. Proceeding to push current branch.${NC}"
else
    git commit -m "build(plugin): automated layout validation and marketplace sync update"
fi

echo -e "${YELLOW}📤 Pushing branch upstream to origin...${NC}"
git push origin "$CURRENT_BRANCH"

echo -e "${GREEN}🎉 Complete! Paste this branch or PR URL into your Anthropic submission portal.${NC}"