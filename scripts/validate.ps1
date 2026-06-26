#!/usr/bin/env pwsh
#Requires -Version 7.0
<#
.SYNOPSIS
  Run local validation checks (lint, secret scan, tests). Mirrors CI validate.yml jobs.

.PARAMETER All
  Run lint, scan, and test (default when no switch is specified).

.PARAMETER Lint
  Ruff, actionlint, compose config, Caddyfile, opencode.json, optional shellcheck.

.PARAMETER Scan
  scan-uncommitted-secrets on changed files.

.PARAMETER Test
  pytest, Pester, and bash integration scripts.
#>
[CmdletBinding()]
param(
    [switch]$All,
    [switch]$Lint,
    [switch]$Scan,
    [switch]$Test
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not ($All -or $Lint -or $Scan -or $Test)) {
    $All = $true
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Invoke-ValidateStep {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [scriptblock]$Action
    )
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "Step failed ($LASTEXITCODE): $Name"
    }
}

function Invoke-BashStep {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$ScriptPath
    )
    Invoke-ValidateStep -Name $Name -Action {
        bash $ScriptPath
    }
}

function Get-CommandOrWarn {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Warning "Skipping: '$Name' not found. Run ./scripts/install-dev-tools.ps1 or install manually."
        return $false
    }
    return $true
}

Push-Location $RepoRoot
try {
    if ($All -or $Lint) {
        Invoke-ValidateStep -Name 'ruff' -Action {
            uv run ruff check webhook_receiver tests
        }

        if (Get-CommandOrWarn 'actionlint') {
            Invoke-ValidateStep -Name 'actionlint' -Action {
                actionlint
            }
        }

        if (Get-Command docker -ErrorAction SilentlyContinue) {
            Invoke-BashStep -Name 'compose config' -ScriptPath './test/test-compose-config.sh'
            Invoke-BashStep -Name 'caddyfile' -ScriptPath './test/test-caddyfile.sh'
        }
        else {
            Write-Warning 'Skipping compose config and caddyfile: docker not available.'
        }

        Invoke-BashStep -Name 'opencode.json' -ScriptPath './test/test-opencode-json.sh'
        Invoke-BashStep -Name 'beads versions' -ScriptPath './test/test-beads-versions-consistency.sh'

        if (Get-CommandOrWarn 'shellcheck') {
            Invoke-ValidateStep -Name 'shellcheck' -Action {
                shellcheck -S error scripts/docker-entrypoint.sh
                shellcheck -S error -e SC2094 .cursor/skills/scan-uncommitted-secrets/scripts/scan.sh
            }
        }
    }

    if ($All -or $Scan) {
        Invoke-ValidateStep -Name 'secret scan' -Action {
            bash .cursor/skills/scan-uncommitted-secrets/scripts/scan.sh
        }
    }

    if ($All -or $Test) {
        Invoke-ValidateStep -Name 'pytest' -Action {
            uv run pytest tests/ -q `
                --cov=webhook_receiver `
                --cov-report=term-missing `
                --cov-report=html:htmlcov `
                --cov-report=xml:coverage.xml
        }

        Invoke-ValidateStep -Name 'pester' -Action {
            pwsh -NoProfile -File ./test/run-pester-tests.ps1
        }

        Invoke-BashStep -Name 'docker-entrypoint' -ScriptPath './test/test-docker-entrypoint.sh'
        Invoke-BashStep -Name 'secret scan (regression)' -ScriptPath './test/test-scan-secrets.sh'
        if (Get-Command docker -ErrorAction SilentlyContinue) {
            Invoke-BashStep -Name 'compose config (test)' -ScriptPath './test/test-compose-config.sh'
            Invoke-BashStep -Name 'caddyfile (test)' -ScriptPath './test/test-caddyfile.sh'
        }
        else {
            Write-Warning 'Skipping compose config and caddyfile (test): docker not available.'
        }
        Invoke-BashStep -Name 'opencode.json (test)' -ScriptPath './test/test-opencode-json.sh'
        Invoke-BashStep -Name 'beads versions (test)' -ScriptPath './test/test-beads-versions-consistency.sh'
    }

    Write-Host ""
    Write-Host "Validation passed." -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "Validation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Pop-Location
}
