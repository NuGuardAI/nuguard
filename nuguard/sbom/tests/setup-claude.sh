#!/bin/bash
# Setup script for Claude Code terminal mode with Microsoft Foundry
# Source this file to configure your environment: source setup-claude-foundry.sh

echo "==================================================================="
echo "  Claude Code + Microsoft Foundry Terminal Mode Setup"
echo "==================================================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration from existing claude-test.py
RESOURCE_NAME="ng-ai-foundary"  # Note: typo in original - "foundary" not "foundry"
API_KEY="${ANTHROPIC_FOUNDRY_API_KEY:?Set ANTHROPIC_FOUNDRY_API_KEY before sourcing this script}"

echo -e "${YELLOW}Setting up environment variables...${NC}"

# Enable Microsoft Foundry integration
export CLAUDE_CODE_USE_FOUNDRY=1
echo "✓ CLAUDE_CODE_USE_FOUNDRY=1"

# Set Azure resource name
export ANTHROPIC_FOUNDRY_RESOURCE="$RESOURCE_NAME"
echo "✓ ANTHROPIC_FOUNDRY_RESOURCE=$RESOURCE_NAME"

# Set API key for authentication
export ANTHROPIC_FOUNDRY_API_KEY="$API_KEY"
echo "✓ ANTHROPIC_FOUNDRY_API_KEY=****[hidden]****"

# Note: base_url and resource are mutually exclusive
# Using resource parameter (recommended for Claude Code)
# If you prefer base_url, comment out ANTHROPIC_FOUNDRY_RESOURCE and uncomment below:
# export ANTHROPIC_FOUNDRY_BASE_URL="https://${RESOURCE_NAME}.services.ai.azure.com/anthropic"
# echo "✓ ANTHROPIC_FOUNDRY_BASE_URL=$ANTHROPIC_FOUNDRY_BASE_URL"

# Set model deployment names (adjust if your deployment names differ)
export ANTHROPIC_DEFAULT_OPUS_MODEL="claude-opus-4-6"
export ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-4-6"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="claude-haiku-4-5"
echo "✓ Model configurations set"

echo ""
echo -e "${GREEN}Environment configured successfully!${NC}"
echo ""
echo "To make these settings permanent, add them to your ~/.bashrc or ~/.zshrc:"
echo ""
echo "  export CLAUDE_CODE_USE_FOUNDRY=1"
echo "  export ANTHROPIC_FOUNDRY_RESOURCE=\"$RESOURCE_NAME\""
echo "  export ANTHROPIC_FOUNDRY_API_KEY=\"$API_KEY\""
echo "  export ANTHROPIC_DEFAULT_OPUS_MODEL=\"claude-opus-4-6\""
echo "  export ANTHROPIC_DEFAULT_SONNET_MODEL=\"claude-sonnet-4-6\""
echo "  export ANTHROPIC_DEFAULT_HAIKU_MODEL=\"claude-haiku-4-5\""
echo ""
echo "Test your setup by running:"
echo "  python claude-foundry-terminal-test.py"
echo ""
claude -c
