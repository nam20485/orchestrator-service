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
    [ValidateSet("DEBUG", "INFO", "WARN", "ERROR")]
    [String]
    $LogLevel = "INFO",
    [Parameter()]
    [Switch]
    $PrintLogs,
    [Parameter()]
    [Switch]
    $Continue,
    [Parameter()]
    [String]
    $Session,
    [Parameter()]
    [Switch]
    $Fork,
    [Parameter()]
    [Switch]
    $Pure,
    [Parameter()]
    [String]
    $Password,
    [Parameter()]
    [String]
    $Username
)

# Resolve server URL (same precedence as prompt.ps1):
#   explicit arg  >  $OPENCODE_SERVER_URL  >  $OPENCODE_HOST/$OPENCODE_PORT  >  http://localhost:4099
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

# Basic auth: prefer explicit args, else fall back to host env vars
# (the opencode CLI itself also reads OPENCODE_SERVER_PASSWORD / OPENCODE_SERVER_USERNAME).
if (-not $Password -and $env:OPENCODE_SERVER_PASSWORD) { $Password = $env:OPENCODE_SERVER_PASSWORD }
if (-not $Username -and $env:OPENCODE_SERVER_USERNAME) { $Username = $env:OPENCODE_SERVER_USERNAME }

# Build the invocation, only emitting flags that are actually set.
$cmdArgs = @("attach", $ServerUrl, "--dir", $Workspace, "--log-level", $LogLevel)
if ($PrintLogs)    { $cmdArgs += "--print-logs" }
if ($Continue)     { $cmdArgs += "--continue" }
if ($Session)      { $cmdArgs += "--session", $Session }
if ($Fork)         { $cmdArgs += "--fork" }
if ($Pure)         { $cmdArgs += "--pure" }
if ($Password)     { $cmdArgs += "--password", $Password }
if ($Username)     { $cmdArgs += "--username", $Username }

& opencode @cmdArgs
