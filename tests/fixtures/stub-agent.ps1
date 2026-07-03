<#
.SYNOPSIS
  Test-only stub "agent" for the BeadsLoop e2e dispatch test.

.DESCRIPTION
  Invoked EXACTLY like the production prompt script by the loop's
  `_spawn_agent`/`_prompt_script_invocation` path:
      pwsh -File stub-agent.ps1 -ServerUrl ... -Workspace ... -Model ... -Agent ... -PromptFile <path>
  Instead of dispatching to opencode, it closes the assigned bead via a REAL
  `br close <id>` so the loop exercises real command execution, real stdout/stderr
  streaming, real `proc.wait()`, and the subsequent real `_check_bead_status`.

  The loop injects `BD_DB` (path to the project's beads.db) into the spawned
  process environment; the stub runs with no project cwd, so it MUST rely on
  `BD_DB` (not auto-discovery) to find the database.

.PARAMETER PromptFile
  Path to the bead prompt written by `_build_bead_prompt`. Contains the line
  "You have been assigned Bead <id>: <title>" from which the bead id is parsed.

.NOTES
  Test fixture only. Never used in production. Lives under tests/fixtures/ so it
  is clearly outside the production scripts/ tree.
#>
param(
    [string]$ServerUrl,
    [string]$Workspace,
    [string]$Model,
    [string]$Agent,
    [string]$PromptFile
)

if (-not $PromptFile) {
    [Console]::Error.WriteLine("stub-agent: -PromptFile is required")
    exit 2
}
if (-not $env:BD_DB) {
    [Console]::Error.WriteLine("stub-agent: BD_DB env var is required")
    exit 3
}

if (-not (Test-Path -LiteralPath $PromptFile)) {
    [Console]::Error.WriteLine("stub-agent: prompt file not found: $PromptFile")
    exit 4
}

$content = Get-Content -Raw -LiteralPath $PromptFile
if (-not $content) {
    [Console]::Error.WriteLine("stub-agent: prompt file is empty: $PromptFile")
    exit 5
}

# Prompt line emitted by _build_bead_prompt:
#   "You have been assigned Bead <id>: <title>"
# Bead ids are slug-hash (lowercase alnum + . _ -).
if ($content -match "assigned Bead\s+([A-Za-z0-9._-]+):") {
    $beadId = $Matches[1]
} else {
    [Console]::Error.WriteLine("stub-agent: could not extract bead id from prompt")
    exit 6
}

Write-Output "STUB-AGENT closing bead $beadId (db=$env:BD_DB)"
& br close $beadId
$code = $LASTEXITCODE
if ($null -ne $code -and $code -ne 0) {
    [Console]::Error.WriteLine("stub-agent: br close failed (exit $code) for $beadId")
    exit $code
}
Write-Output "STUB-AGENT closed $beadId"
exit 0
