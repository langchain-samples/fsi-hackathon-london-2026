@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
set "RUNNER_SCRIPT=%REPO_ROOT%\04_fca_compliance_agent\scripts\run_draft_test.py"
set "VENV_PYTHON=%REPO_ROOT%\.venv\Scripts\python.exe"

where uv >nul 2>&1
if errorlevel 1 (
    if exist "%USERPROFILE%\.local\bin\uv.exe" (
        set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    ) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
        set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
    ) else (
        echo uv not found on PATH; installing uv automatically... 1>&2
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
        where uv >nul 2>&1
        if errorlevel 1 (
            echo Error: uv installation finished, but uv is still not on PATH. 1>&2
            exit /b 1
        )
    )
)

set "IS_HELP=0"
set "HAS_EXTERNAL_FCA=0"
if not "%FCA_MCP_URL%"=="" set "HAS_EXTERNAL_FCA=1"
for %%A in (%*) do (
    if "%%~A"=="-h" set "IS_HELP=1"
    if "%%~A"=="--help" set "IS_HELP=1"
    if "%%~A"=="--fca-mcp-url" set "HAS_EXTERNAL_FCA=1"
)

if "%IS_HELP%"=="0" if not exist "%REPO_ROOT%\.env" (
    echo Warning: %REPO_ROOT%\.env not found. Copy .env.example to .env and add your LangSmith gateway credentials. 1>&2
)

pushd "%REPO_ROOT%"

set "NEED_SYNC=1"
if not "%FORCE_UV_SYNC%"=="1" if exist "%VENV_PYTHON%" (
    if "%HAS_EXTERNAL_FCA%"=="1" (
        "%VENV_PYTHON%" -c "import fca_compliance_agent, mcp" >nul 2>&1 && set "NEED_SYNC=0"
    ) else (
        "%VENV_PYTHON%" -c "import fca_compliance_agent, mcp, qdrant_client, fastembed" >nul 2>&1 && set "NEED_SYNC=0"
    )
)

if "%NEED_SYNC%"=="1" (
    if "%HAS_EXTERNAL_FCA%"=="1" (
        echo Syncing fca-compliance-agent dependencies (--extra mcp^) with uv... 1>&2
        if exist "%REPO_ROOT%\uv.lock" (
            uv sync --frozen --inexact --package fca-compliance-agent --extra mcp
        ) else (
            uv sync --inexact --package fca-compliance-agent --extra mcp
        )
    ) else (
        echo Syncing fca-compliance-agent dependencies (--extra mcp --extra handbook-rag^) with uv... 1>&2
        if exist "%REPO_ROOT%\uv.lock" (
            uv sync --frozen --inexact --package fca-compliance-agent --extra mcp --extra handbook-rag
        ) else (
            uv sync --inexact --package fca-compliance-agent --extra mcp --extra handbook-rag
        )
    )
    if errorlevel 1 (
        set "EXIT_CODE=%ERRORLEVEL%"
        popd
        exit /b %EXIT_CODE%
    )
)

uv run --no-sync --package fca-compliance-agent python "%RUNNER_SCRIPT%" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd

exit /b %EXIT_CODE%
