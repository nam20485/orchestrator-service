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

    # Beads ecosystem (br + bvr) — required for the graph-backed agent loop.
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        Write-Host @'
cargo is not installed. Install Rust via https://rustup.rs/ to compile the Beads ecosystem (br, bvr).
  beads_rust requires the nightly toolchain: rustup toolchain install nightly
  br:  cargo +nightly install --git https://github.com/Dicklesworthstone/beads_rust.git --rev d9f8d7083dee46d04a8e4741c5f535eb7fcabc97 --locked beads_rust
  bvr: cargo +nightly install --git https://github.com/Dicklesworthstone/beads_viewer_rust.git --rev e4506f63214d32c8bcac4f29479a9b80cb932a6a --locked beads_viewer_rust
'@ -ForegroundColor Yellow
    }
    else {
        # beads_rust 0.2.15 (via the `asupersync` dependency) uses `#![feature]`,
        # which requires the nightly toolchain.
        if (-not (& rustup toolchain list 2>$null | Select-String -Quiet 'nightly')) {
            Write-Host 'Installing Rust nightly toolchain (required by beads_rust)...' -ForegroundColor Cyan
            rustup toolchain install nightly
        }

        if (-not (Get-Command br -ErrorAction SilentlyContinue)) {
            Write-Host 'Compiling and installing br (beads_rust)...' -ForegroundColor Cyan
            cargo +nightly install --git https://github.com/Dicklesworthstone/beads_rust.git --rev d9f8d7083dee46d04a8e4741c5f535eb7fcabc97 --locked beads_rust
        }
        else {
            Write-Host 'br already installed.' -ForegroundColor Green
        }

        if (-not (Get-Command bvr -ErrorAction SilentlyContinue)) {
            Write-Host 'Compiling and installing bvr (beads_viewer_rust)...' -ForegroundColor Cyan
            cargo +nightly install --git https://github.com/Dicklesworthstone/beads_viewer_rust.git --rev e4506f63214d32c8bcac4f29479a9b80cb932a6a --locked beads_viewer_rust
        }
        else {
            Write-Host 'bvr already installed.' -ForegroundColor Green
        }
    }

    Write-Host ''
    Write-Host 'Done. Run: pwsh -NoProfile -File ./scripts/validate.ps1 -All' -ForegroundColor Green
}
finally {
    Pop-Location
}
