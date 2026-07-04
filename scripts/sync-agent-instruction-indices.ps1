#!/usr/bin/env pwsh
#Requires -Version 7.0
<#
.SYNOPSIS
  Reconciles the two local agent-instruction indices against the canonical
  nam20485/agent-instructions repository via the GitHub Contents API.

.DESCRIPTION
  The orchestrator's prompt resolves dynamic workflows and workflow
  assignments from two checked-in indices under
  image/local_ai_instruction_modules/:

    - ai-dynamic-workflows.md      <- files in dynamic-workflows/  ($workflow_name)
    - ai-workflow-assignments.md   <- files in ai-workflow-assignments/ ($workflow_assignment shortId)

  This script regenerates both from the live upstream directory listings so
  the local indices never drift from the canonical source of truth. It only
  writes a file when its content would change (idempotent).

  Network-dependent: requires a GITHUB_TOKEN (PAT) with public-repo read.
  For private upstream repos it needs repo scope.

.PARAMETER Owner
  GitHub owner of the canonical repo. Default: nam20485

.PARAMETER Repo
  GitHub repo name of the canonical repo. Default: agent-instructions

.PARAMETER Branch
  Branch to read from. Default: main

.PARAMETER DryRun
  Print the planned changes but write nothing. Always exits 0.

.EXAMPLE
  pwsh -File ./scripts/sync-agent-instruction-indices.ps1 -DryRun
  pwsh -File ./scripts/sync-agent-instruction-indices.ps1 -Owner nam20485 -Repo agent-instructions -Branch main
