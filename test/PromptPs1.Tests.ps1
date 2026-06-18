BeforeAll {
    $script:PromptPs1 = Join-Path $PSScriptRoot '..' 'scripts' 'prompt.ps1'
}

Describe 'prompt.ps1' {
    It 'exists' {
        Test-Path -LiteralPath $script:PromptPs1 | Should -Be $true
    }

    It 'parses without script errors' {
        $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $script:PromptPs1,
            [ref]$null,
            [ref]$errors
        )
        $errors | Should -BeNullOrEmpty
    }

    It 'requires Prompt or PromptFile' {
        $content = Get-Content -LiteralPath $script:PromptPs1 -Raw
        $content | Should -Match 'Provide -Prompt or -PromptFile'
    }
}
