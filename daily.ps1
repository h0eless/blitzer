# daily.ps1 - fully automatic refresh: scrape -> build -> publish.
#
# Runs from Task Scheduler (07:00 and 16:00) or by hand:
#     powershell -ExecutionPolicy Bypass -File .\daily.ps1
#
# What it does, in order:
#   1. scrape all towns + sample the live feed (mobile_hotspots.py)
#   2. rebuild the single-file app and copy it to index.html
#   3. commit index.html + hotspots.sqlite3 and push to GitHub Pages
#   4. print what is new in the archive
#
# Every step is appended to logs\daily-YYYY-MM.log, so a failed night is not
# silent. Pushing is best effort: if the network or GitHub is down, the local
# archive is still updated and the next run publishes everything.
#
# Register the task once (PowerShell, no admin needed):
#     powershell -ExecutionPolicy Bypass -File .\install_task.ps1
# or see the schtasks variant at the bottom of this file.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$orte = "Siegen, Kreuztal, Netphen, Hilchenbach, Wilnsdorf, Freudenberg, Neunkirchen, Burbach"

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily-" + (Get-Date -Format "yyyy-MM") + ".log")

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

Log "=== start ($env:COMPUTERNAME) ==="

try {
    Log "scrape: town pages + live feed"
    & python mobile_hotspots.py sites --live --auto-export 2>&1 |
        Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "scraper failed" }

    Log "build app"
    & python make_app.py --title "Blitzer Siegen" --orte $orte 2>&1 |
        Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) { throw "app build failed" }

    Copy-Item (Join-Path $root "blitzer_app.html") (Join-Path $root "index.html") -Force

    Log "publish"
    git add index.html hotspots.sqlite3
    $pending = git status --porcelain -- index.html hotspots.sqlite3
    if ($pending) {
        git commit --quiet -m ("Daily update " + (Get-Date -Format "yyyy-MM-dd"))
        git fetch --quiet origin
        $behind = [int](git rev-list --count HEAD..origin/main)
        if ($behind -gt 0) {
            Log "remote moved, rebasing"
            git pull --rebase --quiet
        }
        git push --quiet origin main
        Log "pushed to origin/main"
    }
    else {
        Log "nothing changed, no commit"
    }

    Log "new in archive"
    & python mobile_hotspots.py new --days 7 2>&1 |
        Tee-Object -FilePath $log -Append

    Log "=== done ==="
}
catch {
    Log ("FAILED: " + $_.Exception.Message)
    exit 1
}

# ---------------------------------------------------------------------------
# Register it (one time). PowerShell:
#
#   $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument `
#        '-WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\vladi\Documents\Standardprojekt\daily.ps1"'
#   $t = New-ScheduledTaskTrigger -Daily -At 07:00
#   $t2 = New-ScheduledTaskTrigger -Daily -At 16:00
#   $s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
#        -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
#   Register-ScheduledTask -TaskName "Blitzer Archiv" -Action $a -Trigger $t,$t2 `
#        -Settings $s -Description "Scrape blitzer.de, rebuild the app, publish it." -Force
#
# cmd.exe variant (one trigger only):
#
#   schtasks /Create /TN "Blitzer Archiv" /SC DAILY /ST 07:00 /TR ^
#     "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File \"C:\Users\vladi\Documents\Standardprojekt\daily.ps1\""
# ---------------------------------------------------------------------------
