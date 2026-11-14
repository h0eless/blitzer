# Blitzer Siegen

A personal replacement for the "Mobile Blitzer Hotspots" layer that blitzer.de
removed from its app and web map, for Siegen / Kreuztal and the surrounding
towns (Netphen, Hilchenbach, Wilnsdorf, Freudenberg, Neunkirchen, Burbach).

Two pieces:

- **`mobile_hotspots.py`** — the data engine. Scrapes the public town pages and
  the live report feed into SQLite, keeps a permanent archive, exports
  GeoJSON / KML / GPX / `map.html`.
- **`make_app.py`** — bakes the database into one self-contained HTML file
  (`blitzer_app.html`, published as `index.html`) that installs on an iPhone via
  Safari → *Zum Home-Bildschirm*. No server, no build step.

Everything the app shows comes from public blitzer.de data.

## Quick start

```powershell
# one full run: town pages + live feed -> db -> exports
python mobile_hotspots.py sites --live --auto-export

# build / rebuild the installable app (no network needed)
python make_app.py --title "Blitzer Siegen" --orte "Siegen, Kreuztal, Netphen, Hilchenbach, Wilnsdorf, Freudenberg, Neunkirchen, Burbach"

# or let the daily script do both
powershell -ExecutionPolicy Bypass -File .\daily.ps1
```

Upload `blitzer_app.html` as `index.html` to any static host (GitHub Pages,
Netlify) and add it to the home screen once.

This repo is set up the same way: GitHub Pages serves `index.html` from `main`
at <https://h0eless.github.io/blitzer/>. `daily.ps1` writes the build to
`blitzer_app.html` and copies it over `index.html`, so publishing is one push:

```powershell
git add index.html
git commit -m "Daily update"
git push
```

## What the app shows

- **Heat circles** — mobile spots, sized and coloured by how many days they were
  measured (1, 2–4, 5–9, 10+).
- **Blue squares** — fixed cameras, with the speed limit inside.
- **Pink rings** — currently live-reported mobile traps, limit inside, sonar
  pulse while fresh (< 4 h). They age out after 12 h.
- **Grey dashed circles** — archive: the spot is no longer listed, but it stays
  on the map as a hint where traps have stood before.
- **Town bar** — filter map, list and archive by town.
- **Neu tab** — what is new in the archive (7 / 30 days), fixed cameras, your
  whole database, and the archive.

## How the data works

| Source | Used for |
| --- | --- |
| `/ort/mobile-messstellen-in-<Ort>-<Land>` | every mobile spot of a town |
| `/ort/blitzer-in-<Ort>-<Land>` | fixed cameras (`#feste`) + top-12 mobile block |
| `/detail/...` | exact pin (SVG circle, Web-Mercator z=16) + monthly history |
| `/karte/api/markers` | live feed, cluster markers subdivided recursively |

- A URL is matched to an existing row of the same kind within **15 m** and
  re-keyed instead of duplicated, so a re-listed camera is the same place.
- A live report within **250 m** of an archived mobile spot is folded into it
  (`live`, `live_at`) instead of creating a second point.
- `first_seen` is insert-only; `meta.seeded_at` marks the baseline. Spots that
  vanish become `active=0` and are never deleted.
- `mark_missing()` only archives when a scrape covered at least 50 % of the
  previously active spots per town, so a broken page cannot wipe the archive.

## Commands

```powershell
python mobile_hotspots.py sites --live --auto-export   # daily: pages + feed + exports
python mobile_hotspots.py once                         # live feed, one sweep
python mobile_hotspots.py watch --interval 900         # live feed, every 15 min
python mobile_hotspots.py new --days 7                 # what appeared recently
python mobile_hotspots.py stats                        # archive / live / seen counts
python mobile_hotspots.py export                       # rebuild exports from the db
```

Flags: `--orte "A-Land,B-Land"`, `--bbox sw,sw,ne,ne`, `--no-fixed`,
`--reseed` (treat current inventory as Bestand), `--db`, `--out`.

## Daily run

`daily.ps1` scrapes all towns, samples the live feed, rebuilds the app and
prints what is new. Register it once:

```powershell
schtasks /Create /TN "Blitzer Archiv" /SC DAILY /ST 07:00 /TR 'powershell -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\vladi\Documents\Standardprojekt\daily.ps1"'
```

The script only rebuilds the files locally — commit and push `index.html` after
it to update the phone.

For fresher "live" pins during the day, run the watcher in a second window:

```powershell
python mobile_hotspots.py watch --interval 900 --auto-export
```

## Files

| File | Purpose |
| --- | --- |
| `mobile_hotspots.py` | scraper, archive schema, dedupe, clustering, exports, CLI |
| `make_app.py` | generates `blitzer_app.html` from the database |
| `blitzer_app.html` | local build output (ignored by git) |
| `index.html` | the published app — this is what GitHub Pages serves |
| `hotspots.sqlite3` | archive: `webspots`, `sightings`, `sighting_days`, `meta` |
| `daily.ps1` | daily scrape + rebuild + "new" report |
| `hotspots_out/` | generated GeoJSON / KML / GPX / Leaflet page |
| `siegen-hotspots/` | the first PowerShell prototype of the same idea |
