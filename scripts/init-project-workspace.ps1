#! /usr/bin/env pwsh

# Shared helper: initialize a per-project workspace directory + git repo.
# Dot-sourced by scripts/prompt.ps1 and scripts/attach.ps1.
#
# Contract (must match webhook_receiver/workspace.py):
#   - worktree dir name: .worktrees/
#   - git default branch: main
#   - .git/info/exclude entry: .worktrees/
#
# Capture this file's directory at load (dot-source) time. Inside a dot-sourced
# function $PSScriptRoot can be empty or reflect the caller's scope; this
# captured value is stable and used by Get-WorkspaceDirFromEnvOrDotEnv to locate
# the repo .env file.
$script:InitProjectWorkspaceScriptDir = $PSScriptRoot

function Initialize-ProjectWorkspace {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$WorkspaceRoot,

        [Parameter(Mandatory)]
        [string]$Project
    )

    # Guard against path traversal: reject any '..' segment.
    if (($Project -split '[\\/]') -contains '..') {
        throw "Invalid project slug '$Project': contains '..' segment."
    }

    $rootPath = (Resolve-Path -LiteralPath $WorkspaceRoot).Path.TrimEnd('/\') + [IO.Path]::DirectorySeparatorChar
    $hostProjectDir = Join-Path $WorkspaceRoot $Project
    $resolved = [System.IO.Path]::GetFullPath($hostProjectDir).TrimEnd('/\') + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($rootPath)) {
        throw "Project path '$hostProjectDir' escapes workspace root '$WorkspaceRoot'."
    }

    if (-not (Test-Path -LiteralPath $hostProjectDir)) {
        New-Item -ItemType Directory -Force -Path $hostProjectDir | Out-Null
    }
    if (-not (Test-Path -LiteralPath (Join-Path $hostProjectDir ".git"))) {
        Push-Location -LiteralPath $hostProjectDir
        try {
            & git init --initial-branch=main 2>$null | Out-Null
            $excludeFile = Join-Path $hostProjectDir ".git" "info" "exclude"
            if (-not (Select-String -LiteralPath $excludeFile -Pattern '\.worktrees/' -Quiet -ErrorAction SilentlyContinue)) {
                Add-Content -LiteralPath $excludeFile -Value ".worktrees/"
            }
        } finally {
            Pop-Location
        }
    }

    return $hostProjectDir
}

function Get-WorkspaceDirFromEnvOrDotEnv {
    <#
        Returns the host workspace root.

        Precedence:
          1. $env:WORKSPACE_DIR (if set and non-empty).
          2. The WORKSPACE_DIR= line in a repo-root .env file (only that one
             line is parsed; the rest of .env — which may contain secrets — is
             never sourced).
          3. "" (empty) if neither is available.

        The .env search starts at $PSScriptRoot and checks the parent dir, so it
        works whether this helper is invoked from scripts/ or test/.
    #>
    [CmdletBinding()]
    param()

    if ($env:WORKSPACE_DIR) {
        return $env:WORKSPACE_DIR
    }

    $searchBase = if ($script:InitProjectWorkspaceScriptDir) { $script:InitProjectWorkspaceScriptDir } else { $PSScriptRoot }
    if ($searchBase) {
        $candidates = @(
            (Join-Path $searchBase ".env"),
            (Join-Path $searchBase ".." ".env")
        )
        foreach ($candidate in $candidates) {
            if (Test-Path -LiteralPath $candidate) {
                $envPath = (Resolve-Path -LiteralPath $candidate).Path
                foreach ($line in (Get-Content -LiteralPath $envPath)) {
                    if ($line -match '^\s*WORKSPACE_DIR\s*=\s*(?<val>.*)$') {
                        $val = $Matches['val'].Trim()
                        # Strip a single pair of surrounding quotes if present.
                        if ($val.Length -ge 2 -and
                            (($val.StartsWith('"') -and $val.EndsWith('"')) -or
                             ($val.StartsWith("'") -and $val.EndsWith("'")))) {
                            $val = $val.Substring(1, $val.Length - 2)
                        }
                        return $val
                    }
                }
                # .env exists but has no WORKSPACE_DIR line.
                return $env:WORKSPACE_DIR
            }
        }
    }

    return $env:WORKSPACE_DIR
}

