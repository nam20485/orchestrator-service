#!/usr/bin/env pwsh
#Requires -Version 7.0
<#
.SYNOPSIS
  Thin docker compose wrapper that selects the published image tag via IMAGE_REF.

.DESCRIPTION
  Runs docker compose against compose.yaml, exporting the IMAGE_REF interpolation
  variable that compose.yaml uses for the image tags
  (e.g. ghcr.io/.../orchestrator-service:${IMAGE_REF:-main}-latest).

  Supported refs: main (default), development, nam20485. Each maps to the
  branch tag published by docker-publish.yml (<branch>-latest).

  Required env (set in your shell, never echoed by this script):
    WORKSPACE_DIR            host path bind-mounted to /workspace
    OPENCODE_SERVER_PASSWORD opencode server password

.EXAMPLE
  ./scripts/dc.ps1 up development
  Bring the stack up detached using the development-* images.

.EXAMPLE
  ./scripts/dc.ps1 down nam20485
  Stop and remove the nam20485 stack.

.EXAMPLE
  ./scripts/dc.ps1 logs main -f
  Tail logs for the main stack (extra args pass through to docker compose).

.EXAMPLE
  ./scripts/dc.ps1 up main --build
  Omitted ImageRef defaults to main; --build passes through.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'logs')]
    [string]$Command,

    [Parameter(Position = 1)]
    [ValidateSet('main', 'development', 'nam20485')]
    [string]$ImageRef = 'main',

    [Parameter(ValueFromRemainingArguments)]
    [string[]]$ExtraArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeFile = Join-Path $repoRoot 'compose.yaml'
if (-not (Test-Path -LiteralPath $composeFile)) {
    throw "compose.yaml not found: $composeFile"
}

$composeArgs = @('-f', $composeFile, $Command)

# 'up' defaults to detached to match the documented `up -d` usage, unless the
# caller passes a foreground/detach-conflicting flag via ExtraArgs.
if ($Command -eq 'up') {
    $foregroundFlags = @('--attach', '--abort-on-container-exit', '--wait')
    $hasForeground = $false
    if ($ExtraArgs) {
        foreach ($a in $ExtraArgs) { if ($foregroundFlags -contains $a) { $hasForeground = $true; break } }
    }
    if (-not $hasForeground) {
        $composeArgs += '-d'
    }
}
if ($ExtraArgs) {
    $composeArgs += $ExtraArgs
}

Write-Host "IMAGE_REF=$ImageRef => docker compose $Command" -ForegroundColor Cyan

# Scope IMAGE_REF to this compose invocation only; restoring (or removing) the
# prior value prevents a leaked IMAGE_REF from retargeting a later bare
# `docker compose` call in the same shell (e.g. an accidental prod pull).
$prevImageRef = $env:IMAGE_REF
$env:IMAGE_REF = $ImageRef
$exitCode = 0
try {
    & docker @composeArgs
    $exitCode = $LASTEXITCODE
} finally {
    if ($null -eq $prevImageRef) {
        Remove-Item Env:\IMAGE_REF -ErrorAction SilentlyContinue
    } else {
        $env:IMAGE_REF = $prevImageRef
    }
}
exit $exitCode
