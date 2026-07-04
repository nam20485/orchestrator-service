#!/usr/bin/env pwsh
#Requires -Version 7.0
# Pester tests for scripts/sync-agent-instruction-indices.ps1
#
# Deterministic (no network): the script parses; the reconciled index files
# contain the shortIds the orchestrator prompt depends on.
# Network-dependent: a -DryRun against the live upstream is skipped when no
# GITHUB_TOKEN is present (CI sandboxes without network).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Describe 'sync-agent-instruction-indices.ps1' {
    BeforeAll {
        $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
        $script:ScriptPath = Join-Path $script:RepoRoot 'scripts' 'sync-agent-instruction-indices.ps1'
        $script:IndexDir = Join-Path $script:RepoRoot 'image' 'local_ai_instruction_modules'
        $script:DynFile = Join-Path $script:IndexDir 'ai-dynamic-workflows.md'
        $script:AssignFile = Join-Path $script:IndexDir 'ai-workflow-assignments.md'
    }

    It 'script file exists' {
        Test-Path -LiteralPath $script:ScriptPath | Should -Be $true
    }

    It 'script parses without syntax errors' {
        $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $script:ScriptPath, [ref]$null, [ref]$errors)
        $errors.Count | Should -Be 0
    }

    It 'dynamic-workflows index contains required workflow shortIds' {
        $content = Get-Content -LiteralPath $script:DynFile -Raw
        foreach ($id in 'project-setup', 'single-workflow', 'implement-epic', 'review-epic-prs') {
            $content | Should -Match "shortId: $([regex]::Escape($id))\b"
        }
    }

    It 'workflow-assignments index contains required assignment shortIds' {
        $content = Get-Content -LiteralPath $script:AssignFile -Raw
        foreach ($id in 'create-epic-v2', 'report-progress', 'debrief-and-document') {
            $content | Should -Match "shortId: $([regex]::Escape($id))\b"
        }
    }

    It 'both indices declare Branch: main' {
        foreach ($f in $script:DynFile, $script:AssignFile) {
            (Get-Content -LiteralPath $f -Raw) | Should -Match 'Branch: main'
        }
    }

    Context 'live reconcile (network)' {
        It 'runs -DryRun and exits 0' -Skip:(-not $env:GITHUB_TOKEN) {
            $out = & pwsh -NoProfile -File $script:ScriptPath -DryRun 2>&1
            $exit = $LASTEXITCODE
            ($out -join "`n") | Should -Match 'Dry run complete'
            $exit | Should -Be 0
        }
    }
}
