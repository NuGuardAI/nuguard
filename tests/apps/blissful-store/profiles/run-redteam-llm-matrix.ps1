param(
    [ValidateSet('balanced-a','balanced-b','strict-a','strict-b','fast-a','fast-b')]
    [string]$Profile = 'balanced-a',

    [string]$SbomPath = '',

    [string]$OutputDir = 'tests/apps/blissful-store/reports',

    [string]$SummaryPath = '',

    [switch]$PerScenario,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path $Path)) {
        return
    }

    Get-Content $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith('#')) {
            return
        }

        $parts = $line -split '=', 2
        if ($parts.Count -ne 2) {
            return
        }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim()

        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

function Require-Env([string]$Name, [string]$Hint) {
    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if (-not $value) {
        throw "Missing required env var '$Name'. $Hint"
    }
    return $value
}

function Set-EnvProfile([string]$Name) {
    . "$PSScriptRoot/env-profiles.ps1"

    switch ($Name) {
        'balanced-a' { Set-BalancedEnv; return 'tests/apps/blissful-store/profiles/nuguard-balanced-a.yaml' }
        'balanced-b' { Set-BalancedEnv; return 'tests/apps/blissful-store/profiles/nuguard-balanced-b.yaml' }
        'strict-a' { Set-StrictEnv; return 'tests/apps/blissful-store/profiles/nuguard-strict-a.yaml' }
        'strict-b' { Set-StrictEnv; return 'tests/apps/blissful-store/profiles/nuguard-strict-b.yaml' }
        'fast-a' { Set-FastEnv; return 'tests/apps/blissful-store/profiles/nuguard-fast-a.yaml' }
        'fast-b' { Set-FastEnv; return 'tests/apps/blissful-store/profiles/nuguard-fast-b.yaml' }
        default { throw "Unsupported profile '$Name'." }
    }
}

function Resolve-DefaultSbomPath() {
    $candidates = Get-ChildItem -Path 'tests/apps/blissful-store/reports' -File -Filter 'blissful-store-sbom-fullfixture-*.json' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending

    if ($candidates -and $candidates.Count -gt 0) {
        return $candidates[0].FullName.Replace((Get-Location).Path + '\\', '').Replace('\\', '/')
    }

    throw "No SBOM path provided and no full-fixture SBOM was found in tests/apps/blissful-store/reports. Generate one with: uv run nuguard sbom generate --source tests/apps/blissful-store --format json -o tests/apps/blissful-store/reports/blissful-store-sbom-fullfixture-<timestamp>.json"
}

function Assert-SbomScope([string]$Path) {
    try {
        $json = Get-Content -Raw -Path $Path | ConvertFrom-Json
        $target = [string]$json.target

        if ($target -match '[/\\]tests[/\\]apps[/\\]blissful-store[/\\]webapp$') {
            throw "SBOM appears to be webapp-only ($target). Use a full-fixture SBOM generated from --source tests/apps/blissful-store."
        }
    }
    catch {
        throw "Unable to validate SBOM target for '$Path': $($_.Exception.Message)"
    }
}

function Get-RunMetrics([string]$OutputPath, [int]$ExitCode, [bool]$IsDryRun) {
    $metrics = @{
        findings = 0
        high = 0
        medium = 0
        low = 0
        inject_success_findings = 0
        meta_llm = ''
    }

    if ($IsDryRun -or $ExitCode -ne 0 -or -not (Test-Path $OutputPath)) {
        return $metrics
    }

    try {
        $json = Get-Content -Raw -Path $OutputPath | ConvertFrom-Json
        if ($json.findings) {
            $findings = @($json.findings)
            $metrics.findings = $findings.Count
            $metrics.high = @($findings | Where-Object { $_.severity -eq 'high' }).Count
            $metrics.medium = @($findings | Where-Object { $_.severity -eq 'medium' }).Count
            $metrics.low = @($findings | Where-Object { $_.severity -eq 'low' }).Count
            $metrics.inject_success_findings = @($findings | Where-Object { $_.finding_id -like 'inject-success-*' }).Count
        }
        if ($json._meta -and $json._meta.llm) {
            $metrics.meta_llm = (@($json._meta.llm) -join ' | ')
        }
    }
    catch {
        Write-Warning "Failed to parse output JSON for ${OutputPath}: $($_.Exception.Message)"
    }

    return $metrics
}

Import-DotEnv '.env'
Import-DotEnv 'tests/apps/blissful-store/.env'

if (-not $SbomPath) {
    $SbomPath = Resolve-DefaultSbomPath
}

if (-not (Test-Path $SbomPath)) {
    throw "SBOM file not found: $SbomPath"
}

Assert-SbomScope -Path $SbomPath

