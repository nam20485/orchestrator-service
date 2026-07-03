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

    It 'resolves the workspace via Resolve-ProjectWorkspace (no bare /workspace default)' {
        $content = Get-Content -LiteralPath $script:PromptPs1 -Raw
        $content | Should -Match 'Resolve-ProjectWorkspace'
        # The old conditional -Project block must be gone.
        $content | Should -Not -Match 'if\s*\(\s*\$Project\s*\)'
        # The old host dir-creation block must be gone.
        $content | Should -Not -Match 'WORKSPACE_DIR\s*-and\s*\$Workspace'
    }
}
