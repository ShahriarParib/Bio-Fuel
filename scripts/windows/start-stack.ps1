# Start the whole stack on a Windows laptop where Docker Engine runs inside WSL (no Docker Desktop).
#
# Why the keep-alive: WSL shuts its VM down when no Windows process is attached to it, and every container dies with
# it (measured during development: the stack vanished after a few idle minutes). A hidden `sleep infinity` keeps the
# VM, and therefore Docker, running for as long as Windows is up.
#
# Usage (PowerShell):  .\scripts\windows\start-stack.ps1 [-Obs]
param([switch]$Obs, [string]$Distro = "Ubuntu")

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$wslRepo = "/mnt/" + $repo.Substring(0, 1).ToLower() + $repo.Substring(2).Replace("\", "/")

$alive = Get-CimInstance Win32_Process -Filter "Name = 'wsl.exe'" |
    Where-Object { $_.CommandLine -match "sleep infinity" }
if (-not $alive) {
    Start-Process -WindowStyle Hidden -FilePath "wsl.exe" -ArgumentList "-d", $Distro, "--", "sleep", "infinity"
    Write-Host "WSL keep-alive started"
} else {
    Write-Host "WSL keep-alive already running"
}

$prof = if ($Obs) { "--profile obs" } else { "" }
wsl -d $Distro -- bash -lc "cd '$wslRepo' && [ -f .env ] || cp .env.example .env; docker compose $prof up -d --build && bash scripts/smoke_test.sh"
Write-Host "Console: http://localhost:3000   Simulator admin: http://localhost:8000/admin"
