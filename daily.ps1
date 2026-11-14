# daily.ps1 - refresh the hotspot archive and rebuild the app.
#
# Run it by hand, or let Windows Task Scheduler run it every morning.
# It scrapes all towns, samples the live feed, archives anything new and
# rebuilds blitzer_app.html.
#
#   powershell -ExecutionPolicy Bypass -File .\daily.ps1
#
# Want "live" to stay fresh during the day? Run the feed watcher in a second
# window; it re-samples every 15 minutes and rebuilds the exports:
#
#   python mobile_hotspots.py watch --interval 900 --auto-export
#   python make_app.py --title "Blitzer Siegen" --orte "<same list>"
#
# Register it for 07:00 every day (one time, in an admin console):
#
#   PowerShell (easiest, quotes stay intact):
#     schtasks /Create /TN "Blitzer Archiv" /SC DAILY /ST 07:00 /TR 'powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\vladi\Documents\Standardprojekt\daily.ps1"'
#
#   cmd.exe (note the ^ line break):
#     schtasks /Create /TN "Blitzer Archiv" /SC DAILY /ST 07:00 /TR ^
#       "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File \"C:\Users\vladi\Documents\Standardprojekt\daily.ps1\""
#
# Check or remove it again:
#     schtasks /Query /TN "Blitzer Archiv"
#     schtasks /Delete /TN "Blitzer Archiv" /F

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$orte = "Siegen, Kreuztal, Netphen, Hilchenbach, Wilnsdorf, Freudenberg, Neunkirchen, Burbach"

Write-Host "== scraper ==" -ForegroundColor Cyan
python mobile_hotspots.py sites --live --auto-export
if ($LASTEXITCODE -ne 0) { throw "scraper failed, app not rebuilt" }

Write-Host "`n== app ==" -ForegroundColor Cyan
python make_app.py --title "Blitzer Siegen" --orte $orte
if ($LASTEXITCODE -ne 0) { throw "app build failed" }

# GitHub Pages serves index.html at the repo root, so keep it in sync
Copy-Item (Join-Path $root "blitzer_app.html") (Join-Path $root "index.html") -Force

Write-Host "`n== neu im Archiv ==" -ForegroundColor Cyan
python mobile_hotspots.py new --days 7

Write-Host "`nFertig. blitzer_app.html neu gebaut - hochladen, fertig." -ForegroundColor Green
