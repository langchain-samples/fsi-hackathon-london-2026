@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
set "RUNNER_SCRIPT=%REPO_ROOT%\04_fca_compliance_agent\scripts\run_draft_test.py"

if not exist "%REPO_ROOT%\.env" (
    echo Warning: %REPO_ROOT%\.env not found. Copy .env.example to .env and add your LangSmith gateway credentials. 1>&2
)

pushd "%REPO_ROOT%"
if exist "%REPO_ROOT%\.venv" (
    uv run --no-sync --package fca-compliance-agent python "%RUNNER_SCRIPT%" %*
) else (
    uv run --package fca-compliance-agent python "%RUNNER_SCRIPT%" %*
)
set "EXIT_CODE=%ERRORLEVEL%"
popd

exit /b %EXIT_CODE%
