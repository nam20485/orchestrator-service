BeforeAll {
    $script:TrackerPs1 = Join-Path $PSScriptRoot '..' 'scripts' 'link-github-tracker.ps1'
    # Dot-source to load pure helpers (main body is guarded against dot-source).
    . $script:TrackerPs1
}

Describe 'link-github-tracker.ps1 parses cleanly' {
    It 'has no PowerShell parse errors' {
        $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $script:TrackerPs1, [ref]$null, [ref]$errors
        )
        $errors | Should -BeNullOrEmpty
    }

    It 'declares the expected parameters' {
        $content = Get-Content -LiteralPath $script:TrackerPs1 -Raw
        $content | Should -Match '\[string\]\$Repo'
        $content | Should -Match '\[int\]\$Issue'
        $content | Should -Match '\[int\]\$PR'
        $content | Should -Match '\[int\]\$ToIssue'
        $content | Should -Match '\$DryRun'
        $content | Should -Match '\$Force'
    }
}

Describe 'Split-RepoSlug' {
    It 'splits owner/repo' {
        $r = Split-RepoSlug -Slug 'nam20485/gap-miner-v2-lima63'
        $r.Owner | Should -Be 'nam20485'
        $r.Name | Should -Be 'gap-miner-v2-lima63'
    }

    It 'rejects a slug without a slash' {
        { Split-RepoSlug -Slug 'bad' } | Should -Throw
    }

    It 'rejects a slug with multiple slashes' {
        { Split-RepoSlug -Slug 'a/b/c' } | Should -Throw
    }
}

Describe 'Resolve-ProjectNumber' {
    BeforeAll {
        $script:projects = @(
            [pscustomobject]@{ number = 1; title = 'Gap Mining' }
            [pscustomobject]@{ number = 7; title = 'Other Project' }
        )
    }

    It 'returns null for an empty list' {
        Resolve-ProjectNumber -Projects @() -Hint '' | Should -BeNullOrEmpty
    }

    It 'matches by numeric hint' {
        Resolve-ProjectNumber -Projects $script:projects -Hint '7' | Should -Be 7
    }

    It 'matches by exact title (case-insensitive)' {
        Resolve-ProjectNumber -Projects $script:projects -Hint 'gap mining' | Should -Be 1
    }

    It 'matches by title substring' {
        Resolve-ProjectNumber -Projects $script:projects -Hint 'other' | Should -Be 7
    }

    It 'falls back to the first project when no hint matches' {
        Resolve-ProjectNumber -Projects $script:projects -Hint 'nonexistent' | Should -Be 1
    }
}

Describe 'Build-*Args command builders' {
    It 'Build-ProjectItemAddArgs targets project item-add with the issue URL' {
        $args = Build-ProjectItemAddArgs -Owner 'nam20485' -ProjectNumber 1 -Issue 3 -Repo 'nam20485/r'
        ($args -join ' ') | Should -Match 'project item-add 1 --owner nam20485'
        ($args -join ' ') | Should -Match 'https://github\.com/nam20485/r/issues/3'
    }

    It 'Build-IssueMilestoneArgs sets --milestone on the issue' {
        $args = Build-IssueMilestoneArgs -Repo 'o/r' -Issue 3 -MilestoneTitle 'Phase 1: Foundation'
        ($args -join ' ') | Should -Match 'issue edit 3 -R o/r --milestone Phase 1: Foundation'
    }

    It 'Build-PrViewArgs asks for closingIssuesReferences' {
        $args = Build-PrViewArgs -Repo 'o/r' -PR 5
        ($args -join ' ') | Should -Match 'closingIssuesReferences'
    }
}

Describe 'New-LinkedPrBody' {
    It 'prepends Resolves #N to a populated body' {
        $b = New-LinkedPrBody -Body 'Implement feature X' -Issue 1
        $b | Should -Match '(?m)^Resolves #1$'
        $b | Should -Match 'Implement feature X'
    }

    It 'uses just the marker for an empty body' {
        New-LinkedPrBody -Body '' -Issue 7 | Should -BeExactly 'Resolves #7'
    }

    It 'leaves an existing Resolves reference untouched' {
        $b = New-LinkedPrBody -Body "Resolves #1`n`nAlready linked" -Issue 1
        ($b -split "`n").Where({ $_ -eq 'Resolves #1' }).Count | Should -Be 1
    }

    It 'leaves an existing Closes reference untouched' {
        $b = New-LinkedPrBody -Body "closes #2 body" -Issue 2
        $b | Should -Not -Match '(?m)^Resolves #2$'
    }
}

Describe 'Test-PrClosesIssue / Test-IssueHas* predicates' {
    It 'detects a closing reference' {
        $pr = [pscustomobject]@{
            closingIssuesReferences = @([pscustomobject]@{ number = 1 })
        }
        Test-PrClosesIssue -Pr $pr -Issue 1 | Should -BeTrue
        Test-PrClosesIssue -Pr $pr -Issue 2 | Should -BeFalse
    }

    It 'returns false for a PR with no references' {
        $pr = [pscustomobject]@{ closingIssuesReferences = @() }
        Test-PrClosesIssue -Pr $pr -Issue 1 | Should -BeFalse
    }

    It 'detects a project link on an issue' {
        $linked = [pscustomobject]@{ projectItems = @([pscustomobject]@{ id = 'x' }) }
        $unlinked = [pscustomobject]@{ projectItems = @() }
        Test-IssueHasProjectLink -IssueObject $linked | Should -BeTrue
        Test-IssueHasProjectLink -IssueObject $unlinked | Should -BeFalse
    }

    It 'detects a milestone on an issue (with optional title match)' {
        $i = [pscustomobject]@{ milestone = [pscustomobject]@{ title = 'Phase 1: Foundation' } }
        Test-IssueHasMilestone -IssueObject $i | Should -BeTrue
        Test-IssueHasMilestone -IssueObject $i -Title 'phase 1: foundation' | Should -BeTrue
        Test-IssueHasMilestone -IssueObject $i -Title 'Phase 2' | Should -BeFalse
        Test-IssueHasMilestone -IssueObject ([pscustomobject]@{ milestone = $null }) | Should -BeFalse
    }
}

Describe 'link-github-tracker.ps1 -DryRun (no network)' {
    BeforeAll {
        $script:Pwsh = (Get-Process -Id $PID).Path
    }

    It 'emits project + milestone commands for an issue and stays offline' {
        $out = & $script:Pwsh -NoProfile -File $script:TrackerPs1 `
            -Repo 'o/r' -Issue 3 -Project 'Gap Mining' -Milestone 'Phase 1: Foundation' -DryRun 2>&1
        $text = ($out -join "`n")
        $text | Should -Match 'gh project item-add'
        $text | Should -Match 'gh issue edit 3 -R o/r --milestone Phase 1: Foundation'
    }

    It 'emits a pr edit referencing Resolves #N' {
        $out = & $script:Pwsh -NoProfile -File $script:TrackerPs1 `
            -Repo 'o/r' -PR 5 -ToIssue 1 -DryRun 2>&1
        $text = ($out -join "`n")
        $text | Should -Match 'gh pr edit 5 -R o/r'
        $text | Should -Match 'Resolves #1'
    }

    It 'errors when neither -Issue nor -PR is given' {
        $out = & $script:Pwsh -NoProfile -File $script:TrackerPs1 -Repo 'o/r' -DryRun 2>&1
        $LASTEXITCODE | Should -Be 1
    }
}