#>
[CmdletBinding()]
param(
    [string]$Owner = 'nam20485',
    [string]$Repo = 'agent-instructions',
    [string]$Branch = 'main',
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Resolve repo root from this script's location (scripts/ -> parent).
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$IndexDir = Join-Path $RepoRoot 'image' 'local_ai_instruction_modules'

# Each index has its own upstream directory and exact prose lines so the
# generated output reproduces the checked-in files byte-for-byte.
$Indices = @(
    [pscustomobject]@{
        Title         = 'Dynamic Workflows Index'
        ListedBelowNoun = 'dynamic workflows'
        MustResolveNoun = 'dynamic workflows'
        SectionHeader = '## Dynamic Workflows '
        UpstreamDir   = 'ai_instruction_modules/ai-workflow-assignments/dynamic-workflows'
        CanonicalDir  = 'ai_instruction_modules/ai-workflow-assignments/dynamic-workflows/'
        FileName      = 'ai-dynamic-workflows.md'
        SelfExclude   = $null  # index lives outside the listed dir
    },
    [pscustomobject]@{
        Title         = 'Workflow Assignments Index'
        ListedBelowNoun = 'workflow assignments'
        MustResolveNoun = 'workflow assignments (by shortId)'
        SectionHeader = '## Workflow Assignments '
        UpstreamDir   = 'ai_instruction_modules/ai-workflow-assignments'
        CanonicalDir  = 'ai_instruction_modules/ai-workflow-assignments/'
        FileName      = 'ai-workflow-assignments.md'
        SelfExclude   = 'ai-workflow-assignments.md'  # never list the index itself
    }
)

function Get-GitHubToken {
    $tok = [string]$env:GITHUB_TOKEN
    if ([string]::IsNullOrWhiteSpace($tok)) {
        throw "GITHUB_TOKEN is required (PAT with read access to $Owner/$Repo)."
    }
    return $tok.Trim()
}

function Get-DirectoryMarkdownFiles {
    param(
        [string]$OwnerName,
        [string]$RepoName,
        [string]$DirPath,
        [string]$BranchName,
        [string]$Token
    )
    # URL-encode the path segments but keep the slashes (Contents API expects
    # a path with literal '/').
    $encodedPath = $DirPath -replace ' ', '%20'
    $url = "https://api.github.com/repos/$OwnerName/$RepoName/contents/$encodedPath`?ref=$BranchName"
    $headers = @{
        'Accept'               = 'application/vnd.github+json'
        'Authorization'        = "Bearer $Token"
        'X-GitHub-Api-Version' = '2022-11-28'
        'User-Agent'           = 'orchestrator-service-sync-indices'
    }
    $resp = Invoke-RestMethod -Uri $url -Headers $headers -Method Get
    # The API returns a single object for a file and an array for a dir.
    if ($resp -isnot [array]) {
        $resp = @($resp)
    }
    # Only regular files with a .md extension.
    return $resp | Where-Object { $_.type -eq 'file' -and $_.name -like '*.md' } |
        Select-Object -ExpandProperty name
}

function Build-IndexContent {
    param(
        [pscustomobject]$IndexDef,
        [string[]]$MarkdownFiles,
        [string]$OwnerName,
        [string]$RepoName,
        [string]$BranchName
    )
    $sorted = $MarkdownFiles | Sort-Object -Property { $_ }
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine("# $($IndexDef.Title)")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("Repository: $OwnerName/$RepoName")
    [void]$sb.AppendLine("Full repo URL: https://github.com/$OwnerName/$RepoName")
    [void]$sb.AppendLine("Branch: $BranchName")
    [void]$sb.AppendLine("Directory: $($IndexDef.CanonicalDir)")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("Listed below are all of the active $($IndexDef.ListedBelowNoun) and their paths.")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("Agents MUST resolve $($IndexDef.MustResolveNoun) from the remote canonical repository. Do not use local mirrors.")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("## Location of Remote Repository")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine("- Repository: $OwnerName/$RepoName")
    [void]$sb.AppendLine("- Branch: $BranchName")
    [void]$sb.AppendLine("- Directory: ``$($IndexDef.CanonicalDir)``")
    [void]$sb.AppendLine()
    [void]$sb.AppendLine($IndexDef.SectionHeader)
    [void]$sb.AppendLine()

    $uiBase = "https://github.com/$OwnerName/$RepoName/blob/$BranchName/$($IndexDef.CanonicalDir)"
    $rawBase = "https://raw.githubusercontent.com/$OwnerName/$RepoName/$BranchName/$($IndexDef.CanonicalDir)"

    foreach ($file in $sorted) {
        $shortId = [System.IO.Path]::GetFileNameWithoutExtension($file)
        [void]$sb.AppendLine("#### $shortId")
        [void]$sb.AppendLine()
        [void]$sb.AppendLine("- shortId: $shortId")
        [void]$sb.AppendLine()
        [void]$sb.AppendLine("- GitHub UI: [$shortId]($uiBase$file)")
        [void]$sb.AppendLine("- Raw URL:   [$shortId]($rawBase$file)")
        [void]$sb.AppendLine("- Canonical file: ``$($IndexDef.CanonicalDir)$file``")
        [void]$sb.AppendLine()
    }

    return $sb.ToString()
}

function Write-IfChanged {
    param(
        [string]$Path,
        [string]$NewContent,
        [switch]$Dry
    )
    $changed = $true
    if (Test-Path -LiteralPath $Path) {
        $existing = Get-Content -LiteralPath $Path -Raw
        if ($existing -ceq $NewContent) {
            $changed = $false
        }
    }
    if ($Dry) {
        if ($changed) {
            Write-Host "DRY-RUN: would update $Path"
        } else {
            Write-Host "DRY-RUN: no change   $Path"
        }
        return
    }
    if (-not $changed) {
        Write-Host "unchanged $Path"
        return
    }
    Set-Content -LiteralPath $Path -Value $NewContent -NoNewline -Encoding utf8
    Write-Host "updated   $Path"
}

# --- main ---
$token = Get-GitHubToken
$anyUpdate = $false
foreach ($def in $Indices) {
    $target = Join-Path $IndexDir $def.FileName
    Write-Host "Fetching $($def.UpstreamDir) from $Owner/$Repo@$Branch ..."
    $files = Get-DirectoryMarkdownFiles -OwnerName $Owner -RepoName $Repo `
        -DirPath $def.UpstreamDir -BranchName $Branch -Token $token
    # Exclude the index file itself when it lives in the same upstream dir.
    if ($def.SelfExclude) {
        $files = $files | Where-Object { $_ -ne $def.SelfExclude }
    }
    if (-not $files) {
        Write-Warning "No markdown files found in $($def.UpstreamDir); leaving $($def.FileName) untouched."
        continue
    }
    $content = Build-IndexContent -IndexDef $def -MarkdownFiles $files `
        -OwnerName $Owner -RepoName $Repo -BranchName $Branch
    # Track whether a real write occurred.
    $existed = Test-Path -LiteralPath $target
    $before = if ($existed) { Get-Content -LiteralPath $target -Raw } else { '' }
    Write-IfChanged -Path $target -NewContent $content -Dry:$DryRun
    if (-not $DryRun -and (-not $existed -or $before -cne $content)) {
        $anyUpdate = $true
    }
}

if ($DryRun) {
    Write-Host 'Dry run complete; nothing written.'
} elseif ($anyUpdate) {
    Write-Host 'Indices reconciled; review and commit any changes.'
} else {
    Write-Host 'Indices already in sync.'
}
exit 0
