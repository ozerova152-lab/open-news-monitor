<#!
.SYNOPSIS
Starts the safe first-monitor interview on Windows.

.DESCRIPTION
The profile is saved locally in config\local and is excluded from Git.
#>

param(
    [string]$Name = 'my-monitor'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
    throw 'The local environment is not ready. Run .\scripts\setup-windows.ps1 first.'
}

$SafeName = $Name -replace '[^a-zA-Z0-9_-]', '-'
if ([string]::IsNullOrWhiteSpace($SafeName)) { $SafeName = 'my-monitor' }
$ProfilePath = Join-Path $ProjectRoot "config\local\$SafeName.json"

Push-Location $ProjectRoot
try {
    & $Python -m open_news_monitor init-monitor $ProfilePath
}
finally {
    Pop-Location
}
