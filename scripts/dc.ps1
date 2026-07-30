#!/usr/bin/env pwsh
#Requires -Version 7.0
<<<<<<< HEAD
=======
<#
.SYNOPSIS
  Thin docker compose wrapper that selects the published image tag via IMAGE_REF.

.DESCRIPTION
  Runs docker compose against compose.yaml, exporting the IMAGE_REF interpolation
  variable that compose.yaml uses for the image tags
  (e.g. ghcr.io/.../orchestrator-service:${IMAGE_REF:-main}-latest).


  Command abbreviations are accepted: u=up, d=down, l=logs.
  Supported refs: main (default), development, nam20485. Each maps to the
  branch tag published by docker-publish.yml (<branch>-latest).

  Command abbreviations are accepted: u=up, d=down, l=logs.

  For `up`, the wrapper always passes `--pull always` so a stale local image
  cache can never shadow the current <branch>-latest tag. Pass `--build`
  explicitly via ExtraArgs to rebuild the image locally instead.

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
  Build all images from local Dockerfiles (layers compose.build.yaml) and start the stack.
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet('up', 'down', 'logs', 'u', 'd', 'l')]
  [string]$Command,

  [Parameter(Position = 1)]
  [ValidateSet('main', 'development', 'nam20485')]
  [string]$ImageRef = 'main',

  [Parameter(ValueFromRemainingArguments)]
  [string[]]$ExtraArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Normalize abbreviated command aliases (u/d/l) to their canonical forms so the
# rest of the script only deals with 'up', 'down', 'logs'.
switch ($Command) {
  'u' { $Command = 'up' }
  'd' { $Command = 'down' }
  'l' { $Command = 'logs' }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeFile = Join-Path $repoRoot 'compose.yaml'
if (-not (Test-Path -LiteralPath $composeFile)) {
  throw "compose.yaml not found: $composeFile"
}

$hasBuild = $false
if ($ExtraArgs) {
  foreach ($a in $ExtraArgs) { if ($a -eq '--build') { $hasBuild = $true; break } }
}

$composeArgs = @('compose', '-f', $composeFile)
if ($hasBuild) {
  $composeArgs += '-f', (Join-Path $repoRoot 'compose.build.yaml')
}
$composeArgs += $Command

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
  # Always pull the freshest published image for the chosen ref so a stale
  # local cache can never shadow the current <branch>-latest tag. Skip this
  # in --build mode (compose.build.yaml sets pull_policy: never) so the
  # locally-built image is used as-is.
  if (-not $hasBuild) {
    $composeArgs += '--pull', 'always'
  }
}
if ($ExtraArgs) {
  $composeArgs += $ExtraArgs
}

$buildTag = if ($hasBuild) { ' [local build]' } else { '' }
$commandLine = "IMAGE_REF=$ImageRef => docker compose $Command$buildTag"
Write-Host $commandLine -ForegroundColor Cyan

# Scope IMAGE_REF to this compose invocation only; restoring (or removing) the
# prior value prevents a leaked IMAGE_REF from retargeting a later bare
# `docker compose` call in the same shell (e.g. an accidental prod pull).
$prevImageRef = $env:IMAGE_REF
$env:IMAGE_REF = $ImageRef
$exitCode = 0
try {
  & docker @composeArgs
  $exitCode = $LASTEXITCODE
}
finally {
  if ($null -eq $prevImageRef) {
    Remove-Item Env:\IMAGE_REF -ErrorAction SilentlyContinue
  }
  else {
    $env:IMAGE_REF = $prevImageRef
  }
}
exit $exitCode

<#
.SYNOPSIS
  Thin docker compose wrapper that selects the published image tag via IMAGE_REF.

.DESCRIPTION
  Runs docker compose against compose.yaml, exporting the IMAGE_REF interpolation
  variable that compose.yaml uses for the image tags
  (e.g. ghcr.io/.../orchestrator-service:${IMAGE_REF:-main}-latest).

  Supported refs: main (default), development, nam20485. Each maps to the
  branch tag published by docker-publish.yml (<branch>-latest).

  For `up`, the wrapper always passes `--pull always` so a stale local image
  cache can never shadow the current <branch>-latest tag. Pass `--build`
  explicitly via ExtraArgs to rebuild the image locally instead.

  Required env (set in your shell, never echoed by this script):
    WORKSPACE_DIR            host path bind-mounted to /workspace
    OPENCODE_SERVER_PASSWORD opencode server password

.PARAMETER Command
  The docker compose command to run. Allowed values: up, down, logs. Position 0.

.PARAMETER ImageRef
  Published image tag (branch) to deploy. Allowed values: main (default),
  development, nam20485. Each maps to the `<ref>-latest` tag published by
  docker-publish.yml. Position 1.

.PARAMETER ExtraArgs
  Any additional arguments passed through verbatim to `docker compose`
  (e.g. `-f` to follow logs, `--build` with `up` to rebuild locally).

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
Supported refs: main (default), development, nam20485. Each maps to the
branch tag published by docker-publish.yml (<branch>-latest).

for `up`, the wrapper always passes `--pull always` so a stale local image
cache can never shadow the current <branch>-latest tag. Pass `--build`
  explicitly via ExtraArgs to rebuild the image locally instead.

Required env (set in your shell, never echoed by this script):
WORKSPACE_DIR            host path bind-mounted to /workspace
OPENCODE_SERVER_PASSWORD opencode server password

.PARAMETER Command
The docker compose command to run. Allowed values: up, down, logs. Position 0.

.PARAMETER ImageRef
Published image tag (branch) to deploy. Allowed values: main (default),
development, nam20485. Each maps to the `<ref>-latest` tag published by
docker-publish.yml. Position 1.

.PARAMETER ExtraArgs
Any additional arguments passed through verbatim to `docker compose`
  (e.g. `-f` to follow logs, `--build` with `up` to rebuild locally).

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
