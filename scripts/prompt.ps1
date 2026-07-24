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
    $Model = "zai-coding-plan/glm-5",
    [Parameter()]
    [String]
    $Agent = "orchestrator",
    [Parameter()]
    [String]
    $Variant = "",
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
    $Auto = "true",
    [Parameter()]
    [String]
    $Thinking = "true",
    [Parameter()]
    [String]
    $Prompt,
    [Parameter()]
    [String]
    $PromptFile,
    [Parameter()]
    [String]
    $Project
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

# Always resolve the workspace to an isolated per-project subdir. The bare
# /workspace root is never used as a project (see Resolve-ProjectWorkspace).
# The webhook receiver invokes this script with -Workspace /workspace/<slug>
# and no -Project; Resolve-ProjectWorkspace derives the slug from that path.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "init-project-workspace.ps1")
$hostWorkspaceDir = Get-WorkspaceDirFromEnvOrDotEnv
$Workspace = Resolve-ProjectWorkspace -Workspace $Workspace -Project $Project -HostWorkspaceDir $hostWorkspaceDir

# opencode boolean flags (--thinking, --auto, --print-logs) take NO argument
# (yargs [boolean]); passing an explicit value (e.g. "--auto true") leaks
# "true" as a positional message token, corrupting the prompt. Include each
# flag only when its (string) param is truthy.
$runArgs = @(
    "run",
    "--attach", $ServerUrl,
    "--dir",    $Workspace,
    "--model",  $Model,
    "--agent",  $Agent,
    "--format", $Format,
    "--log-level", $LogLevel
)
if ($Thinking -eq 'true')  { $runArgs += "--thinking" }
if ($Auto -eq 'true')      { $runArgs += "--auto" }
if ($PrintLogs -eq 'true') { $runArgs += "--print-logs" }
if ($Variant) {
    $runArgs += @("--variant", $Variant)
}
$runArgs += $Prompt
opencode @runArgs
