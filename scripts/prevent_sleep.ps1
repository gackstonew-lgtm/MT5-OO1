<#
.SYNOPSIS
    Queries Windows power settings and displays the recommended commands to prevent
    sleep and hibernation while running automated algorithmic trading.
.DESCRIPTION
    Strictly read-only: inspects current active scheme, AC standby timeout, and hibernate timeout.
    Outputs the exact powercfg commands for the human operator to review and apply manually.
#>

Write-Host "==================================================================" -ForegroundColor Cyan
Write-Host "  MT5 & Bridge Always-On Power Settings Auditor (Read-Only)" -ForegroundColor Cyan
Write-Host "==================================================================" -ForegroundColor Cyan

# 1. Query Active Scheme
$ActiveSchemeRaw = (powercfg /getactivescheme) 2>&1
Write-Host "`n[CURRENT ACTIVE POWER PLAN]" -ForegroundColor Yellow
Write-Host $ActiveSchemeRaw

# 2. Query Standby & Hibernate Configuration on AC Power
Write-Host "`n[CURRENT SLEEP & HIBERNATE TIMEOUTS]" -ForegroundColor Yellow
$QueryStandby = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE) 2>&1 | Select-String "Current AC Power Setting Index"
$QueryHibernate = (powercfg /query SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE) 2>&1 | Select-String "Current AC Power Setting Index"

if ($QueryStandby) {
    Write-Host "  Standby Idle Timeout (AC):   $QueryStandby"
} else {
    Write-Host "  Standby Idle Timeout (AC):   (Standard default active)"
}

if ($QueryHibernate) {
    Write-Host "  Hibernate Idle Timeout (AC): $QueryHibernate"
} else {
    Write-Host "  Hibernate Idle Timeout (AC): (Standard default active)"
}

Write-Host "`n[RECOMMENDED ACTIONS FOR 24/7 ALGORITHMIC TRADING]" -ForegroundColor Green
Write-Host "To prevent the PC or VPS from sleeping during trading hours and breaking bridge communication,"
Write-Host "execute the following commands in an elevated PowerShell/CMD terminal:`n"

Write-Host "  # 1. Disable Standby / Sleep when plugged in (AC):" -ForegroundColor White
Write-Host "  powercfg /change standby-timeout-ac 0`n" -ForegroundColor Cyan

Write-Host "  # 2. Disable Hibernation when plugged in (AC):" -ForegroundColor White
Write-Host "  powercfg /change hibernate-timeout-ac 0`n" -ForegroundColor Cyan

Write-Host "  # 3. Optional: Set Display Monitor timeout to 15 minutes (saves power while bot continues):" -ForegroundColor White
Write-Host "  powercfg /change monitor-timeout-ac 15`n" -ForegroundColor Cyan

Write-Host "NOTE: Per repository safety constraints, this script does NOT alter system settings silently." -ForegroundColor Magenta
Write-Host "Please copy and run the commands above in an Administrator prompt if desired."
Write-Host "==================================================================" -ForegroundColor Cyan
