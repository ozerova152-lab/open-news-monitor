<#!
.SYNOPSIS
Creates a local Python environment and installs Open News Monitor on Windows.

.DESCRIPTION
Run this script once after downloading the repository. It does not create a
monitor, send a message, or request any credentials.
#>

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonLauncher = Get-Command py -ErrorAction SilentlyContinue

if (-not $PythonLauncher) {
    throw 'Python launcher "py" was not found. Install Python 3.11 or newer from https://www.python.org/downloads/windows/ and run this script again.'
}

Push-Location $ProjectRoot
try {
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        & py -3 -m venv .venv
    }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install -e .
    Write-Host ''
    Write-Host 'Open News Monitor is ready.' -ForegroundColor Green
    Write-Host 'Next step: run .\scripts\create-monitor.ps1 to create your first monitor.'
}
finally {
    Pop-Location
}
