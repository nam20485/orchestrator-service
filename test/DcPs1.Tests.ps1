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
        $content | Should -Match '\[ValidateSet\(.up., .down., .logs.\)\]'
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
