# daily.ps1 - fully automatic refresh: scrape -> build -> publish.
#
# Runs from Task Scheduler (07:00 and 16:00) or by hand:
#     powershell -ExecutionPolicy Bypass -File .\daily.ps1
# or just double-click update.cmd, which keeps the window open so an error
# can actually be read.
#
# What it does, in order:
#   1. scrape all towns + sample the live feed (mobile_hotspots.py)
#   2. rebuild the single-file app and copy it to index.html
#   3. commit index.html (+ the db once a day, in the morning) and push
#   4. print what is new in the archive
#
# Every step is appended to logs\daily-YYYY-MM.log as utf-8. A python
# traceback lands in logs\error.log; the console gets one readable line.
# Pushing is best effort: if the network or GitHub is down, the local archive
# is still updated and the next run publishes everything.
#
# Register the task once (PowerShell, no admin needed):
#     powershell -ExecutionPolicy Bypass -File .\install_task.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$orte = "Siegerland & Umgebung - Umkreis 30 km um Siegen"

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily-" + (Get-Date -Format "yyyy-MM") + ".log")

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

# Windows PowerShell 5.1 turns the first stderr line of a native program into
# a terminating error when $ErrorActionPreference is Stop and stderr is
# redirected - which used to abort the whole run on the first traceback line
# and hide the real message. Run-Log relaxes the preference around the call
# and writes every line, stdout and stderr alike, to console and log as utf-8.
function Run-Log([scriptblock]$cmd) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $cmd 2>&1 | ForEach-Object {
            $line = "$_"
            Write-Host $line
            Add-Content -Path $log -Value $line -Encoding utf8
        }
    }
    finally {
        $ErrorActionPreference = $prev
    }
    return $LASTEXITCODE
}

# One run at a time: the scheduled run and a manual run must not both open
# hotspots.sqlite3. A leftover lock whose process is gone is simply stale.
$lock = Join-Path $logDir "running.lock"
if (Test-Path $lock) {
    $lockPid = Get-Content $lock -ErrorAction SilentlyContinue
    $alive = $false
    if ($lockPid) { $alive = [bool](Get-Process -Id $lockPid -ErrorAction SilentlyContinue) }
    if ($alive) {
        Log "another run is still active (pid $lockPid) - skipping"
        exit 0
    }
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
$PID | Set-Content $lock

try {
    Log "=== start ($env:COMPUTERNAME) ==="

    Log "scrape: town pages + live feed"
    $code = Run-Log { python mobile_hotspots.py sites --live --auto-export }
    if ($code -ne 0) { throw "scraper failed (exit $code)" }

    Log "build app"
    $code = Run-Log { python make_app.py --title "Blitzer Siegen" --orte $orte }
    if ($code -ne 0) { throw "app build failed (exit $code)" }

    Copy-Item (Join-Path $root "blitzer_app.html") (Join-Path $root "index.html") -Force

    Log "publish"
    git add index.html
    # hotspots.sqlite3 is a binary blob; committing it twice a day only grows
    # the repository history. The morning run publishes it once a day.
    if ((Get-Date).Hour -lt 12) { git add hotspots.sqlite3 }
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
    Run-Log { python mobile_hotspots.py new --days 7 } | Out-Null

    Log "=== done ==="
}
catch {
    Log ("FAILED: " + $_.Exception.Message)
    exit 1
}
finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
}
