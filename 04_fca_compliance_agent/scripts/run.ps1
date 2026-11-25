# Run the FCA & Consumer Credit Act compliance agent on Windows (PowerShell)
# Automatically ensures `uv` is installed, syncs dependencies (including MCP / Handbook RAG extras),
# and launches the runner (which connects to or auto-bootstraps both regulatory MCP servers).
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$EnvFile = Join-Path $RepoRoot ".env"
$RunnerScript = Join-Path $RepoRoot "04_fca_compliance_agent\scripts\run_draft_test.py"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LockFile = Join-Path $RepoRoot "uv.lock"

function Ensure-Uv {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        return
    }
    foreach ($candidate in @("$env:USERPROFILE\.local\bin", "$env:USERPROFILE\.cargo\bin")) {
        if (Test-Path (Join-Path $candidate "uv.exe")) {
            $env:PATH = "$candidate;$env:PATH"
            return
        }
    }
    Write-Host "uv not found on PATH; installing uv automatically..."
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:PATH = "$env:USERPROFILE\.local\bin;$env:USERPROFILE\.cargo\bin;$env:PATH"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw "uv installation finished, but uv is still not on PATH."
    }
}

$IsHelp = ($args -contains "-h") -or ($args -contains "--help")
$HasExternalFca = [bool]$env:FCA_MCP_URL -or ($args -contains "--fca-mcp-url")

Ensure-Uv

if ((-not $IsHelp) -and (-not (Test-Path $EnvFile))) {
    Write-Warning "$EnvFile not found. Copy .env.example to .env and add your LangSmith gateway credentials."
}

Push-Location $RepoRoot
try {
    $NeedSync = $true
    $CheckExpr = if ($HasExternalFca) {
        "import fca_compliance_agent, mcp"
    } else {
        "import fca_compliance_agent, mcp, qdrant_client, fastembed"
    }
    if (($env:FORCE_UV_SYNC -ne "1") -and (Test-Path $VenvPython)) {
        & $VenvPython -c $CheckExpr 2>$null
        if ($LASTEXITCODE -eq 0) {
            $NeedSync = $false
        }
    }

    if ($NeedSync) {
        $Extras = if ($HasExternalFca) { @("--extra", "mcp") } else { @("--extra", "mcp", "--extra", "handbook-rag") }
        Write-Host "Syncing fca-compliance-agent dependencies ($($Extras -join ' ')) with uv..."
        if (Test-Path $LockFile) {
            uv sync --frozen --inexact --package fca-compliance-agent @Extras
        } else {
            uv sync --inexact --package fca-compliance-agent @Extras
        }
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    uv run --no-sync --package fca-compliance-agent python $RunnerScript @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
