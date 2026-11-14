param(
  [string]$City  = 'Siegen',
  [string]$State = 'Nordrhein-Westfalen',
  [string]$OutDir = $PSScriptRoot
)

$ErrorActionPreference = 'Stop'
$base = 'https://www.blitzer.de'
$citySlug  = [uri]::EscapeDataString($City)
$stateSlug = [uri]::EscapeDataString($State)
$listUrl = "$base/ort/mobile-messstellen-in-$citySlug-$stateSlug"

$tmp = Join-Path $env:TEMP ('hotspots_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null

function Fetch-Html([string]$url, [string]$name) {
  $file = Join-Path $tmp "$name.html"
  for ($try = 1; $try -le 4; $try++) {
    & curl.exe -sS -L --compressed --retry 2 --retry-delay 2 -A 'Mozilla/5.0' -o $file $url
    if ($LASTEXITCODE -eq 0) { break }
    Write-Warning "curl attempt $try failed ($LASTEXITCODE) for $url"
    Start-Sleep -Seconds (2 * $try)
  }
  if ($LASTEXITCODE -ne 0) { throw "curl failed ($LASTEXITCODE): $url" }
  Get-Content -Raw -Encoding UTF8 $file
}

function Strip-Html([string]$s) {
  if (-not $s) { return '' }
  $s = [regex]::Replace($s, '(?s)<[^>]+>', ' ')
  $s = [System.Net.WebUtility]::HtmlDecode($s)
  [regex]::Replace($s, '\s+', ' ').Trim()
}

Write-Host "Fetching list: $listUrl"
$listHtml = Fetch-Html $listUrl 'list'

$cardRe = [regex]'<li class="card card--spot"><a href="([^"]+)"><span class="sign" title="([^"]*)">([^<]*)</span><span class="card__txt"><strong>([^<]*)</strong><small>([^<]*)</small></span></a></li>'
$cards = $cardRe.Matches($listHtml)
Write-Host "Found $($cards.Count) hotspots"
if ($cards.Count -eq 0) { throw 'No hotspots parsed - page layout changed?' }

$total = $cards.Count
$records = @()
$i = 0
foreach ($c in $cards) {
  $i++
  $href   = [System.Net.WebUtility]::HtmlDecode($c.Groups[1].Value)
  $title  = $c.Groups[2].Value
  $limit  = ($title -replace '\s*km/h\s*', '').Trim()
  $street = [System.Net.WebUtility]::HtmlDecode($c.Groups[4].Value).Trim()
  $small  = [System.Net.WebUtility]::HtmlDecode($c.Groups[5].Value).Trim()

  $days = 0; $last = ''
  $m = [regex]::Match($small, '(\d+)\s+Messtag(?:e)?\s*\u00B7\s*zuletzt\s*([^\u00B7]+?)\s*\u00B7')
  if ($m.Success) { $days = [int]$m.Groups[1].Value; $last = $m.Groups[2].Value.Trim() }

  $detailUrl = if ($href.StartsWith('http')) { $href } else { $base + $href }
  Write-Host ("  [{0}/{1}] {2}" -f $i, $total, $street)

  $detailHtml = Fetch-Html $detailUrl ("d{0:D2}" -f $i)
  Start-Sleep -Milliseconds 500

  $pin = [regex]::Match($detailHtml, 'class="map__pin" cx="(\d+)" cy="(\d+)"')
  if (-not $pin.Success) { Write-Warning "No map pin for $street - skipping"; continue }

  $cx = [double]$pin.Groups[1].Value
  $cy = [double]$pin.Groups[2].Value
  $world = 256.0 * [math]::Pow(2, 16)
  $lng = $cx / $world * 360.0 - 180.0
  $lat = [math]::Atan([math]::Sinh([math]::PI * (1.0 - 2.0 * $cy / $world))) * 180.0 / [math]::PI

  $addr = Strip-Html ([regex]::Match($detailHtml, '<dt>Adresse</dt><dd>(.*?)</dd>').Groups[1].Value)
  $area = Strip-Html ([regex]::Match($detailHtml, '<dt>Lage</dt><dd>(.*?)</dd>').Groups[1].Value)

  $records += [pscustomobject]@{
    street   = $street
    limit    = $limit
    days     = $days
    last     = $last
    address  = $addr
    area     = $area
    lat      = [math]::Round($lat, 6)
    lng      = [math]::Round($lng, 6)
    url      = $detailUrl
  }
}

if ($records.Count -eq 0) { throw 'No records with coordinates' }
Write-Host "Collected $($records.Count) records with coordinates"

# Sanity check: coordinates should be near the requested city (Siegen: ~50.87 / 8.02)
$avgLat = ($records | Measure-Object lat -Average).Average
$avgLng = ($records | Measure-Object lng -Average).Average
Write-Host ("Center: {0:N4}, {1:N4}" -f $avgLat, $avgLng)

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

# ---------- CSV ----------
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$records |
  Select-Object street, limit, days, last, address, area,
    @{ n = 'lat'; e = { $_.lat.ToString('0.######', $inv) } },
    @{ n = 'lng'; e = { $_.lng.ToString('0.######', $inv) } },
    url |
  Export-Csv -Path (Join-Path $OutDir 'siegen-hotspots.csv') -NoTypeInformation -Encoding UTF8

# ---------- KML ----------
function XmlEscape([string]$s) { [System.Security.SecurityElement]::Escape($s) }
function Num([double]$n) { $n.ToString('0.######', [System.Globalization.CultureInfo]::InvariantCulture) }

$kml = New-Object System.Text.StringBuilder
[void]$kml.AppendLine('<?xml version="1.0" encoding="UTF-8"?>')
[void]$kml.AppendLine('<kml xmlns="http://www.opengis.net/kml/2.2">')
[void]$kml.AppendLine('<Document>')
[void]$kml.AppendLine("  <name>$(XmlEscape($City)) - Mobile Blitzer Hotspots</name>")
[void]$kml.AppendLine('  <description>Messstellen der letzten 12 Monate, sortiert nach Messtagen. Quelle: blitzer.de</description>')
$styles = @(
  @{ id='hot';  color='ff2828d6'; min=15 },
  @{ id='warm'; color='ff0077f7'; min=10 },
  @{ id='mid';  color='ff49bffc'; min=5  },
  @{ id='low';  color='ff9d7b45'; min=0  }
)
foreach ($s in $styles) {
  [void]$kml.AppendLine("  <Style id=""$($s.id)""><IconStyle><color>$($s.color)</color><scale>1.1</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle></Style>")
}
foreach ($r in ($records | Sort-Object days -Descending)) {
  $style = if ($r.days -ge 15) { 'hot' } elseif ($r.days -ge 10) { 'warm' } elseif ($r.days -ge 5) { 'mid' } else { 'low' }
  $name = "$($r.days)x $($r.street) ($($r.limit) km/h)"
  $desc = "<b>$($r.limit) km/h</b> - $($r.days) Messtage, zuletzt $($r.last)<br/>$($r.address)<br/><a href=""$($r.url)"">Details auf blitzer.de</a>"
  [void]$kml.AppendLine('  <Placemark>')
  [void]$kml.AppendLine("    <name>$(XmlEscape($name))</name>")
  [void]$kml.AppendLine("    <description>$(XmlEscape($desc))</description>")
  [void]$kml.AppendLine("    <styleUrl>#$style</styleUrl>")
  [void]$kml.AppendLine("    <Point><coordinates>$(Num $r.lng),$(Num $r.lat),0</coordinates></Point>")
  [void]$kml.AppendLine('  </Placemark>')
}
[void]$kml.AppendLine('</Document>')
[void]$kml.AppendLine('</kml>')
[System.IO.File]::WriteAllText((Join-Path $OutDir 'siegen-hotspots.kml'), $kml.ToString(), (New-Object System.Text.UTF8Encoding($false)))

# ---------- HTML ----------
$json = $records | Sort-Object days -Descending | ConvertTo-Json -Depth 5
$template = @'
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>__CITY__ - Mobile Blitzer Hotspots</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#0f1720;color:#e8eef4}
#app{display:flex;height:100%}
#side{width:390px;flex:0 0 390px;display:flex;flex-direction:column;background:#111c26;border-right:1px solid #22303c;z-index:1000;min-height:0}
#side header{padding:18px 18px 10px}
h1{margin:0;font-size:1.4rem;letter-spacing:.2px}
.sub{margin:6px 0 0;color:#8fa3b5;font-size:.82rem;line-height:1.35}
.note{margin:10px 0 0;padding:8px 10px;border-radius:8px;background:#1a2a36;color:#a9bccb;font-size:.76rem;line-height:1.35}
#legend{display:flex;gap:12px;flex-wrap:wrap;padding:0 18px 10px;font-size:.75rem;color:#a9bccb}
#legend span{display:inline-flex;align-items:center;gap:5px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
#list{list-style:none;margin:0;padding:0 8px 10px;overflow:auto;flex:1;min-height:0}
#list li{display:flex;gap:10px;align-items:center;padding:8px 10px;border-radius:10px;cursor:pointer;transition:background .12s}
#list li:hover{background:#1b2a36}
.badge{min-width:40px;height:28px;border-radius:8px;display:grid;place-items:center;font-weight:700;font-size:.83rem;color:#fff;flex:0 0 auto}
.item{min-width:0}
.item strong{display:block;font-size:.86rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item small{display:block;font-size:.72rem;color:#8fa3b5;margin-top:2px}
footer{padding:10px 18px;border-top:1px solid #22303c;color:#73879a;font-size:.7rem;line-height:1.4}
footer a{color:#8fb8d6}
#map{flex:1;height:100%;background:#0b1219}
.leaflet-popup-content-wrapper{background:#16232e;color:#e8eef4;border-radius:10px}
.leaflet-popup-tip{background:#16232e}
.leaflet-popup-content{margin:12px 14px;font-size:.82rem;line-height:1.45}
.pop strong{font-size:.92rem}
.pop .lim{display:inline-block;background:#e0312f;color:#fff;border-radius:5px;padding:1px 6px;font-weight:700;margin:3px 0}
.pop small{color:#9fb2c3}
.pop a{color:#8fb8d6}
@media(max-width:760px){
  #app{flex-direction:column}
  #side{width:100%;flex:0 0 auto;max-height:46vh;order:2;border-right:0;border-top:1px solid #22303c}
  #map{order:1;flex:1 1 auto;min-height:54vh}
  #list{max-height:26vh}
}
</style>
</head>
<body>
<div id="app">
  <aside id="side">
    <header>
      <h1>__CITY__</h1>
      <p class="sub">Mobile Blitzer-Hotspots &middot; __COUNT__ Stellen<br>Messtage der letzten 12 Monate (bis 12.11.2026)</p>
      <p class="note">Historische Messstellen &ndash; keine Live-Warnungen. Klick auf einen Eintrag zoomt zur Stelle.</p>
    </header>
    <div id="legend">
      <span><i class="dot" style="background:#d62828"></i>15+ Tage</span>
      <span><i class="dot" style="background:#f77f00"></i>10&ndash;14</span>
      <span><i class="dot" style="background:#fcbf49"></i>5&ndash;9</span>
      <span><i class="dot" style="background:#457b9d"></i>&lt;5</span>
    </div>
    <ol id="list"></ol>
    <footer>Daten: <a href="__LISTURL__" target="_blank" rel="noopener">blitzer.de</a> &middot; Karte: OpenStreetMap</footer>
  </aside>
  <main id="map"></main>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const DATA = __DATA__;
const color = d => d.days >= 15 ? '#d62828' : d.days >= 10 ? '#f77f00' : d.days >= 5 ? '#fcbf49' : '#457b9d';
const map = L.map('map', { zoomControl: true }).setView([__LAT__, __LNG__], 13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
}).addTo(map);

const markers = DATA.map(d => {
  const m = L.circleMarker([d.lat, d.lng], {
    radius: Math.max(7, Math.min(17, 5 + d.days * 0.5)),
    color: '#ffffff', weight: 1.2, opacity: .9,
    fillColor: color(d), fillOpacity: .9
  }).addTo(map);
  m.bindPopup(`<div class="pop"><strong>${d.street}</strong><br>
    <span class="lim">${d.limit} km/h</span> &middot; ${d.days} Messtage<br>
    <small>zuletzt: ${d.last}</small><br>
    <small>${d.address || ''}${d.area ? ' &middot; ' + d.area : ''}</small><br>
    <a href="${d.url}" target="_blank" rel="noopener">Details auf blitzer.de</a></div>`);
  return m;
});

const list = document.getElementById('list');
DATA.forEach((d, i) => {
  const li = document.createElement('li');
  li.innerHTML = `<span class="badge" style="background:${color(d)}">${d.limit}</span>
    <span class="item"><strong>${d.street}</strong>
    <small>${d.days} Messtage &middot; zuletzt ${d.last}</small></span>`;
  li.onclick = () => {
    map.flyTo([d.lat, d.lng], 16, { duration: .7 });
    markers[i].openPopup();
  };
  list.appendChild(li);
});

if (DATA.length) {
  const bounds = L.latLngBounds(DATA.map(d => [d.lat, d.lng]));
  map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });
}
</script>
</body>
</html>
'@

$html = $template.
  Replace('__CITY__', [System.Net.WebUtility]::HtmlEncode($City)).
  Replace('__COUNT__', [string]$records.Count).
  Replace('__DATA__', $json).
  Replace('__LAT__', $avgLat.ToString('0.######', [System.Globalization.CultureInfo]::InvariantCulture)).
  Replace('__LNG__', $avgLng.ToString('0.######', [System.Globalization.CultureInfo]::InvariantCulture)).
  Replace('__LISTURL__', $listUrl)

[System.IO.File]::WriteAllText((Join-Path $OutDir 'siegen-hotspots.html'), $html, (New-Object System.Text.UTF8Encoding($false)))

Write-Host ''
Write-Host "Done. Files in $OutDir"
Get-ChildItem $OutDir | Select-Object Name, Length | Format-Table -AutoSize
