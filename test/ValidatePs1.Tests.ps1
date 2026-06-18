BeforeAll {
    $script:ValidatePs1 = Join-Path $PSScriptRoot '..' 'scripts' 'validate.ps1'
}

Describe 'validate.ps1' {
    It 'exists' {
        Test-Path -LiteralPath $script:ValidatePs1 | Should -Be $true
    }

    It 'parses without script errors' {
        $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $script:ValidatePs1,
            [ref]$null,
            [ref]$errors
        )
        $errors | Should -BeNullOrEmpty
    }

    It 'declares All Lint Scan and Test switches' {
        $content = Get-Content -LiteralPath $script:ValidatePs1 -Raw
        $content | Should -Match '\[switch\]\$All'
        $content | Should -Match '\[switch\]\$Lint'
        $content | Should -Match '\[switch\]\$Scan'
        $content | Should -Match '\[switch\]\$Test'
    }
}
