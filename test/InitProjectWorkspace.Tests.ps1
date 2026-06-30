BeforeAll {
    $script:HelperScript = Join-Path $PSScriptRoot '..' 'scripts' 'init-project-workspace.ps1'
    # Dot-source to load Initialize-ProjectWorkspace / Resolve-ProjectWorkspace.
    . $script:HelperScript
}

Describe 'Resolve-ProjectWorkspace slug validation' {
    It 'rejects an explicit single-dot project' {
        { Resolve-ProjectWorkspace -Project '.' } | Should -Throw
    }

    It 'rejects path separators in the project slug (<value>)' -ForEach @(
        @{ Value = 'foo/bar' }
        @{ Value = 'foo\bar' }
    ) {
        { Resolve-ProjectWorkspace -Project $Value } | Should -Throw
    }

    It 'rejects parent-directory traversal (<value>)' -ForEach @(
        @{ Value = '..' }
        @{ Value = 'foo/..' }
        @{ Value = 'a/../b' }
    ) {
        { Resolve-ProjectWorkspace -Project $Value } | Should -Throw
    }

    It 'rejects an allowlist-violating slug' {
        { Resolve-ProjectWorkspace -Project '.hidden' } | Should -Throw
    }

    It 'accepts a valid single-segment slug' {
        $result = Resolve-ProjectWorkspace -Project 'valid-slug'
        $result | Should -Be '/workspace/valid-slug'
    }

    It 'auto-generates a session slug when -Project is empty' {
        $result = Resolve-ProjectWorkspace -Project ''
        $result | Should -Match '^/workspace/session-[0-9a-f-]+$'
    }
}

Describe 'Resolve-ProjectWorkspace worktree pass-through' {
    It 'passes a multi-segment /workspace path through unchanged' {
        $result = Resolve-ProjectWorkspace -Workspace '/workspace/my-app/.worktrees/bead-1' -Project ''
        $result | Should -Be '/workspace/my-app/.worktrees/bead-1'
    }

    It 'passes a multi-segment path through unchanged (arbitrary depth)' {
        $result = Resolve-ProjectWorkspace -Workspace '/workspace/proj/sub/deep' -Project ''
        $result | Should -Be '/workspace/proj/sub/deep'
    }

    It 'explicit -Project still wins over a multi-segment workspace' {
        $result = Resolve-ProjectWorkspace -Workspace '/workspace/a/b' -Project 'myproj'
        $result | Should -Be '/workspace/myproj'
    }

    It 'does NOT pass through a multi-segment path containing .. (falls back to session slug)' {
        $result = Resolve-ProjectWorkspace -Workspace '/workspace/a/../b' -Project ''
        $result | Should -Match '^/workspace/session-[0-9a-f-]+$'
    }

    It 'does NOT pass through /workspace// (empty segment collapses to root → session slug)' {
        $result = Resolve-ProjectWorkspace -Workspace '/workspace//' -Project ''
        $result | Should -Match '^/workspace/session-[0-9a-f-]+$'
    }

    It 'does NOT pass through /workspace/. (self segment resolves to root → session slug)' {
        $result = Resolve-ProjectWorkspace -Workspace '/workspace/.' -Project ''
        $result | Should -Match '^/workspace/session-[0-9a-f-]+$'
    }

    It 'still auto-generates a session slug for the bare /workspace root' {
        $result = Resolve-ProjectWorkspace -Workspace '/workspace' -Project ''
        $result | Should -Match '^/workspace/session-[0-9a-f-]+$'
    }

    It 'follows BEADS_WORKSPACE_ROOT for the pass-through prefix' {
        $old = $env:BEADS_WORKSPACE_ROOT
        try {
            $env:BEADS_WORKSPACE_ROOT = '/data/beads'
            $result = Resolve-ProjectWorkspace -Workspace '/data/beads/proj/.worktrees/x' -Project ''
            $result | Should -Be '/data/beads/proj/.worktrees/x'
        }
        finally {
            $env:BEADS_WORKSPACE_ROOT = $old
        }
    }

    It 'does NOT pass through a path under a different root than BEADS_WORKSPACE_ROOT' {
        $old = $env:BEADS_WORKSPACE_ROOT
        try {
            $env:BEADS_WORKSPACE_ROOT = '/data/beads'
            # /workspace/... no longer matches the configured root prefix.
            $result = Resolve-ProjectWorkspace -Workspace '/workspace/my-app/.worktrees/x' -Project ''
            $result | Should -Match '^/data/beads/session-[0-9a-f-]+$'
        }
        finally {
            $env:BEADS_WORKSPACE_ROOT = $old
        }
    }
}
