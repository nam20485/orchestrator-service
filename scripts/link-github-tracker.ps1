#!/usr/bin/env pwsh

<#
.SYNOPSIS
    Link GitHub issues to a Project V2 + milestone, and link PRs to issues.

.DESCRIPTION
    Backfill/repair tool for the discovery-path-alignment leftover issues:
    issues created by orchestration were not linked to the GitHub Project or
    Milestone, and PRs were not linked to their issues.

    - Issue (-Issue): adds it to a Project V2 (`gh project item-add`) and assigns
      a milestone (`gh issue edit --milestone`), then verifies both links.
    - PR (-PR -ToIssue): ensures the PR body references the issue with
      `Resolves #<n>` (`gh pr edit`), then verifies `closingIssuesReferences`.

    Idempotent: links already present are skipped unless -Force is given.
    -DryRun prints the exact gh commands without executing them (no network,
    no gh required).

.PARAMETER Repo
    Target repository in the form "owner/repo" (e.g., "nam20485/gap-miner-v2-lima63").

.PARAMETER Issue
    Issue number to link to the project and/or milestone.

.PARAMETER PR
    Pull request number to link to an issue (-ToIssue).

.PARAMETER ToIssue
    Issue number a PR (-PR) should resolve.

.PARAMETER Project
    Project V2 number OR title used to disambiguate. If omitted, the first
    project owned by the repo owner is used.

.PARAMETER Milestone
    Milestone title to assign to -Issue. If omitted, milestone linking is skipped.

.PARAMETER DryRun
    Print planned gh commands without executing them.

.PARAMETER Force
    Re-apply links even when they are already present.

.EXAMPLE
    ./scripts/link-github-tracker.ps1 -Repo o/r -Issue 3 -Project "Gap Mining" `
        -Milestone "Phase 1: Foundation"

.EXAMPLE
    ./scripts/link-github-tracker.ps1 -Repo o/r -PR 5 -ToIssue 1 -DryRun

.NOTES
    Requires the GitHub CLI (gh) authenticated with repo + project scopes.
#>
[CmdletBinding()]
param(
    # NOTE: params are intentionally non-mandatory so this script can be
    # dot-sourced by Pester to unit-test the pure helpers. Validation of -Repo
    # happens in the main body (Split-RepoSlug + empty check).
    [string]$Repo,

    [int]$Issue,
    [int]$PR,
    [int]$ToIssue,

    [string]$Project,
    [string]$Milestone,

    [switch]$DryRun,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ----------------------------------------------------------------------------
# Pure helpers (unit-tested without any network access).
# ----------------------------------------------------------------------------

function Split-RepoSlug {
    param([Parameter(Mandatory)][string]$Slug)
    if ($Slug -notmatch '^[^/]+/[^/]+$') {
        throw "Repo must be 'owner/repo', got: $Slug"
    }
    $parts = $Slug -split '/', 2
    return [pscustomobject]@{ Owner = $parts[0]; Name = $parts[1] }
}

function Resolve-ProjectNumber {
    <#
        Pick a project number from a parsed project list given an optional hint.
        Hint may be a number or a (case-insensitive, substring) title.
        Falls back to the first project. Returns $null when the list is empty.
    #>
    param(
        [AllowNull()][array]$Projects,
        [string]$Hint
    )
    if (-not $Projects -or $Projects.Count -eq 0) { return $null }
    if ($Hint) {
        $n = 0
        if ([int]::TryParse($Hint, [ref]$n)) {
            $byNum = $Projects | Where-Object { [int]$_.number -eq $n }
            if ($byNum) { return [int]($byNum | Select-Object -First 1).number }
        }
        $byTitle = $Projects |
            Where-Object { $_.title -and ($_.title -ieq $Hint -or $_.title -ilike "*$Hint*") }
        if ($byTitle) { return [int]($byTitle | Select-Object -First 1).number }
    }
    return [int]($Projects | Select-Object -First 1).number
}

function Build-ProjectItemAddArgs {
    param(
        [Parameter(Mandatory)][string]$Owner,
        [Parameter(Mandatory)][int]$ProjectNumber,
        [Parameter(Mandatory)][int]$Issue,
        [Parameter(Mandatory)][string]$Repo
    )
    $url = "https://github.com/$Repo/issues/$Issue"
    return @('project', 'item-add', "$ProjectNumber", '--owner', $Owner, '--url', $url)
}

function Build-IssueMilestoneArgs {
    param(
        [Parameter(Mandatory)][string]$Repo,
        [Parameter(Mandatory)][int]$Issue,
        [Parameter(Mandatory)][string]$MilestoneTitle
    )
    return @('issue', 'edit', "$Issue", '-R', $Repo, '--milestone', $MilestoneTitle)
}

function Build-IssueViewArgs {
    param([Parameter(Mandatory)][string]$Repo, [Parameter(Mandatory)][int]$Issue)
    return @('issue', 'view', "$Issue", '-R', $Repo, '--json', 'number,milestone,projectItems')
}

function Build-PrViewArgs {
    param([Parameter(Mandatory)][string]$Repo, [Parameter(Mandatory)][int]$PR)
    return @('pr', 'view', "$PR", '-R', $Repo, '--json', 'number,body,closingIssuesReferences')
}

function New-LinkedPrBody {
    <# Returns a PR body that references `Resolves #<Issue>`; leaves an existing
       Resolves/Closes/Fixes reference untouched. #>
    param([string]$Body, [Parameter(Mandatory)][int]$Issue)
    if ($null -eq $Body) { $Body = '' }
    $marker = "Resolves #$Issue"
    if ($Body -match "(?im)^\s*(resolves|closes|fixes)\s+#$Issue\b") { return $Body }
    if ($Body.Trim()) { return "$marker`n`n$Body" }
    return $marker
}

