# Source this file to set generation/gate knobs for each profile family.
# Usage:
#   . ./tests/apps/blissful-store/profiles/env-profiles.ps1
#   Set-BalancedEnv
#   uv run nuguard redteam --config tests/apps/blissful-store/profiles/nuguard-balanced-a.yaml -o tests/apps/blissful-store/reports/balanced-a.json

function Set-BalancedEnv {
    $env:NUGUARD_REDTEAM_PROMPT_GENERATION_VARIANTS = '2'
    $env:NUGUARD_REDTEAM_PROMPT_GENERATION_TEMPERATURE = '0.55'
    $env:NUGUARD_REDTEAM_PROMPT_GENERATION_TOP_P = '0.85'
    $env:NUGUARD_REDTEAM_PROMPT_GATE_MIN_RELEVANCE = '0.15'
    $env:NUGUARD_REDTEAM_PROMPT_GATE_MAX_SIMILARITY = '0.92'
    $env:NUGUARD_REDTEAM_PROMPT_GATE_KEEP_BEST_EFFORT = 'true'
}

function Set-StrictEnv {
    $env:NUGUARD_REDTEAM_PROMPT_GENERATION_VARIANTS = '1'
    $env:NUGUARD_REDTEAM_PROMPT_GENERATION_TEMPERATURE = '0.35'
    $env:NUGUARD_REDTEAM_PROMPT_GENERATION_TOP_P = '0.75'
    $env:NUGUARD_REDTEAM_PROMPT_GATE_MIN_RELEVANCE = '0.22'
    $env:NUGUARD_REDTEAM_PROMPT_GATE_MAX_SIMILARITY = '0.88'
    $env:NUGUARD_REDTEAM_PROMPT_GATE_KEEP_BEST_EFFORT = 'false'
}

function Set-FastEnv {
    $env:NUGUARD_REDTEAM_PROMPT_GENERATION_VARIANTS = '1'
    $env:NUGUARD_REDTEAM_PROMPT_GENERATION_TEMPERATURE = '0.40'
    $env:NUGUARD_REDTEAM_PROMPT_GATE_MIN_RELEVANCE = '0.12'
    $env:NUGUARD_REDTEAM_PROMPT_GATE_MAX_SIMILARITY = '0.90'
    $env:NUGUARD_REDTEAM_PROMPT_GATE_KEEP_BEST_EFFORT = 'true'
}
