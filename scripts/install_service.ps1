<#
.SYNOPSIS
    Installs the MT5 Python Bridge as an automatic Windows Service with NSSM (preferred)
    or Task Scheduler fallback.
.DESCRIPTION
    Configures automatic start at system boot, restart on failure with delay (5s+),
    execution under the current user, loading of .env, and rotating log files.
#>

[CmdletBinding()]
param(
    [switch]$UseTaskScheduler = $false,
    [string]$ServiceName = "MT5BridgeService"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$PythonExe = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $PythonExe) {
    $PythonExe = "python.exe"
}
$ScriptPath = Join-Path $ProjectRoot "scripts\run_bridge_server.py"
$LogsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$StdoutLog = Join-Path $LogsDir "service_stdout.log"
$StderrLog = Join-Path $LogsDir "service_stderr.log"

$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  MT5 Python Bridge Service Installer" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "Project Directory: $ProjectRoot"
Write-Host "Python Executable: $PythonExe"
Write-Host "Target Script:     $ScriptPath"
Write-Host "Service Name:      $ServiceName"
Write-Host "Admin Privileges:  $IsAdmin"

if (-not $IsAdmin) {
    Write-Host "`n[ERROR] Administrator privileges are required to register a Windows Service or Scheduled Task." -ForegroundColor Red
    Write-Host "To install the bridge service:" -ForegroundColor Yellow
    Write-Host "  1. Right-click PowerShell and select 'Run as Administrator'" -ForegroundColor Yellow
    Write-Host "  2. cd `"$ProjectRoot`"" -ForegroundColor Yellow
    Write-Host "  3. powershell -ExecutionPolicy Bypass -File scripts/install_service.ps1" -ForegroundColor Yellow
    Write-Host "`nFor non-admin background execution, you can use: scripts\start_bridge.bat" -ForegroundColor Cyan
    exit 1
}

# Locate NSSM
$NssmPath = $null
$NssmCmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
if ($NssmCmd) {
    $NssmPath = $NssmCmd.Source
} elseif (Test-Path "$ProjectRoot\tools\nssm.exe") {
    $NssmPath = "$ProjectRoot\tools\nssm.exe"
}

# If Admin and NSSM available (or downloadable) and not explicitly forced to Task Scheduler
if ($IsAdmin -and -not $UseTaskScheduler) {
    if (-not $NssmPath) {
        Write-Host "NSSM not found locally. Attempting to download NSSM to tools\ directory..." -ForegroundColor Yellow
        $ToolsDir = Join-Path $ProjectRoot "tools"
        if (-not (Test-Path $ToolsDir)) { New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null }
        $NssmZip = Join-Path $ToolsDir "nssm.zip"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $NssmZip -TimeoutSec 10
            Expand-Archive -Path $NssmZip -DestinationPath (Join-Path $ToolsDir "extracted") -Force
            Copy-Item (Join-Path $ToolsDir "extracted\nssm-2.24\win64\nssm.exe") (Join-Path $ToolsDir "nssm.exe") -Force
            Remove-Item $NssmZip -Force -ErrorAction SilentlyContinue
            $NssmPath = Join-Path $ToolsDir "nssm.exe"
        } catch {
            Write-Host "Could not download NSSM automatically: $_. Falling back to Task Scheduler." -ForegroundColor Yellow
        }
    }
}

if ($IsAdmin -and $NssmPath -and -not $UseTaskScheduler) {
    Write-Host "`n[MODE: NSSM WINDOWS SERVICE]" -ForegroundColor Green
    Write-Host "Using NSSM binary: $NssmPath"

    # Stop and remove existing service if present
    & sc.exe query $ServiceName 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Removing pre-existing service $ServiceName..."
        & $NssmPath stop $ServiceName 2>&1 | Out-Null
        & $NssmPath remove $ServiceName confirm 2>&1 | Out-Null
    }

    # Install Service
    Write-Host "Registering $ServiceName with NSSM..."
    & $NssmPath install $ServiceName $PythonExe
    & $NssmPath set $ServiceName AppParameters "`"$ScriptPath`""
    & $NssmPath set $ServiceName AppDirectory "`"$ProjectRoot`""
    & $NssmPath set $ServiceName AppStdout "`"$StdoutLog`""
    & $NssmPath set $ServiceName AppStderr "`"$StderrLog`""
    & $NssmPath set $ServiceName AppRotateFiles 1
    & $NssmPath set $ServiceName AppRotateBytes 10485760  # 10 MB rotation
    & $NssmPath set $ServiceName AppRestartDelay 5000     # 5s restart delay
    & $NssmPath set $ServiceName AppThrottle 5000
    & $NssmPath set $ServiceName Start SERVICE_AUTO_START

    # Start Service
    Write-Host "Starting $ServiceName..."
    & $NssmPath start $ServiceName

    Write-Host "`n[SUCCESS] NSSM Service '$ServiceName' installed and started successfully!" -ForegroundColor Green
    & sc.exe query $ServiceName
} else {
    Write-Host "`n[MODE: TASK SCHEDULER FALLBACK]" -ForegroundColor Yellow
    if (-not $IsAdmin) {
        Write-Host "Note: Running as Standard User. Task Scheduler will register for current user session." -ForegroundColor Cyan
    }

    $TaskName = $ServiceName
    $Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`"" -WorkingDirectory $ProjectRoot
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 0)

    # Unregister if already present
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false

    # Register Task
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "MT5 Python Bridge Daemon Service" | Out-Null

    # Start Task immediately
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "`n[SUCCESS] Scheduled Task '$TaskName' registered and started successfully!" -ForegroundColor Green
    Get-ScheduledTask -TaskName $TaskName
}

Write-Host "==================================================================" -ForegroundColor Cyan