function Test-PrClosesIssue {
    param([AllowNull()]$Pr, [Parameter(Mandatory)][int]$Issue)
    if (-not $Pr) { return $false }
    $refs = $Pr.closingIssuesReferences
    if (-not $refs) { return $false }
    foreach ($r in $refs) { if ([int]$r.number -eq $Issue) { return $true } }
    return $false
}

function Test-IssueHasProjectLink {
    param([AllowNull()]$IssueObject)
    if (-not $IssueObject) { return $false }
    $items = @($IssueObject.projectItems)
    return ($items.Count -gt 0)
}

function Test-IssueHasMilestone {
    param([AllowNull()]$IssueObject, [string]$Title)
    if (-not $IssueObject -or -not $IssueObject.milestone) { return $false }
    if ($Title) { return ([string]$IssueObject.milestone.title -ieq $Title) }
    return $true
}

# ----------------------------------------------------------------------------
# Network primitive. Honors $script:DryRun: prints the command and returns a
# stand-in instead of calling gh.
# ----------------------------------------------------------------------------

function Invoke-Gh {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [switch]$AsJson
    )
    $cmd = 'gh ' + ($Arguments -join ' ')
    if ($script:LinkDryRun) {
        Write-Host "[dry-run] $cmd" -ForegroundColor Yellow
        if ($AsJson) { return $null }
        return ''
    }
    Write-Host $cmd -ForegroundColor Cyan
    $out = & gh @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "gh failed (exit $LASTEXITCODE): $cmd"
    }
    if ($AsJson -and $out) { return $out | ConvertFrom-Json }
    return $out
}

function Get-ProjectNumber {
    param(
        [Parameter(Mandatory)][string]$Owner,
        [string]$Hint,
        [Parameter(Mandatory)][bool]$IsDryRun
    )
    if ($IsDryRun) {
        $n = 0
        if ($Hint -and [int]::TryParse($Hint, [ref]$n)) { return [int]$n }
        return 1
    }
    $listJson = & gh project list --owner $Owner --format json
    if ($LASTEXITCODE -ne 0 -or -not $listJson) { return $null }
    $plist = $listJson | ConvertFrom-Json
    return Resolve-ProjectNumber -Projects $plist -Hint $Hint
}

# ----------------------------------------------------------------------------
# Main body. Only executes when run as a script (NOT when dot-sourced for tests).
# ----------------------------------------------------------------------------

