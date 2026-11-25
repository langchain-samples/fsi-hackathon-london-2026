# Run the FCA & Consumer Credit Act compliance agent draft test on Windows (PowerShell)
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$EnvFile = Join-Path $RepoRoot ".env"
$RunnerScript = Join-Path $RepoRoot "04_fca_compliance_agent\scripts\run_draft_test.py"

if (-not (Test-Path $EnvFile)) {
    Write-Warning "$EnvFile not found. Copy .env.example to .env and add your LangSmith gateway credentials."
}

Push-Location $RepoRoot
try {
    if (Test-Path (Join-Path $RepoRoot ".venv")) {
        uv run --no-sync --package fca-compliance-agent python $RunnerScript @args
    } else {
        uv run --package fca-compliance-agent python $RunnerScript @args
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
