#! /usr/bin/env pwsh

[CmdletBinding()]
param (    
    [Parameter()]
    [String]
    $ServerUrl,
    [Parameter()]
    [String]
    $Workspace = "/workspace",
    [Parameter()]
    [String]
    $Model = "zai-coding-plan/glm-4.7",
    [Parameter()]
    [String]
    $Agent = "orchestrator",
    [Parameter()]
    [String]
    $Format = "default",
    [Parameter()]
    [String]
    $LogLevel = "INFO",
    [Parameter()]
    [String]
    $PrintLogs = "true",
    [Parameter()]
    [String]
    $DangerouslySkipPermissions = "true",
    [Parameter()]
    [String]
    $Thinking = "true",
    [Parameter()]
    [String]
    $Prompt,
    [Parameter()]
    [String]
    $PromptFile
)

if (-not $ServerUrl) {
    if ($env:OPENCODE_SERVER_URL) {
        $ServerUrl = $env:OPENCODE_SERVER_URL
    } elseif ($env:OPENCODE_HOST -or $env:OPENCODE_PORT) {
        $host_ = if ($env:OPENCODE_HOST) { $env:OPENCODE_HOST } else { "localhost" }
        $port_ = if ($env:OPENCODE_PORT) { $env:OPENCODE_PORT } else { "4099" }
        $ServerUrl = "http://${host_}:${port_}"
    } else {
        $ServerUrl = "http://localhost:4099"
    }
}

if ($PromptFile) {
    if (-not (Test-Path -LiteralPath $PromptFile)) {
        throw "PromptFile not found: $PromptFile"
    }
    $Prompt = Get-Content -LiteralPath $PromptFile -Raw
}
if (-not $Prompt) {
    throw "Provide -Prompt or -PromptFile."
}

# Ensure the workspace directory exists. When attaching to a container server
# with a host bind mount at /workspace, the host-side subdir must exist before
# opencode resolves --dir (server-side). $WORKSPACE_DIR points at the host root
# mounted at /workspace; derive the host path and create it if needed.
if ($env:WORKSPACE_DIR -and $Workspace -and $Workspace.StartsWith('/workspace')) {
    $relativePath = $Workspace -replace '^/workspace/?', ''
    if ($relativePath) {
        # Guard against path traversal: reject any '..' segments so $Workspace
        # cannot create directories outside $WORKSPACE_DIR.
        if ($relativePath -split '/' -notcontains '..') {
            $rootPath   = (Resolve-Path -LiteralPath $env:WORKSPACE_DIR).Path.TrimEnd('/\') + [IO.Path]::DirectorySeparatorChar
            $hostPath   = Join-Path $env:WORKSPACE_DIR $relativePath
            $resolved   = [System.IO.Path]::GetFullPath($hostPath).TrimEnd('/\') + [IO.Path]::DirectorySeparatorChar
            if ($resolved.StartsWith($rootPath)) {
                if (-not (Test-Path -LiteralPath $hostPath)) {
                    New-Item -ItemType Directory -Force -Path $hostPath | Out-Null
                }
            }
        }
    }
}

opencode run `
    --attach $ServerUrl `
    --dir $Workspace `
    --model $Model `
    --agent $Agent `
    --thinking $Thinking `
    --dangerously-skip-permissions $DangerouslySkipPermissions `
    --format $Format `
    --print-logs $PrintLogs `
    --log-level $LogLevel `
    $Prompt
