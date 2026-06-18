#!/usr/bin/env pwsh
#Requires -Version 7.0
<#
.SYNOPSIS
  Install optional local tools for full validate.ps1 parity with CI.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $RepoRoot
try {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        throw @'
uv is required but was not found on PATH. Install it first, then rerun this script:
  - Linux/macOS:  curl -LsSf https://astral.sh/uv/install.sh | sh
  - Windows:      powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
'@
    }

    Write-Host 'Syncing Python dev dependencies (pytest, ruff, httpx)...' -ForegroundColor Cyan
    uv sync --group dev

    if (-not (Get-Module -ListAvailable -Name Pester | Where-Object { $_.Version -ge '5.0.0' })) {
        Write-Host 'Installing Pester 5...' -ForegroundColor Cyan
        Install-Module Pester -MinimumVersion 5.0.0 -Scope CurrentUser -Force -AllowClobber
    }
    else {
        Write-Host 'Pester 5+ already installed.' -ForegroundColor Green
    }

    if (-not (Get-Command actionlint -ErrorAction SilentlyContinue)) {
        Write-Host @'
actionlint is not installed. Options:
  - Debian/Ubuntu: download from https://github.com/rhysd/actionlint/releases
  - Go: go install github.com/rhysd/actionlint/cmd/actionlint@latest
'@ -ForegroundColor Yellow
    }

    if (-not (Get-Command shellcheck -ErrorAction SilentlyContinue)) {
        Write-Host 'shellcheck: apt install shellcheck (optional lint step)' -ForegroundColor Yellow
    }

    if (-not (Get-Command jq -ErrorAction SilentlyContinue)) {
        Write-Host 'jq: apt install jq (required for test/test-opencode-json.sh)' -ForegroundColor Yellow
    }

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host 'docker: required for compose/caddy tests and CI build job' -ForegroundColor Yellow
    }

    Write-Host ''
    Write-Host 'Done. Run: pwsh -NoProfile -File ./scripts/validate.ps1 -All' -ForegroundColor Green
}
finally {
    Pop-Location
}
