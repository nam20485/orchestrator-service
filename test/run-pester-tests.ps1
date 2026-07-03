#!/usr/bin/env pwsh
#Requires -Version 7.0
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
Push-Location $repoRoot
try {
    if (-not (Get-Module -ListAvailable -Name Pester | Where-Object { $_.Version -ge '5.0.0' })) {
        Write-Error @'
Pester 5+ is required. Install with:
  Install-Module Pester -MinimumVersion 5.0.0 -Scope CurrentUser -Force
'@
    }
    Import-Module Pester -MinimumVersion 5.0.0 -Force
    $result = Invoke-Pester `
        -Script (Get-ChildItem -Path (Join-Path $repoRoot 'test') -Filter '*.Tests.ps1') `
        -PassThru
    if ($null -eq $result -or $result.FailedCount -gt 0) {
        exit 1
    }
}
finally {
    Pop-Location
}
