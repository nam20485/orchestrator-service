#!/usr/bin/env pwsh

<#
.SYNOPSIS
    List recent published image tags and roll the running stack back to one.

.DESCRIPTION
    docker-publish.yml publishes immutable `<branch>-<run_number>` and
    `sha-<commit>` tags for orchestratorservice, webhook-receiver, and
    webhook-proxy on every push. This script lists recent successful
    docker-publish.yml runs (candidate rollback tags) and, unless -DryRun is
    set, brings the stack up pinned to a chosen tag via compose.rollback.yaml.

    See docs/rollback-runbook.md for the full procedure.

.PARAMETER Branch
    Branch whose published runs to list. Default: current git branch.

.PARAMETER Count
    Number of recent successful runs to list. Default: 10.

.PARAMETER Tag
    Explicit tag to roll back to (e.g. "main-123" or "sha-<commit>"). Skips
    the listing step and goes straight to rollback.

.PARAMETER DryRun
    Print the resolved docker compose command without running it.

.EXAMPLE
    ./scripts/rollback.ps1
    # Lists recent tags for the current branch.

.EXAMPLE
    ./scripts/rollback.ps1 -Tag main-123
    # Rolls the stack back to ghcr.io/.../*:main-123.

.NOTES
    Requires: GitHub CLI (gh, authenticated) and docker compose.
#>
[CmdletBinding()]
param(
    [Parameter()]
    [string]$Branch,

    [Parameter()]
    [int]$Count = 10,

    [Parameter()]
    [string]$Tag,

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $RepoRoot
try {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "Required command 'gh' not found in PATH."
    }

    if (-not $Tag) {
        if (-not $Branch) {
            $Branch = (git rev-parse --abbrev-ref HEAD).Trim()
        }

        Write-Host "Recent successful docker-publish.yml runs on '$Branch':" -ForegroundColor Cyan
        $runsJson = gh run list --workflow=docker-publish.yml --branch $Branch --status success `
            --limit $Count --json number,headSha,displayTitle,createdAt
        $runs = $runsJson | ConvertFrom-Json

        if (-not $runs -or $runs.Count -eq 0) {
            Write-Warning "No successful docker-publish.yml runs found for branch '$Branch'."
            exit 1
        }

        foreach ($r in $runs) {
            $shortSha = $r.headSha.Substring(0, 12)
            Write-Host ("  {0}-{1}   (sha-{2})   {3}   {4}" -f $Branch, $r.number, $shortSha, $r.createdAt, $r.displayTitle)
        }

        Write-Host ''
        Write-Host "Re-run with -Tag '<branch>-<run-number>' or -Tag 'sha-<full-commit-sha>' to roll back." -ForegroundColor Yellow
        exit 0
    }

    $env:IMAGE_TAG = $Tag
    $composeArgs = @('-f', 'compose.yaml', '-f', 'compose.rollback.yaml', 'up', '-d')

    if ($DryRun) {
        Write-Host "IMAGE_TAG=$Tag docker compose $($composeArgs -join ' ')" -ForegroundColor Yellow
        exit 0
    }

    Write-Host "Rolling back to tag '$Tag'..." -ForegroundColor Cyan
    docker compose @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up failed with exit code $LASTEXITCODE"
    }

    Write-Host "Rollback to '$Tag' complete. Verify with: docker compose ps" -ForegroundColor Green
}
finally {
    Pop-Location
}
