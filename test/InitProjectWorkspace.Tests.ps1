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

Describe 'Resolve-ProjectWorkspace symlink-escape guard' {
    BeforeAll {
        # Sibling temp roots so an escape target can live OUTSIDE the workspace
        # root while still in the OS temp area.
        $script:TempBase = [System.IO.Path]::GetTempPath().TrimEnd('/\')
    }

    It 'rejects a pass-through path whose real target escapes the workspace root' {
        $old = $env:BEADS_WORKSPACE_ROOT
        $root = Join-Path $script:TempBase ("ipw-root-" + [Guid]::NewGuid().ToString('N'))
        $escape = Join-Path $script:TempBase ("ipw-escape-" + [Guid]::NewGuid().ToString('N'))
        try {
            New-Item -ItemType Directory -Force -Path (Join-Path $escape 'bead1') | Out-Null
            New-Item -ItemType Directory -Force -Path (Join-Path $root 'proj') | Out-Null
            # `.worktrees` is a tracked-looking symlink pointing OUTSIDE root.
            try {
                New-Item -ItemType SymbolicLink -Path (Join-Path $root 'proj' '.worktrees') -Target $escape -ErrorAction Stop | Out-Null
            } catch {
                Set-ItResult -Skipped -Because "symlink creation is unavailable on this host ('$($_.Exception.Message)')."
                return
            }
            $env:BEADS_WORKSPACE_ROOT = $root
            $candidate = ((Join-Path $root 'proj' '.worktrees' 'bead1') -replace '\\', '/')
            { Resolve-ProjectWorkspace -Workspace $candidate -Project '' } | Should -Throw
        }
        finally {
            $env:BEADS_WORKSPACE_ROOT = $old
            Remove-Item -LiteralPath $root, $escape -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    It 'passes through an EXISTING real (non-symlink) worktree dir under root' {
        $old = $env:BEADS_WORKSPACE_ROOT
        $root = Join-Path $script:TempBase ("ipw-real-" + [Guid]::NewGuid().ToString('N'))
        try {
            $wt = Join-Path $root 'proj' '.worktrees' 'bead1'
            New-Item -ItemType Directory -Force -Path $wt | Out-Null
            $env:BEADS_WORKSPACE_ROOT = $root
            $candidate = ($wt -replace '\\', '/')
            $result = Resolve-ProjectWorkspace -Workspace $candidate -Project ''
            $result | Should -Be $candidate
        }
        finally {
            $env:BEADS_WORKSPACE_ROOT = $old
            Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
