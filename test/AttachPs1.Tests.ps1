BeforeAll {
    $script:AttachPs1 = Join-Path $PSScriptRoot '..' 'scripts' 'attach.ps1'
    # Dot-source the shared helper so we can unit-test its functions directly
    # without invoking opencode.
    . (Join-Path $PSScriptRoot '..' 'scripts' 'init-project-workspace.ps1')
}

Describe 'attach.ps1' {
    It 'exists' {
        Test-Path -LiteralPath $script:AttachPs1 | Should -Be $true
    }

    It 'parses without script errors' {
        $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $script:AttachPs1,
            [ref]$null,
            [ref]$errors
        )
        $errors | Should -BeNullOrEmpty
    }

    It 'calls Resolve-ProjectWorkspace unconditionally' {
        $content = Get-Content -LiteralPath $script:AttachPs1 -Raw
        $content | Should -Match 'Resolve-ProjectWorkspace'
        # The old conditional -Project block must be gone.
        $content | Should -Not -Match 'if\s*\(\s*\$Project\s*\)'
    }
}

Describe 'Resolve-ProjectWorkspace' {
    It 'auto-generates a session slug when no -Project and bare /workspace (never returns root)' {
        $result = Resolve-ProjectWorkspace -Workspace "/workspace" -Project "" -HostWorkspaceDir ""
        $result | Should -Not -Be "/workspace"
        $result | Should -Match '^/workspace/session-\d{8}-\d{6}-[0-9a-f]{6}$'
    }

    It 'uses an explicit -Project' {
        $result = Resolve-ProjectWorkspace -Workspace "/workspace" -Project "myapp" -HostWorkspaceDir ""
        $result | Should -Be "/workspace/myapp"
    }

    It 'derives the slug from a webhook-style -Workspace /workspace/{slug}' {
        $result = Resolve-ProjectWorkspace -Workspace "/workspace/owner-repo" -Project "" -HostWorkspaceDir ""
        $result | Should -Be "/workspace/owner-repo"
    }

    It 'ROOT GUARD: refuses to resolve to the bare /workspace root' {
        # A degenerate slug that collapses back to the root must throw.
        { Resolve-ProjectWorkspace -Workspace "/workspace" -Project "/" -HostWorkspaceDir "" } | Should -Throw
    }

    It 'rejects path traversal (..)' {
        { Resolve-ProjectWorkspace -Workspace "/workspace" -Project ".." -HostWorkspaceDir "" } | Should -Throw
    }

    It 'initializes the host project dir as a git repo on main with .worktrees/ excluded' {
        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("attach-test-" + [System.IO.Path]::GetRandomFileName())
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        try {
            $containerDir = Resolve-ProjectWorkspace -Workspace "/workspace" -Project "x" -HostWorkspaceDir $tmp
            $containerDir | Should -Be "/workspace/x"

            $hostProjectDir = Join-Path $tmp "x"
            Test-Path -LiteralPath $hostProjectDir | Should -Be $true
            Test-Path -LiteralPath (Join-Path $hostProjectDir ".git") | Should -Be $true

            $branch = (& git -C $hostProjectDir symbolic-ref --short HEAD 2>$null).Trim()
            $branch | Should -Be "main"

            $exclude = Join-Path $hostProjectDir ".git" "info" "exclude"
            $excludeContent = Get-Content -LiteralPath $exclude -Raw
            $excludeContent | Should -Match '\.worktrees/'
        }
        finally {
            Remove-Item -Recurse -Force -LiteralPath $tmp -ErrorAction SilentlyContinue
        }
    }
}

Describe 'Get-WorkspaceDirFromEnvOrDotEnv' {
    It 'returns $env:WORKSPACE_DIR when set' {
        $old = $env:WORKSPACE_DIR
        try {
            $env:WORKSPACE_DIR = "my-host-workspace-dir"
            Get-WorkspaceDirFromEnvOrDotEnv | Should -Be "my-host-workspace-dir"
        }
        finally {
            $env:WORKSPACE_DIR = $old
        }
    }

    It 'falls back to the .env WORKSPACE_DIR line when $env:WORKSPACE_DIR is unset' {
        $old = $env:WORKSPACE_DIR
        try {
            $env:WORKSPACE_DIR = ""
            $result = Get-WorkspaceDirFromEnvOrDotEnv
            $repoEnv = Join-Path $PSScriptRoot '..' '.env'
            if (Test-Path -LiteralPath $repoEnv) {
                # Repo .env exists and defines WORKSPACE_DIR, so the helper
                # must surface a non-empty value parsed from it.
                $result | Should -Not -BeNullOrEmpty
            }
        }
        finally {
            $env:WORKSPACE_DIR = $old
        }
    }
}
