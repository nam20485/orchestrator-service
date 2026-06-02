#! /usr/bin/env pwsh

[CmdletBinding()]
param (    
    [Parameter()]
    [String]
    $ServerUrl = "http://localhost:4099",
    [Parameter()]
    [String]
    $Workspace = "/workspace",
    [Parameter()]
    [String]
    $Model = "bailian-payg/qwen3.6-plus",
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

if ($PromptFile) {
    if (-not (Test-Path -LiteralPath $PromptFile)) {
        throw "PromptFile not found: $PromptFile"
    }
    $Prompt = Get-Content -LiteralPath $PromptFile -Raw
}
if (-not $Prompt) {
    throw "Provide -Prompt or -PromptFile."
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
