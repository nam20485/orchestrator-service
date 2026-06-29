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
