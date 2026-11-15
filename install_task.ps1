# install_task.ps1 - register the automatic refresh in Windows Task Scheduler.
#
#     powershell -ExecutionPolicy Bypass -File .\install_task.ps1
#
# Creates (or replaces) the task "Blitzer Archiv":
#   - runs daily.ps1 at 07:00 and 16:00
#   - catches up if the PC was off: StartWhenAvailable
#   - keeps running on battery, gives up after one hour
#
# Remove it again:
#     Unregister-ScheduledTask -TaskName "Blitzer Archiv" -Confirm:$false

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $root "daily.ps1"

if (-not (Test-Path $script)) { throw "daily.ps1 not found next to install_task.ps1" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ('-WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $script + '"')

$morning = New-ScheduledTaskTrigger -Daily -At 07:00
$evening = New-ScheduledTaskTrigger -Daily -At 16:00

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName "Blitzer Archiv" `
    -Action $action -Trigger $morning, $evening -Settings $settings `
    -Description "Scrape blitzer.de, rebuild the app, publish it to GitHub Pages." `
    -Force | Out-Null

$task = Get-ScheduledTask -TaskName "Blitzer Archiv"
$info = Get-ScheduledTaskInfo -TaskName "Blitzer Archiv"
Write-Host ("Task {0}: {1}" -f $task.TaskName, $task.State) -ForegroundColor Green
Write-Host ("Next run: {0}" -f $info.NextRunTime)
Write-Host "Logs: $root\logs\daily-YYYY-MM.log"
