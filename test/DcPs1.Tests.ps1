BeforeAll {
    $script:DcPs1 = Join-Path $PSScriptRoot '..' 'scripts' 'dc.ps1'
}

Describe 'dc.ps1' {
    It 'exists' {
        Test-Path -LiteralPath $script:DcPs1 | Should -Be $true
    }

    It 'parses without script errors' {
        $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $script:DcPs1,
            [ref]$null,
            [ref]$errors
        )
        $errors | Should -BeNullOrEmpty
    }

    It 'declares the Command ImageRef and ExtraArgs parameters' {
        $content = Get-Content -LiteralPath $script:DcPs1 -Raw
        $content | Should -Match '\[ValidateSet\(.up., .down., .logs., .u., .d., .l.\)\]'
        $content | Should -Match '\[ValidateSet\(.main., .development., .nam20485.\)\]'
        $content | Should -Match 'ValueFromRemainingArguments'
    }

    It 'exports IMAGE_REF and targets compose.yaml' {
        $content = Get-Content -LiteralPath $script:DcPs1 -Raw
        $content | Should -Match '\$env:IMAGE_REF'
        $content | Should -Match 'compose\.yaml'
    }

    It 'always pulls the freshest image on up (--pull always)' {
        $content = Get-Content -LiteralPath $script:DcPs1 -Raw
        $content | Should -Match "'--pull',\s*'always'"
    }
}

Describe 'dc.ps1 help' {
    It 'discovers comment-based help via Get-Help -Full' {
        $helpText = (Get-Help -Full $script:DcPs1 | Out-String) -replace '\s+', ' '
        $helpText | Should -Match 'SYNOPSIS'
        $helpText | Should -Match 'IMAGE_REF'
    }

    It 'documents the Command ImageRef and ExtraArgs parameters' {
        $helpText = (Get-Help -Full $script:DcPs1 | Out-String) -replace '\s+', ' '
        $helpText | Should -Match 'Command'
        $helpText | Should -Match 'ImageRef'
        $helpText | Should -Match 'ExtraArgs'
    }

    It 'documents the allowed values for Command and ImageRef' {
        $helpText = (Get-Help -Full $script:DcPs1 | Out-String) -replace '\s+', ' '
        $helpText | Should -Match 'Allowed values: up, down, logs'
        $helpText | Should -Match 'Allowed values: main \(default\), development, nam20485'
    }

    It 'shows examples via Get-Help -Examples' {
        $examples = (Get-Help $script:DcPs1 -Examples | Out-String) -replace '\s+', ' '
        $examples | Should -Match 'EXAMPLE'
    }

    It 'prints full help at runtime with -? and exits 0' {
        $output = & pwsh -NoProfile -File $script:DcPs1 -? 2>&1 | Out-String
        $LASTEXITCODE | Should -Be 0
        ($output -replace '\s+', ' ') | Should -Match 'SYNOPSIS'
    }

    It 'keeps the help block last in the file after the shebang lines' {
        $content = Get-Content -LiteralPath $script:DcPs1 -Raw
        $content | Should -Not -Match '(?s)#>\s*\S'
        (Get-Content -LiteralPath $script:DcPs1 -TotalCount 1) | Should -Be '#!/usr/bin/env pwsh'
        (Get-Content -LiteralPath $script:DcPs1 -TotalCount 2)[1] | Should -Be '#Requires -Version 7.0'
    }
}