$configPath = Set-EnvProfile -Name $Profile
if (-not (Test-Path $configPath)) {
    throw "Profile config not found: $configPath"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
New-Item -ItemType Directory -Force -Path 'tmp' | Out-Null

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$tempConfig = "tmp/redteam-llm-matrix-$Profile-$stamp.yaml"

# Keep the profile fixed and only override the SBOM path for apples-to-apples model comparison.
$configRaw = Get-Content -Raw -Path $configPath
$configPatched = [regex]::Replace($configRaw, '(?m)^sbom:\s+.*$', "sbom: $SbomPath")
Set-Content -Path $tempConfig -Value $configPatched

$matrix = @(
    @{
        Label = 'gemini-3.1-flash-lite-preview'
        Model = 'gemini/gemini-3.1-flash-lite-preview'
        ApiKeyVar = 'GEMINI_API_KEY'
        ApiBaseVar = ''
    },
    @{
        Label = 'azure-gpt-4.1'
        Model = 'azure/gpt-4.1'
        ApiKeyVar = 'AZURE_API_KEY'
        ApiBaseVar = 'AZURE_OPENAI_ENDPOINT'
    },
    @{
        Label = 'claude-sonnet-4.5'
        Model = 'claude-sonnet-4-5'
        ApiKeyVar = 'AZURE_ANTHROPIC_KEY'
        ApiBaseVar = 'AZURE_ANTHROPIC_ENDPOINT'
    }
)

$rows = @()
$scenarioRows = @()
$failed = $false

$scenarios = @(
    'prompt-injection',
    'tool-abuse',
    'privilege-escalation',
    'data-exfiltration',
    'policy-violation',
    'mcp-toxic-flow'
)

Write-Host "PROFILE=$Profile"
Write-Host "CONFIG=$configPath"
Write-Host "TEMP_CONFIG=$tempConfig"
Write-Host "SBOM=$SbomPath"

foreach ($entry in $matrix) {
    $label = $entry.Label
    $model = $entry.Model
    $apiKey = Require-Env -Name $entry.ApiKeyVar -Hint "Set it in .env before running this matrix."

    $apiBase = ''
    if ($entry.ApiBaseVar) {
        $apiBase = Require-Env -Name $entry.ApiBaseVar -Hint "Set it in .env before running this matrix."
    }

    # Force placeholder expansion used by the profile file.
    $env:AZURE_ANTHROPIC_MODEL_NAME = $model
    $env:AZURE_ANTHROPIC_KEY = $apiKey

    # Also set explicit redteam env overrides for compatibility with config precedence paths.
    $env:NUGUARD_REDTEAM_LLM_MODEL = $model
    $env:NUGUARD_REDTEAM_EVAL_LLM_MODEL = $model
    $env:NUGUARD_REDTEAM_LLM_API_KEY = $apiKey
    $env:NUGUARD_REDTEAM_EVAL_LLM_API_KEY = $apiKey

    if ($apiBase) {
        $env:AZURE_ANTHROPIC_ENDPOINT = $apiBase
        $env:NUGUARD_REDTEAM_LLM_API_BASE = $apiBase
        $env:NUGUARD_REDTEAM_EVAL_LLM_API_BASE = $apiBase
    }
    else {
        $env:AZURE_ANTHROPIC_ENDPOINT = ''
        Remove-Item Env:NUGUARD_REDTEAM_LLM_API_BASE -ErrorAction SilentlyContinue
        Remove-Item Env:NUGUARD_REDTEAM_EVAL_LLM_API_BASE -ErrorAction SilentlyContinue
    }

    if ($PerScenario) {
        $llmExitCode = 0
        $llmSeconds = 0.0
        $llmFindings = 0
        $llmHigh = 0
        $llmMedium = 0
        $llmLow = 0
        $llmInjectSuccess = 0
        $llmMeta = ''

        foreach ($scenario in $scenarios) {
            $scenarioOut = Join-Path $OutputDir "redteam-matrix-$Profile-$label-$scenario-$stamp.json"
            Write-Host "RUN=$label MODEL=$model SCENARIO=$scenario OUT=$scenarioOut"

            $scenarioExit = 0
            $scenarioSeconds = 0.0

            if ($DryRun) {
                uv run python -c "from nuguard.config import load_config; c=load_config(r'$tempConfig'); print(c.redteam_llm_model, c.redteam_eval_llm_model)"
                $scenarioExit = $LASTEXITCODE
            }
            else {
                $swScenario = [System.Diagnostics.Stopwatch]::StartNew()
                uv run nuguard redteam --config $tempConfig --scenarios $scenario -o $scenarioOut
                $scenarioExit = $LASTEXITCODE
                $swScenario.Stop()
                $scenarioSeconds = [math]::Round($swScenario.Elapsed.TotalSeconds, 2)
            }

            $scenarioMetrics = Get-RunMetrics -OutputPath $scenarioOut -ExitCode $scenarioExit -IsDryRun $DryRun

            if ($scenarioExit -ne 0) {
                $llmExitCode = $scenarioExit
                $failed = $true
            }

            $llmSeconds = [math]::Round(($llmSeconds + $scenarioSeconds), 2)
            $llmFindings += [int]$scenarioMetrics.findings
            $llmHigh += [int]$scenarioMetrics.high
            $llmMedium += [int]$scenarioMetrics.medium
            $llmLow += [int]$scenarioMetrics.low
            $llmInjectSuccess += [int]$scenarioMetrics.inject_success_findings
            if (-not $llmMeta -and $scenarioMetrics.meta_llm) {
                $llmMeta = $scenarioMetrics.meta_llm
            }

            $scenarioRows += [PSCustomObject]@{
                llm_label = $label
                model = $model
                scenario = $scenario
                exit_code = $scenarioExit
                duration_seconds = $scenarioSeconds
                findings = [int]$scenarioMetrics.findings
                high = [int]$scenarioMetrics.high
                medium = [int]$scenarioMetrics.medium
                low = [int]$scenarioMetrics.low
                inject_success_findings = [int]$scenarioMetrics.inject_success_findings
                meta_llm = $scenarioMetrics.meta_llm
                output = $scenarioOut
            }
        }

        $rows += [PSCustomObject]@{
            llm_label = $label
            model = $model
            exit_code = $llmExitCode
            duration_seconds = $llmSeconds
            findings = $llmFindings
            high = $llmHigh
            medium = $llmMedium
            low = $llmLow
            inject_success_findings = $llmInjectSuccess
            meta_llm = $llmMeta
            output = "redteam-matrix-$Profile-$label-<scenario>-$stamp.json"
        }
    }
    else {
        $outFile = Join-Path $OutputDir "redteam-matrix-$Profile-$label-$stamp.json"
        Write-Host "RUN=$label MODEL=$model OUT=$outFile"

        $exitCode = 0
        $seconds = 0.0

        if ($DryRun) {
            uv run python -c "from nuguard.config import load_config; c=load_config(r'$tempConfig'); print(c.redteam_llm_model, c.redteam_eval_llm_model)"
            $exitCode = $LASTEXITCODE
        }
        else {
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            uv run nuguard redteam --config $tempConfig -o $outFile
            $exitCode = $LASTEXITCODE
            $sw.Stop()
            $seconds = [math]::Round($sw.Elapsed.TotalSeconds, 2)
        }

        $metrics = Get-RunMetrics -OutputPath $outFile -ExitCode $exitCode -IsDryRun $DryRun

        if ($exitCode -ne 0) {
            $failed = $true
        }

        $rows += [PSCustomObject]@{
            llm_label = $label
            model = $model
            exit_code = $exitCode
            duration_seconds = $seconds
            findings = [int]$metrics.findings
            high = [int]$metrics.high
            medium = [int]$metrics.medium
            low = [int]$metrics.low
            inject_success_findings = [int]$metrics.inject_success_findings
            meta_llm = $metrics.meta_llm
            output = $outFile
        }
    }
}

if (-not $SummaryPath) {
    $SummaryPath = Join-Path $OutputDir "redteam-llm-matrix-$Profile-$stamp.md"
}

$generatedAt = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')

$lines = @()
$lines += "# Redteam LLM Matrix Summary"
$lines += ""
$lines += "- Generated at: $generatedAt"
$lines += "- Profile: $Profile"
$lines += "- Base profile config: $configPath"
$lines += "- SBOM (fixed for all runs): $SbomPath"
$lines += "- Temp config used: $tempConfig"
$lines += "- Per-scenario mode: $PerScenario"
$lines += ""
$lines += "## Results"
$lines += ""
$lines += "| LLM | Model | Exit | Duration (s) | Findings | High | Medium | Low | Inject-Success Findings | Meta LLM | Output |"
$lines += "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|"

foreach ($r in $rows) {
    $lines += "| $($r.llm_label) | $($r.model) | $($r.exit_code) | $($r.duration_seconds) | $($r.findings) | $($r.high) | $($r.medium) | $($r.low) | $($r.inject_success_findings) | $($r.meta_llm) | $($r.output) |"
}

if ($PerScenario) {
    $lines += ""
    $lines += "## Per-Scenario Results"
    $lines += ""
    $lines += "| LLM | Scenario | Exit | Duration (s) | Findings | High | Medium | Low | Inject-Success Findings | Output |"
    $lines += "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"

    foreach ($sr in $scenarioRows) {
        $lines += "| $($sr.llm_label) | $($sr.scenario) | $($sr.exit_code) | $($sr.duration_seconds) | $($sr.findings) | $($sr.high) | $($sr.medium) | $($sr.low) | $($sr.inject_success_findings) | $($sr.output) |"
    }
}

$lines += ""
$lines += "## Notes"
$lines += ""
$lines += "- Same profile and SBOM are used for all LLMs to keep the comparison fair."
$lines += "- Exit code 0 means the run completed and output JSON was produced."
$lines += "- Inject-Success Findings counts finding IDs prefixed with inject-success-."
$lines += "- In per-scenario mode, aggregate rows are sums across scenario runs for each LLM."

Set-Content -Path $SummaryPath -Value $lines

Write-Host "SUMMARY=$SummaryPath"

if ($failed) {
    exit 1
}

exit 0