if ($MyInvocation.InvocationName -ne '.') {
    $script:LinkDryRun = [bool]$DryRun

    if (-not $Repo) {
        Write-Error '-Repo is required (owner/repo).'
        exit 1
    }
    if (-not ($Issue -or $PR)) {
        Write-Error 'Provide -Issue (to link to project/milestone) and/or -PR -ToIssue (to link a PR to an issue).'
        exit 1
    }

    $slug = Split-RepoSlug -Slug $Repo
    $actions = [System.Collections.Generic.List[object]]::new()

    if ($Issue) {
        $before = Invoke-Gh -Arguments (Build-IssueViewArgs -Repo $Repo -Issue $Issue) -AsJson

        # Project link
        $projNum = Get-ProjectNumber -Owner $slug.Owner -Hint $Project -IsDryRun $script:LinkDryRun
        if ($projNum) {
            $alreadyLinked = (-not $Force) -and (Test-IssueHasProjectLink -IssueObject $before)
            if ($alreadyLinked) {
                Write-Host "Issue #$Issue already in a project; skipping (use -Force to re-add)." -ForegroundColor DarkGray
                $actions.Add(@{ kind = 'project'; status = 'already-linked'; issue = $Issue })
            }
            else {
                Invoke-Gh -Arguments (Build-ProjectItemAddArgs -Owner $slug.Owner -ProjectNumber ([int]$projNum) -Issue $Issue -Repo $Repo) | Out-Null
                $actions.Add(@{ kind = 'project'; status = 'linked'; issue = $Issue; project = $projNum })
            }
        }
        else {
            Write-Warning "No GitHub Project found for owner '$($slug.Owner)'; skipping project link (issue #$Issue)."
            $actions.Add(@{ kind = 'project'; status = 'no-project-found'; issue = $Issue })
        }

        # Milestone link
        if ($Milestone) {
            $alreadyMilestone = (-not $Force) -and (Test-IssueHasMilestone -IssueObject $before -Title $Milestone)
            if ($alreadyMilestone) {
                Write-Host "Issue #$Issue already on milestone '$Milestone'; skipping (use -Force to re-set)." -ForegroundColor DarkGray
                $actions.Add(@{ kind = 'milestone'; status = 'already-linked'; issue = $Issue; milestone = $Milestone })
            }
            else {
                Invoke-Gh -Arguments (Build-IssueMilestoneArgs -Repo $Repo -Issue $Issue -MilestoneTitle $Milestone) | Out-Null
                $actions.Add(@{ kind = 'milestone'; status = 'linked'; issue = $Issue; milestone = $Milestone })
            }
        }

        # Verify (real runs only)
        if (-not $script:LinkDryRun) {
            $after = Invoke-Gh -Arguments (Build-IssueViewArgs -Repo $Repo -Issue $Issue) -AsJson
            $projOk = Test-IssueHasProjectLink -IssueObject $after
            $msOk = if ($Milestone) { Test-IssueHasMilestone -IssueObject $after -Title $Milestone } else { $true }
            $actions.Add(@{ kind = 'verify'; issue = $Issue; project = $projOk; milestone = $msOk })
            if (-not $projOk -and $projNum) {
                Write-Error "Project verification failed for issue #$Issue (project absent after add)."
            }
            if (-not $msOk) {
                Write-Error "Milestone verification failed for issue #$Issue (expected '$Milestone')."
            }
        }
    }

    if ($PR) {
        if (-not $ToIssue) {
            Write-Error '-PR requires -ToIssue (the issue the PR should resolve).'
            exit 1
        }
        $prBefore = Invoke-Gh -Arguments (Build-PrViewArgs -Repo $Repo -PR $PR) -AsJson
        $alreadyCloses = (-not $Force) -and (Test-PrClosesIssue -Pr $prBefore -Issue $ToIssue)
        if ($alreadyCloses) {
            Write-Host "PR #$PR already resolves issue #$ToIssue; skipping (use -Force to re-apply)." -ForegroundColor DarkGray
            $actions.Add(@{ kind = 'pr'; status = 'already-linked'; pr = $PR; issue = $ToIssue })
        }
        else {
            $newBody = if ($prBefore) { New-LinkedPrBody -Body $prBefore.body -Issue $ToIssue } else { "Resolves #$ToIssue" }
            Invoke-Gh -Arguments @('pr', 'edit', "$PR", '-R', $Repo, '--body', $newBody) | Out-Null
            $actions.Add(@{ kind = 'pr'; status = 'linked'; pr = $PR; issue = $ToIssue })
        }
        if (-not $script:LinkDryRun) {
            $prAfter = Invoke-Gh -Arguments (Build-PrViewArgs -Repo $Repo -PR $PR) -AsJson
            $ok = Test-PrClosesIssue -Pr $prAfter -Issue $ToIssue
            $actions.Add(@{ kind = 'verify-pr'; pr = $PR; issue = $ToIssue; linked = $ok })
            if (-not $ok) {
                Write-Error "PR #$PR does not resolve issue #$ToIssue after update (GitHub may need a moment to index the reference)."
            }
        }
    }

    $actions | ConvertTo-Json -Depth 6
    Write-Host 'Done.' -ForegroundColor Green
}
