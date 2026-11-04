<#
.SYNOPSIS
    Uninstalls the MT5 Python Bridge service and cleans up tasks and processes.
#>

[CmdletBinding()]
param(
    [string]$ServiceName = "MT5BridgeService"
)

$ErrorActionPreference = "SilentlyContinue"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path

Write-Host "==================================================================" -ForegroundColor Yellow
Write-Host "  MT5 Python Bridge Service Uninstaller" -ForegroundColor Yellow
Write-Host "==================================================================" -ForegroundColor Yellow

# 1. Check & Remove NSSM Windows Service
$NssmCmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
$NssmPath = if ($NssmCmd) { $NssmCmd.Source } elseif (Test-Path "$ProjectRoot\tools\nssm.exe") { "$ProjectRoot\tools\nssm.exe" } else { $null }

if ($NssmPath) {
    & $NssmPath stop $ServiceName 2>&1 | Out-Null
    & $NssmPath remove $ServiceName confirm 2>&1 | Out-Null
}
& sc.exe stop $ServiceName 2>&1 | Out-Null
& sc.exe delete $ServiceName 2>&1 | Out-Null

# 2. Check & Remove Task Scheduler Task
$Task = Get-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
if ($Task) {
    Stop-ScheduledTask -TaskName $ServiceName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $ServiceName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Unregistered Scheduled Task '$ServiceName'." -ForegroundColor Green
}

# 3. Kill any orphan bridge runner processes
$OrphanProcs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*run_bridge_server.py*" }
foreach ($p in $OrphanProcs) {
    Write-Host "Terminating bridge process PID $($p.ProcessId)..." -ForegroundColor Yellow
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

Write-Host "`n[SUCCESS] Uninstallation of '$ServiceName' complete." -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Yellow
