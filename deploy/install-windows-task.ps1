<#
.SYNOPSIS
    Runs the attendance service in the background on Windows: starts at boot (no login needed),
    restarts it if it crashes, logs to data\service.log. Safe to run again.

.EXAMPLE
    # PowerShell as Administrator, from the project folder:
    powershell -ExecutionPolicy Bypass -File deploy\install-windows-task.ps1
#>
param([string]$TaskName = "Attendance bot")

$ErrorActionPreference = "Stop"
$dir = (Resolve-Path "$PSScriptRoot\..").Path
$exe = "$dir\.venv\Scripts\attendance.exe"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Run this script in PowerShell as Administrator." }
if (-not (Test-Path $exe)) { throw "$exe not found. Run 'uv sync' in $dir first." }
if (-not (Test-Path "$dir\.env")) { throw "$dir\.env not found. Copy it from the other machine first." }
New-Item -ItemType Directory -Force "$dir\data" | Out-Null

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# shortcut: service.log grows without limit (a few lines per hour); add rotation if it ever gets big.
$action = New-ScheduledTaskAction -Execute "cmd.exe" -WorkingDirectory $dir `
    -Argument "/c `"`"$exe`" run >> `"$dir\data\service.log`" 2>&1`""
$trigger = New-ScheduledTaskTrigger -AtStartup
# ExecutionTimeLimit 0: otherwise Windows kills the task after 72 h. IgnoreNew: two bots would conflict in Telegram.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -User "SYSTEM" -RunLevel Highest | Out-Null
Start-ScheduledTask -TaskName $TaskName

Start-Sleep 5
Write-Host "Task '$TaskName': $((Get-ScheduledTask -TaskName $TaskName).State)" -ForegroundColor Green
Write-Host "Log:  Get-Content `"$dir\data\service.log`" -Tail 20 -Wait"
Write-Host "Make sure the PC never sleeps:  powercfg /change standby-timeout-ac 0"
