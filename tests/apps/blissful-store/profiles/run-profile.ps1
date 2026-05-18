param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('balanced-a','balanced-b','strict-a','strict-b','fast-a','fast-b')]
    [string]$Profile,

    [ValidateSet('patrick','new_customer','high_value','anonymous')]
    [string]$CustomerProfile,

    [string]$OutputPath = '',

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

. "$PSScriptRoot/env-profiles.ps1"

if ($CustomerProfile) {
    Set-BlissfulCustomerProfile -CustomerProfile $CustomerProfile
}

switch ($Profile) {
    'balanced-a' {
        Set-BalancedEnv
        $config = 'tests/apps/blissful-store/profiles/nuguard-balanced-a.yaml'
        $defaultOut = 'tests/apps/blissful-store/reports/profile-balanced-a.json'
    }
    'balanced-b' {
        Set-BalancedEnv
        $config = 'tests/apps/blissful-store/profiles/nuguard-balanced-b.yaml'
        $defaultOut = 'tests/apps/blissful-store/reports/profile-balanced-b.json'
    }
    'strict-a' {
        Set-StrictEnv
        $config = 'tests/apps/blissful-store/profiles/nuguard-strict-a.yaml'
        $defaultOut = 'tests/apps/blissful-store/reports/profile-strict-a.json'
    }
    'strict-b' {
        Set-StrictEnv
        $config = 'tests/apps/blissful-store/profiles/nuguard-strict-b.yaml'
        $defaultOut = 'tests/apps/blissful-store/reports/profile-strict-b.json'
    }
    'fast-a' {
        Set-FastEnv
        $config = 'tests/apps/blissful-store/profiles/nuguard-fast-a.yaml'
        $defaultOut = 'tests/apps/blissful-store/reports/profile-fast-a.json'
    }
    'fast-b' {
        Set-FastEnv
        $config = 'tests/apps/blissful-store/profiles/nuguard-fast-b.yaml'
        $defaultOut = 'tests/apps/blissful-store/reports/profile-fast-b.json'
    }
}

if (-not $OutputPath) {
    $OutputPath = $defaultOut
}

Write-Host "PROFILE=$Profile"
Write-Host "CONFIG=$config"
Write-Host "OUTPUT=$OutputPath"
Write-Host "CUSTOMER_PROFILE=$env:BLISSFUL_CUSTOMER_PROFILE"
Write-Host "PROMPT_VARIANTS=$env:NUGUARD_REDTEAM_PROMPT_GENERATION_VARIANTS"
Write-Host "PROMPT_TEMP=$env:NUGUARD_REDTEAM_PROMPT_GENERATION_TEMPERATURE"
Write-Host "PROMPT_TOP_P=$env:NUGUARD_REDTEAM_PROMPT_GENERATION_TOP_P"
Write-Host "GATE_MIN_RELEVANCE=$env:NUGUARD_REDTEAM_PROMPT_GATE_MIN_RELEVANCE"
Write-Host "GATE_MAX_SIMILARITY=$env:NUGUARD_REDTEAM_PROMPT_GATE_MAX_SIMILARITY"
Write-Host "GATE_KEEP_BEST_EFFORT=$env:NUGUARD_REDTEAM_PROMPT_GATE_KEEP_BEST_EFFORT"

if ($DryRun) {
    uv run python -c "from nuguard.config import load_config; c=load_config(r'$config'); print('OK', c.redteam_profile, c.redteam_guided_max_turns, c.redteam_guided_concurrency, c.redteam_request_timeout)"
    exit $LASTEXITCODE
}

uv run nuguard redteam --config $config -o $OutputPath
exit $LASTEXITCODE
