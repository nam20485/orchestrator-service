#! /usr/bin/env pwsh

# Shared helper: initialize a per-project workspace directory + git repo.
# Dot-sourced by scripts/prompt.ps1 and scripts/attach.ps1.
#
# Contract (must match webhook_receiver/workspace.py):
#   - worktree dir name: .worktrees/
#   - git default branch: main
#   - .git/info/exclude entry: .worktrees/

function Initialize-ProjectWorkspace {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$WorkspaceRoot,

        [Parameter(Mandatory)]
        [string]$Project
    )

    # Guard against path traversal: reject any '..' segment.
    if (($Project -split '[/\]') -contains '..') {
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