function Resolve-ProjectWorkspace {
    <#
        Resolve the container-side --dir to an isolated per-project subdir.

        Project slug precedence:
          1. -Project <slug>                       (if supplied, non-empty)
          2. derived from -Workspace               (if it is /workspace/<slug>,
                                                   a single path-safe segment)
          3. auto-generated                        session-<yyyyMMdd-HHmmss>-<6hex>

        The result NEVER equals the bare /workspace root. When -HostWorkspaceDir
        is supplied, the host-side project dir is initialized as a git repo on
        branch 'main' with '.worktrees/' in .git/info/exclude (via
        Initialize-ProjectWorkspace) so per-bead worktrees work.
    #>
    [CmdletBinding()]
    param(
        [string]$Workspace = "/workspace",
        [string]$Project,
        [string]$HostWorkspaceDir
    )

    # Container workspace root. Defaults to /workspace (the bind-mount target).
    # Follows BEADS_WORKSPACE_ROOT so this matches wherever the BeadsLoop actually
    # places project worktrees (webhook_receiver.config.beads_workspace_root).
    $containerRoot = if ([string]::IsNullOrWhiteSpace($env:BEADS_WORKSPACE_ROOT)) { "/workspace" } else { $env:BEADS_WORKSPACE_ROOT.TrimEnd('/\') }
    $sep = [IO.Path]::DirectorySeparatorChar
    $rootPattern = '^' + [regex]::Escape($containerRoot.TrimEnd('/\')) + [regex]::Escape($sep) + '(?<rel>.+)$'

    # 1. Explicit -Project wins.
    # 2. Else derive from a <root>/<slug> -Workspace value (webhook style).
    # 3. Else pass through an already-multi-segment <root>/<...> path unchanged
    #    (a per-bead worktree created server-side by the BeadsLoop).
    # 4. Else auto-generate a unique session slug.
    if ([string]::IsNullOrWhiteSpace($Project)) {
        if ($Workspace -match $rootPattern) {
            $rel = $Matches['rel']
            # Single path-safe segment only: no nested slashes, no '..', and
            # matching the filesystem-safe slug pattern.
            if ($rel -notmatch '[/\\]' -and
                $rel -ne '..' -and
                $rel -match '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
                $Project = $rel
            }
            else {
                $segs = $rel -split '[\\/]'
                # Multi-segment path already under <root>/: a ready-made working
                # directory (e.g. a per-bead worktree created server-side by
                # workspace.create_bead_worktree). Pass it through ONLY when it has
                # no traversal ('..'), self ('.'), or empty ('//') segments AND
                # resolves strictly under <root>/ — never the bare root itself.
                # The repo/worktree already exists; the root guard and
                # Initialize-ProjectWorkspace are intentionally skipped here.
                if ($segs -notcontains '..' -and $segs -notcontains '.' -and $segs -notcontains '') {
                    $passThrough = $Workspace.TrimEnd('/\')
                    if ($passThrough -ne $containerRoot -and $passThrough.StartsWith($containerRoot + $sep)) {
                        return $passThrough
                    }
                }
            }
        }
        if ([string]::IsNullOrWhiteSpace($Project)) {
            $stamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMdd-HHmmss")
            $hex = -join (1..6 | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
            $Project = "session-$stamp-$hex"
        }
    }

    # Reject path traversal / root-collapse in the resolved slug. Applies to
    # explicit -Project, derived, and auto-generated slugs alike: must be a
    # single path-safe segment matching the same allowlist used for derivation.
    # This rejects '.', empty/whitespace, any path separator, '..' segments,
    # and any non-allowlist characters (so e.g. -Project '.' cannot collapse to
    # the bare /workspace root and 'foo/bar' cannot create nested dirs).
    if ([string]::IsNullOrWhiteSpace($Project) -or
        $Project -eq '.' -or
        $Project -match '[/\\]' -or
        ($Project -split '[\\/]') -contains '..' -or
        $Project -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
        throw "Invalid project slug '$Project': must be a single path-safe segment (rejected '.', path separators, '..', and non-allowlist characters)."
    }

    $containerDir = "$containerRoot/$Project"

    # ROOT GUARD: never allow the bare /workspace root as a project.
    if ($containerDir.TrimEnd('/') -eq $containerRoot) {
        throw "Refusing to use workspace root '$containerRoot' as a project; a project subdir is required."
    }

    # Ensure the host-side project dir exists as a git repo (for worktrees).
    if (-not [string]::IsNullOrWhiteSpace($HostWorkspaceDir)) {
        Initialize-ProjectWorkspace -WorkspaceRoot $HostWorkspaceDir -Project $Project | Out-Null
    }

    return $containerDir
}
