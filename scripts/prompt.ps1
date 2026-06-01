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
    $Model = "zai-coding-plan/glm-4.7-flash",
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
    [Parameter(Mandatory = $true)]
    [String]
    $Prompt,
)

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
