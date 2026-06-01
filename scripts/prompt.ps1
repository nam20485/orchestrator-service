#! /usr/bin/env pwsh

[CmdletBinding()]
param (
    [Parameter()]
    [String]
    $Prompt
)

opencode run `
    --attach http://localhost:4099 `
    --model zai-coding-plan/glm-4.7 `
    --agent orchestrator `
    --thinking `
    --dangerously-skip-permissions `
    --format json `
    --print-logs `
    $Prompt