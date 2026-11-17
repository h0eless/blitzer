#!/usr/bin/env python3
"""
mobile_hotspots.py - rebuild the removed "Mobile Blitzer Hotspots" layer.

Blitzer.de dropped the aggregated mobile-hotspot layer from the app and from
the new web map. Two public sources behind it are still alive:

1. The live report feed:
       GET /karte/api/markers?type=<ids>&box=<swLat>,<swLng>,<neLat>,<neLng>&z=<zoom>
   Dense areas come back as cluster markers (type 1000), so the box gets
   subdivided until every report resolves.

2. The per-town pages, which never lost the aggregate:
       /ort/blitzer-in-<Ort>-<Bundesland>            (fixed cameras + top 12)
       /ort/mobile-messstellen-in-<Ort>-<Bundesland> (all measurement sites)
       /detail/mobile-messstelle-in-<Ort>,<Strasse>/<id>
       /detail/fester-blitzer-in-<Ort>,<Strasse>/<id>
   Each detail page carries the exact pin as an SVG circle in Web-Mercator
   pixels at z=16, plus the month-by-month measurement history. That block is
   the removed hotspot layer, geocoded, for free.

`sites` scrapes source 2, `once` samples source 1, `export` merges both into
GeoJSON / KML / GPX / a Leaflet page.

Every spot the scraper has ever seen stays in the database. `first_seen` is
written once, on insert, so a daily `sites` run turns the DB into a personal
archive and the `new` command answers "what appeared since yesterday?".

Usage:
    python mobile_hotspots.py sites
    python mobile_hotspots.py sites --live --auto-export   # daily: town pages + current feed
    python mobile_hotspots.py sites --orte "Siegen-Nordrhein-Westfalen,Kreuztal-Nordrhein-Westfalen"
    python mobile_hotspots.py once --bbox 50.75,7.80,51.05,8.20
    python mobile_hotspots.py watch --interval 900
    python mobile_hotspots.py new --days 30
    python mobile_hotspots.py export
    python mobile_hotspots.py stats
"""

import re

import argparse
import html
import json
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://www.blitzer.de/karte"
MARKERS = BASE + "/api/markers"
DB_PATH = Path(__file__).with_name("hotspots.sqlite3")
OUT_DIR = Path(__file__).with_name("hotspots_out")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Mobile report types. 1..19 = mobile speed traps (vmax in the payload),
# 2 = mobile red light, 6 = tailgating, 7 = semi-stationary trailer.
MOBILE_TYPES = "1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19"
FIXED_TYPES = "110,111,112,113,114"

# Siegen + Kreuztal + the ring of towns around them.
DEFAULT_BBOX = (50.75, 7.80, 51.05, 8.20)

MAX_ZOOM = 18
QUERY_ZOOM = 16
MIN_CELL_DEG = 0.0018          # ~200 m; below this stop subdividing
MAX_DEPTH = 6
REQUEST_DELAY = 0.12           # seconds between requests
# How close two reports must be to count as the same place. Kept tight on
# purpose: a camera 60 m further down the road is its own spot in the archive,
# while two reports 40 m apart are the same camera seen twice.
SAME_PLACE_M = 50.0
CLUSTER_RADIUS_M = SAME_PLACE_M   # feed sightings this close form one site
MIN_DAY_HITS = 1
LIVE_HOURS = 6.0               # a feed hit counts as "currently live" this long


# --------------------------------------------------------------------------- #
# http
# --------------------------------------------------------------------------- #

def _request(url, tries=4):
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": UA,
                "Referer": BASE + "/",
                "Accept-Language": "de-DE,de;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"request failed: {url} ({last})")


def fetch_markers(sw, ne, types, zoom=QUERY_ZOOM):
    q = urllib.parse.urlencode(
        {
            "type": types,
            "box": f"{sw[0]:.6f},{sw[1]:.6f},{ne[0]:.6f},{ne[1]:.6f}",
            "z": str(zoom),
        }
    )
    payload = _request(f"{MARKERS}?{q}")
    return payload.get("pois") or []


def sweep(sw, ne, types, depth=0):
    """Return a flat list of report dicts, subdividing wherever the server
    collapses several reports into one cluster marker."""
    if ne[0] - sw[0] <= 0 or ne[1] - sw[1] <= 0:
        return []

    out = []
    for poi in fetch_markers(sw, ne, types):
        if int(poi.get("type", 0)) == 1000:
            width = ne[0] - sw[0]
            height = ne[1] - sw[1]
            if depth >= MAX_DEPTH or min(width, height) <= MIN_CELL_DEG:
                out.append(poi)  # keep the aggregate, flagged by caller
                continue
            mid_lat = (sw[0] + ne[0]) / 2.0
            mid_lng = (sw[1] + ne[1]) / 2.0
            for a in (sw[0], mid_lat):
                for b in (sw[1], mid_lng):
                    out.extend(
                        sweep(
                            (a, b),
                            (a + (mid_lat - sw[0]), b + (mid_lng - sw[1])),
                            types,
                            depth + 1,
                        )
                    )
                    time.sleep(REQUEST_DELAY)
        else:
            out.append(poi)
    time.sleep(REQUEST_DELAY)
    return out


def sweep_bbox(bbox, types):
    sw = (bbox[0], bbox[1])
    ne = (bbox[2], bbox[3])
    return sweep(sw, ne, types)


# --------------------------------------------------------------------------- #
# source 2: /ort/ + /detail/ pages
# --------------------------------------------------------------------------- #

SITE_BASE = "https://www.blitzer.de"
DEFAULT_ORTE = [
    "Siegen-Nordrhein-Westfalen",
    "Kreuztal-Nordrhein-Westfalen",
    "Netphen-Nordrhein-Westfalen",
    "Hilchenbach-Nordrhein-Westfalen",
    "Wilnsdorf-Nordrhein-Westfalen",
    "Freudenberg-Nordrhein-Westfalen",
    "Neunkirchen-Nordrhein-Westfalen",
    "Burbach-Nordrhein-Westfalen",
]
MERCATOR_ZOOM = 16
MERGE_RADIUS_M = SAME_PLACE_M   # live report this close to an archived spot = same spot

_CARD_RE = re.compile(
    r'<li class="card(?:\s+card--spot)?">\s*<a href="([^"]+)">\s*'
    r'<span class="sign[^"]*"\s+title="([^"]*)"',
    re.S,
)
_TXT_RE = re.compile(
    r'<span class="card__txt"><strong>(.*?)</strong><small>(.*?)</small>',
    re.S,
)
_PIN_RE = re.compile(r'class="map__pin" cx="(\d+)" cy="(\d+)"')
_TILE_Z_RE = re.compile(r"/maps/[^/]+/256/(\d+)/\d+/\d+\.png")
_MESSTAGE_RE = re.compile(r"(\d+)\s*Messtage?")
_ZULETZT_RE = re.compile(r"zuletzt\s+([A-ZÄÖÜ][a-zäöüß]*\s+\d{4})")
_FACTS_RE = re.compile(
    r"<div>\s*<dt>(.*?)</dt>\s*<dd>(.*?)</dd>\s*</div>", re.S
)
_SECTION_RE = r'<section[^>]*\bid="{sid}".*?</section>'


def _vmax_from_title(title):
    """`title="50 km/h"` -> 50; unknown signs carry title="unbekannt"."""
    m = re.search(r"(\d+)\s*km/h", title or "")
    return int(m.group(1)) if m else None


def fetch_html(url, tries=3):
    for attempt in range(tries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "de-DE,de;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt + 1 < tries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt + 1 >= tries:
                raise
            time.sleep(1.5 * (attempt + 1))
    return None


def prect_to_latlng(cx, cy, zoom):
    """Web-Mercator world pixels -> WGS84. The detail pages draw the pin as an
    SVG circle in exactly this coordinate space."""
    world = 256.0 * (2 ** zoom)
    lng = cx / world * 360.0 - 180.0
    y = 1.0 - 2.0 * cy / world
    lat = math.degrees(math.atan(math.sinh(math.pi * y)))
    return lat, lng


def parse_spot_cards(page):
    """Cards on /ort/mobile-messstellen-... - href, vmax, name, meta line."""
    cards = []
    for m in _CARD_RE.finditer(page):
        href, vmax = m.group(1), _vmax_from_title(m.group(2))
        tail = page[m.end(): m.end() + 400]
        tm = _TXT_RE.search(tail)
        if not tm:
            continue
        name = re.sub(r"<[^>]+>", "", tm.group(1))
        meta = re.sub(r"<[^>]+>", "", tm.group(2))
        name = html.unescape(name).strip()
        meta = html.unescape(meta).strip()
        days_m = _MESSTAGE_RE.search(meta)
        last_m = _ZULETZT_RE.search(meta)
        parts = [p.strip() for p in meta.split("·")]
        cards.append(
            {
                # hrefs carry &#43; for '+'; unescape before it becomes a request
                "url": SITE_BASE + html.unescape(href),
                "street": name,
                "vmax": vmax,
                "days": int(days_m.group(1)) if days_m else None,
                "last_month": last_m.group(1) if last_m else (parts[1] if len(parts) > 1 else ""),
                "city": parts[-1] if len(parts) > 2 else "",
                "kind": "mobile",
            }
        )
    return cards


def _section(page, sid):
    """Inner HTML of one <section id="..."> block, or '' if it is absent."""
    m = re.search(_SECTION_RE.format(sid=sid), page or "", re.S)
    return m.group(0) if m else ""


def parse_fixed_cards(page):
    """Cards from the #feste block of /ort/blitzer-in-...

    Same markup as the mobile cards, but the meta line is
    "Geschwindigkeit · 57080 Siegen" instead of a Messtage count.
    """
    section = _section(page, "feste")
    if not section:
        return []
    cards = []
    for m in _CARD_RE.finditer(section):
        href, vmax = m.group(1), _vmax_from_title(m.group(2))
        tail = section[m.end(): m.end() + 400]
        tm = _TXT_RE.search(tail)
        if not tm:
            continue
        name = html.unescape(re.sub(r"<[^>]+>", "", tm.group(1))).strip()
        meta = html.unescape(re.sub(r"<[^>]+>", "", tm.group(2))).strip()
        parts = [p.strip() for p in meta.split("·") if p.strip()]
        art = parts[0] if parts else ""
        city = ""
        if parts:
            cm = re.search(r"\d{5}\s+(.+)$", parts[-1])
            city = (cm.group(1) if cm else parts[-1]).strip()
        cards.append(
            {
                "url": SITE_BASE + html.unescape(href),
                "street": name,
                "vmax": vmax,
                "days": None,
                "last_month": "",
                "city": city,
                "art": art,
                "kind": "fixed",
            }
        )
    return cards


def parse_facts(page):
    """The <dl class="facts"> key/value list, lowercased keys."""
    facts = {}
    for key, value in _FACTS_RE.findall(page or ""):
        k = html.unescape(re.sub(r"<[^>]+>", "", key)).strip().lower()
        v = html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()
        facts[k] = re.sub(r"\s+", " ", v)
    return facts


def parse_detail(page):
    """Detail page -> exact coordinates + monthly history.

    Works for both /mobile-messstelle/ and /fester-blitzer/ pages: the pin is
    the same SVG circle, the facts differ only in wording (Tempo/Tempolimit).
    """
    pin = _PIN_RE.search(page)
    if not pin:
        return None
    zm = _TILE_Z_RE.search(page)
    zoom = int(zm.group(1)) if zm else MERCATOR_ZOOM
    lat, lng = prect_to_latlng(int(pin.group(1)), int(pin.group(2)), zoom)

    facts = parse_facts(page)
    text = re.sub(r"<[^>]+>", " ", page)
    text = html.unescape(re.sub(r"\s+", " ", text))
    days_m = _MESSTAGE_RE.search(text)
    vmax_m = re.search(r"(?:Tempolimit|Tempo)\s+(\d+)\s*km/h", text)
    addr_m = re.search(r"Adresse\s+(.+?)\s+(?:Lage|Messtage|Richtung)", text)
    months = {k: int(v) for k, v in
              re.findall(r"([A-ZÄÖÜ][a-zäöüß]+\s+\d{4}):\s*(\d+)", text)}

    # "Weitere Messstellen in <Ort>" at the bottom of every detail page repeats
    # other spots' "N Messtage" lines, so the first match in the page belongs
    # to somebody else. The month chart is this spot's own data - trust it.
    last_month = ""
    days = None
    if months:
        days = sum(months.values())
        for month, count in months.items():
            if count:
                last_month = month
    if days is None and days_m:
        days = int(days_m.group(1))
    if not last_month:
        last_month = _ZULETZT_RE.search(text).group(1) if _ZULETZT_RE.search(text) else ""

    tempo = re.search(r"(\d+)", facts.get("tempo", "") or facts.get("tempolimit", ""))
    return {
        "lat": round(lat, 6),
        "lng": round(lng, 6),
        "days": days,
        "vmax": int(tempo.group(1)) if tempo else (int(vmax_m.group(1)) if vmax_m else None),
        "address": facts.get("adresse") or (addr_m.group(1).strip() if addr_m else ""),
        "last_month": last_month,
        "months": months,
        "art": facts.get("art", ""),
        "direction": facts.get("richtung", ""),
        "hint": facts.get("hinweis", ""),
    }


def scrape_ort(ort_slug, sleep=REQUEST_DELAY, with_fixed=True):
    """Every camera the two town pages know about, coordinates included.

    /ort/mobile-messstellen-...  -> all mobile spots
    /ort/blitzer-in-...          -> #feste fixed cameras + a top-12 mobile block

    Both /ort/blitzer-in- pages are always read, so the archive keeps working
    even if the dedicated mobile page is retired. The same spot appears on both
    pages with the same URL; the detail page is fetched only once.
    """
    out = []

    mobile_page = fetch_html(f"{SITE_BASE}/ort/mobile-messstellen-in-{ort_slug}")
    alt_page = fetch_html(f"{SITE_BASE}/ort/blitzer-in-{ort_slug}")
    if mobile_page is None and alt_page is None:
        print(f"  {ort_slug}: not found")
        return []

    cards = []
    if mobile_page:
        cards.extend(parse_spot_cards(mobile_page))
    if alt_page:
        cards.extend(parse_spot_cards(_section(alt_page, "messstellen")))

    unique, seen = [], set()
    for card in cards:
        if card["url"] in seen:
            continue
        seen.add(card["url"])
        unique.append(card)
    cards = unique

    for card in cards:
        time.sleep(sleep)
        detail = fetch_html(card["url"])
        if detail is None:
            continue
        info = parse_detail(detail)
        if not info:
            continue
        merged = {**card, **{k: v for k, v in info.items() if v not in (None, "", {})}}
        merged["vmax"] = merged.get("vmax") or info.get("vmax")
        merged["note"] = " · ".join(
            x for x in (info.get("direction"), info.get("hint")) if x
        )
        # The list card is this spot's own summary line; the detail chart can be
        # missing, and its first Messtage match may belong to a nearby spot.
        if card.get("days") is not None:
            merged["days"] = card["days"]
        if card.get("last_month"):
            merged["last_month"] = card["last_month"]
        merged["ort"] = ort_slug
        merged["kind"] = "mobile"
        out.append(merged)

    n_mobile = len(out)
    fixed_cards = parse_fixed_cards(alt_page) if with_fixed else []
    for card in fixed_cards:
        time.sleep(sleep)
        detail = fetch_html(card["url"])
        if detail is None:
            continue
        info = parse_detail(detail)
        if not info:
            continue
        merged = {**card, **{k: v for k, v in info.items() if v not in (None, "", {})}}
        merged["vmax"] = merged.get("vmax") or info.get("vmax")
        merged["note"] = " · ".join(
            x for x in (info.get("direction"), info.get("hint")) if x
        )
        merged["ort"] = ort_slug
        merged["kind"] = "fixed"
        merged["days"] = None
        out.append(merged)

    print(
        f"  {ort_slug}: {n_mobile} Messstelle(n) + "
        f"{len(out) - n_mobile} feste(r) Blitzer"
    )
    return out


def scrape_orte(orte, sleep=REQUEST_DELAY, with_fixed=True):
    spots = []
    for slug in orte:
        spots.extend(scrape_ort(slug, sleep=sleep, with_fixed=with_fixed))
    return spots


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id         TEXT PRIMARY KEY,
    type       INTEGER NOT NULL,
    lat        REAL NOT NULL,
    lng        REAL NOT NULL,
    vmax       INTEGER,
    city       TEXT,
    street     TEXT,
    is_cluster INTEGER NOT NULL DEFAULT 0,
    hits       INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sighting_days (
    id  TEXT NOT NULL,
    day TEXT NOT NULL,
    PRIMARY KEY (id, day)
);
CREATE INDEX IF NOT EXISTS idx_sightings_type ON sightings(type);
CREATE TABLE IF NOT EXISTS webspots (
    url        TEXT PRIMARY KEY,
    ort        TEXT,
    city       TEXT,
    street     TEXT,
    vmax       INTEGER,
    days       INTEGER,
    last_month TEXT,
    months     TEXT,
    lat        REAL NOT NULL,
    lng        REAL NOT NULL,
    scraped_at TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'mobile',
    art        TEXT,
    note       TEXT,
    first_seen TEXT NOT NULL DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after the first version of the table. Existing databases get
# them through ALTER TABLE; new ones already have them from SCHEMA above.
WEBSPOT_COLUMNS = {
    "kind": "TEXT NOT NULL DEFAULT 'mobile'",
    "art": "TEXT",
    "note": "TEXT",
    "first_seen": "TEXT NOT NULL DEFAULT ''",
    "active": "INTEGER NOT NULL DEFAULT 1",
}


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def meta_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(conn, key, value):
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def _migrate(conn):
    """Bring an older database up to the archive schema.

    first_seen can only be backfilled with the last scrape timestamp - that is
    the honest answer for rows that predate the column. `seeded_at` is then set
    to the newest of those, so the existing inventory is "Bestand" and only
    spots discovered later count as new.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(webspots)")}
    for name, ddl in WEBSPOT_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE webspots ADD COLUMN {name} {ddl}")
    conn.execute(
        "UPDATE webspots SET first_seen = scraped_at "
        "WHERE first_seen IS NULL OR first_seen = ''"
    )
    conn.execute("UPDATE webspots SET kind = 'mobile' WHERE kind IS NULL OR kind = ''")
    conn.commit()

    if meta_get(conn, "seeded_at") is None:
        newest = conn.execute("SELECT MAX(scraped_at) FROM webspots").fetchone()[0]
        if newest:
            meta_set(conn, "seeded_at", newest)


IDENTITY_M = 15.0     # same physical camera = same pin, within a few metres


def _find_place(conn, kind, lat, lng, radius_m=IDENTITY_M):
    """The archived row for this physical spot, whatever its URL is now.

    blitzer.de builds detail URLs from the street name plus an id. If the id is
    ever regenerated, the place is still the same place: same kind, same pin
    within a few metres. Returns the url of the closest such row, or None.
    """
    pad_lat = radius_m / 111000.0
    pad_lng = radius_m / (111000.0 * max(0.2, math.cos(math.radians(lat))))
    rows = conn.execute(
        "SELECT url, lat, lng FROM webspots WHERE kind=? "
        "AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
        (kind, lat - pad_lat, lat + pad_lat, lng - pad_lng, lng + pad_lng),
    ).fetchall()
    best, best_d = None, radius_m
    for url, rlat, rlng in rows:
        d = haversine_m(lat, lng, rlat, rlng)
        if d <= best_d:
            best, best_d = url, d
    return best


def store_webspots(conn, spots):
    """Upsert one scrape. first_seen is insert-only, so a spot keeps the day the
    archive learned about it even when blitzer.de keeps reporting it.

    A URL not seen before is first matched by pin: if a row for the same place
    already exists, that row is re-keyed to the new URL instead of creating a
    duplicate. Returns (stored, rekeyed).
    """
    now_iso = _now_iso()
    rekeyed = 0
    for s in spots:
        url = s["url"]
        kind = s.get("kind") or "mobile"
        exists = conn.execute(
            "SELECT 1 FROM webspots WHERE url=?", (url,)
        ).fetchone()
        if not exists:
            same = _find_place(conn, kind, s["lat"], s["lng"])
            if same and same != url:
                conn.execute("UPDATE webspots SET url=? WHERE url=?", (url, same))
                rekeyed += 1
        conn.execute(
            """
            INSERT INTO webspots (url, ort, city, street, vmax, days, last_month,
                                  months, lat, lng, scraped_at, kind, art, note,
                                  first_seen, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(url) DO UPDATE SET
                ort=excluded.ort, city=excluded.city, street=excluded.street,
                vmax=excluded.vmax, days=excluded.days,
                last_month=excluded.last_month, months=excluded.months,
                lat=excluded.lat, lng=excluded.lng, scraped_at=excluded.scraped_at,
                kind=excluded.kind, art=excluded.art, note=excluded.note, active=1
            """,
            (
                url,
                s.get("ort"),
                s.get("city"),
                s.get("street"),
                s.get("vmax"),
                s.get("days"),
                s.get("last_month"),
                json.dumps(s.get("months") or {}, ensure_ascii=False),
                s["lat"],
                s["lng"],
                now_iso,
                kind,
                s.get("art"),
                s.get("note"),
                now_iso,
            ),
        )
    conn.commit()
    return len(spots), rekeyed


def mark_missing(conn, spots):
    """Flag archive entries that stopped being listed, per (Ort, kind).

    Guarded: a half-broken scrape would otherwise wipe a whole town. Only run
    when the fresh result covers at least half of what was active before.
    Nothing is ever deleted - active=0 is the archive.
    """
    seen = {}
    for s in spots:
        seen.setdefault((s.get("ort"), s.get("kind") or "mobile"), set()).add(s["url"])

    gone = 0
    for (ort, kind), urls in seen.items():
        if not urls:
            continue
        before = conn.execute(
            "SELECT COUNT(*) FROM webspots WHERE ort=? AND kind=? AND active=1",
            (ort, kind),
        ).fetchone()[0]
        if before and len(urls) < before * 0.5:
            print(
                f"  {ort} / {kind}: nur {len(urls)} von {before} Stellen gelesen "
                f"- Archiv bleibt unangetastet"
            )
            continue
        marks = ",".join("?" * len(urls))
        cur = conn.execute(
            f"UPDATE webspots SET active=0 WHERE ort=? AND kind=? AND active=1 "
            f"AND url NOT IN ({marks})",
            (ort, kind, *urls),
        )
        gone += cur.rowcount
    conn.commit()
    return gone


def open_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def store(conn, report, is_cluster, now_iso, today):
    addr = report.get("address") or {}
    if is_cluster:
        key = f"cluster:{report['lat']:.5f},{report['lng']:.5f}"
    else:
        key = str(report.get("id") or "").strip()
        if not key:
            key = f"anon:{report['lat']:.5f},{report['lng']:.5f}:{report.get('type')}"

    conn.execute(
        """
        INSERT INTO sightings (id, type, lat, lng, vmax, city, street,
                               is_cluster, hits, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            hits      = hits + 1,
            last_seen = excluded.last_seen,
            lat       = excluded.lat,
            lng       = excluded.lng,
            vmax      = COALESCE(excluded.vmax, sightings.vmax),
            city      = COALESCE(excluded.city, sightings.city),
            street    = COALESCE(excluded.street, sightings.street)
        """,
        (
            key,
            _int(report.get("type"), 0),
            float(report["lat"]),
            float(report["lng"]),
            _int(report.get("vmax")),
            addr.get("city") or None,
            addr.get("street") or None,
            1 if is_cluster else 0,
            now_iso,
            now_iso,
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO sighting_days (id, day) VALUES (?, ?)", (key, today)
    )


def run_once(conn, bbox, types, label=""):
    now = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%d %H:%M:%S")
    today = now.strftime("%Y-%m-%d")

    reports = sweep_bbox(bbox, types)
    fresh = 0
    for r in reports:
        try:
            store(conn, r, int(r.get("type", 0)) == 1000, now_iso, today)
            fresh += 1
        except (KeyError, TypeError, ValueError):
            continue
    conn.commit()

    stamp = now.strftime("%H:%M:%S")
    print(f"[{stamp}] {label}sweep: {fresh} report(s) stored")
    return fresh


# --------------------------------------------------------------------------- #
# clustering + export
# --------------------------------------------------------------------------- #

def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_sites(conn, radius_m=CLUSTER_RADIUS_M):
    rows = conn.execute(
        "SELECT id, type, lat, lng, vmax, city, street, is_cluster, hits, "
        "first_seen, last_seen FROM sightings"
    ).fetchall()
    if not rows:
        return merge_webspots(conn, [])

    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    cell = radius_m / 111000.0
    buckets = {}
    for idx, row in enumerate(rows):
        buckets.setdefault((int(row[2] / cell), int(row[3] / cell)), []).append(idx)

    for (by, bx), members in buckets.items():
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                other = buckets.get((by + dy, bx + dx))
                if not other:
                    continue
                for i in members:
                    for j in other:
                        if i >= j:
                            continue
                        if haversine_m(rows[i][2], rows[i][3], rows[j][2], rows[j][3]) <= radius_m:
                            union(i, j)

    groups = {}
    for idx in range(len(rows)):
        groups.setdefault(find(idx), []).append(idx)

    days_by_id = {}
    for sid, day in conn.execute("SELECT id, day FROM sighting_days"):
        days_by_id.setdefault(sid, set()).add(day)

    sites = []
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=LIVE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    for members in groups.values():
        ids = [rows[i][0] for i in members]
        days = set()
        for sid in ids:
            days |= days_by_id.get(sid, set())
        report_days = len(days) or 1
        total_hits = sum(rows[i][8] for i in members)

        wsum = sum(rows[i][8] for i in members) or len(members)
        lat = sum(rows[i][2] * rows[i][8] for i in members) / wsum
        lng = sum(rows[i][3] * rows[i][8] for i in members) / wsum

        streets = sorted({rows[i][6] for i in members if rows[i][6]})
        cities = sorted({rows[i][5] for i in members if rows[i][5]})
        vmaxes = sorted({rows[i][4] for i in members if rows[i][4]})
        last_seen = max(rows[i][10] for i in members)
        # "live" means: this spot was in the feed during the most recent sweep.
        live_at = last_seen if last_seen >= cutoff else ""

        sites.append(
            {
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "reports": total_hits,
                "distinct_reports": len(members),
                "days": report_days,
                "street": streets[0] if streets else "",
                "streets": streets,
                "city": cities[0] if cities else "",
                "vmax": vmaxes,
                "first_seen": min(rows[i][9] for i in members),
                "last_seen": last_seen,
                "kind": "mobile",
                "active": True,
                "ort": "",
                "live": bool(live_at),
                "live_at": live_at,
            }
        )

    sites = [s for s in sites if s["days"] >= MIN_DAY_HITS]
    sites.sort(key=lambda s: (-s["days"], -s["reports"]))
    return merge_webspots(conn, sites)


def load_webspots(conn):
    rows = conn.execute(
        "SELECT url, ort, city, street, vmax, days, last_month, months, lat, lng, "
        "scraped_at, kind, art, note, first_seen, active FROM webspots"
    ).fetchall()
    out = []
    for url, ort, city, street, vmax, days, last_month, months, lat, lng, \
            scraped_at, kind, art, note, first_seen, active in rows:
        days = days or 0
        first = first_seen or scraped_at or ""
        out.append(
            {
                "lat": lat,
                "lng": lng,
                "reports": days,
                "distinct_reports": 0,
                "days": days,
                "street": street or "",
                "streets": [street] if street else [],
                "city": city or (ort or "").split("-")[0],
                "vmax": [vmax] if vmax else [],
                "first_seen": first,
                "last_seen": last_month or "",
                "months": json.loads(months) if months else {},
                "source": "ort",
                "url": url,
                "ort": ort or "",
                "kind": kind or "mobile",
                "art": art or "",
                "note": note or "",
                "active": bool(active),
                "live": False,
                "live_at": "",
            }
        )
    return out


def merge_webspots(conn, live_sites):
    """The /ort/ block is the authoritative aggregate; live feed hits within
    MERGE_RADIUS_M get folded into it, the rest stand alone as live-only spots.

    Fixed cameras are never merge targets: a mobile report 60 m away is a
    different installation, not more evidence for the box on the B62.
    """
    web = load_webspots(conn)
    if not web:
        for s in live_sites:
            s.setdefault("source", "live")
            s.setdefault("months", {})
            s.setdefault("kind", "mobile")
            s.setdefault("active", True)
            s.setdefault("ort", "")
        return live_sites

    mergeable = [w for w in web if w["kind"] != "fixed"]
    leftovers = []
    for live in live_sites:
        target = None
        best = MERGE_RADIUS_M
        for spot in mergeable:
            d = haversine_m(live["lat"], live["lng"], spot["lat"], spot["lng"])
            if d <= best:
                best = d
                target = spot
        if target is None:
            live.setdefault("source", "live")
            live.setdefault("months", {})
            live.setdefault("kind", "mobile")
            live.setdefault("active", True)
            live.setdefault("ort", "")
            live.setdefault("live", True)
            live.setdefault("live_at", live.get("last_seen") or "")
            leftovers.append(live)
            continue
        target["reports"] += live["reports"]
        target["distinct_reports"] += live["distinct_reports"]
        target["live"] = True
        # reported in the feed right now -> it is there, even if the town page
        # dropped it. The DB row keeps active=0; the payload shows it as live.
        target["active"] = True
        target["live_at"] = max(
            target.get("live_at") or "", live.get("live_at") or ""
        )
        target["days"] = max(target["days"], live["days"])

    merged = web + leftovers
    # live first, then the hottest spots - the map and list draw in this order
    merged.sort(key=lambda s: (not s.get("live"), -s["days"], -s["reports"]))
    return merged


def severity(site):
    """Rough heat: how often this spot has produced reports."""
    d = site["days"]
    if d >= 10:
        return 4
    if d >= 5:
        return 3
    if d >= 2:
        return 2
    return 1


def write_geojson(sites, path):
    features = []
    for s in sites:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]},
                "properties": {
                    "street": s["street"],
                    "city": s["city"],
                    "days": s["days"],
                    "reports": s["reports"],
                    "vmax": s["vmax"],
                    "severity": severity(s),
                    "kind": s.get("kind", "mobile"),
                    "active": 1 if s.get("active", True) else 0,
                    "source": s.get("source", "live"),
                    "live": 1 if s.get("live") else 0,
                    "live_at": s.get("live_at") or "",
                    "first_seen": s["first_seen"],
                    "last_seen": s["last_seen"],
                },
            }
        )
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )


def write_kml(sites, path):
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        "<name>Mobile Blitzer Hotspots</name>",
        '<Style id="h1"><IconStyle><color>ff00ff00</color><scale>0.8</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/caution.png</href></Icon></IconStyle></Style>',
        '<Style id="h2"><IconStyle><color>ff00aaff</color><scale>0.9</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/caution.png</href></Icon></IconStyle></Style>',
        '<Style id="h3"><IconStyle><color>ff0080ff</color><scale>1.0</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/caution.png</href></Icon></IconStyle></Style>',
        '<Style id="h4"><IconStyle><color>ff0000ff</color><scale>1.2</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/caution.png</href></Icon></IconStyle></Style>',
    ]
    for s in sites:
        title = html.escape((s["street"] or "unbekannt") + (f", {s['city']}" if s["city"] else ""))
        desc = html.escape(
            f"{s['days']} Tag(e) gemeldet, {s['reports']} Meldung(en)\n"
            f"Letzte: {s['last_seen']}\nTempolimit: {', '.join(map(str, s['vmax'])) or '?'}"
        )
        parts.append(
            f"<Placemark><name>{title}</name>"
            f"<description>{desc}</description>"
            f"<styleUrl>#h{severity(s)}</styleUrl>"
            f"<Point><coordinates>{s['lng']},{s['lat']},0</coordinates></Point></Placemark>"
        )
    parts.append("</Document></kml>")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_gpx(sites, path):
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="mobile_hotspots" xmlns="http://www.topografix.com/GPX/1/1">',
    ]
    for s in sites:
        name = html.escape((s["street"] or "Hotspot") + (f", {s['city']}" if s["city"] else ""))
        parts.append(
            f'<wpt lat="{s["lat"]}" lon="{s["lng"]}"><name>{name}</name>'
            f'<desc>{s["days"]} Tag(e), {s["reports"]} Meldung(en)</desc></wpt>'
        )
    parts.append("</gpx>")
    path.write_text("\n".join(parts), encoding="utf-8")


MAP_HTML = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mobile Blitzer Hotspots</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
html,body{margin:0;height:100%;background:#1b1e24;color:#e8eaee;
  font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
#map{position:absolute;inset:0}
#panel{position:absolute;z-index:500;top:12px;left:12px;max-width:300px;
  background:rgba(27,30,36,.94);border:1px solid #3a404c;border-radius:10px;padding:12px 14px}
#panel h1{margin:0 0 6px;font-size:15px}
#panel p{margin:0;color:#9aa3b2;font-size:12px}
.legend{display:flex;gap:10px;margin-top:8px;font-size:11px;color:#9aa3b2}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:middle}
.leaflet-popup-content b{font-size:13px}
</style></head><body>
<div id="map"></div>
<div id="panel"><h1>Mobile Blitzer Hotspots</h1>
<p>__COUNT__ Stellen aus __REPORTS__ Meldungen, __DAYS__ Tag(e) Beobachtung.</p>
<div class="legend">
 <span><i class="dot" style="background:#8bd450"></i>1 Tag</span>
 <span><i class="dot" style="background:#f2c14e"></i>2-4</span>
 <span><i class="dot" style="background:#f08a3c"></i>5-9</span>
 <span><i class="dot" style="background:#e5484d"></i>10+</span>
 <span><i class="dot" style="background:#4f9cff"></i>fest</span>
 <span><i class="dot" style="background:#ff3b6b"></i>live</span>
</div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const sites = __DATA__;
const colours = {1:'#8bd450',2:'#f2c14e',3:'#f08a3c',4:'#e5484d'};
const map = L.map('map',{zoomControl:true}).setView([50.89,8.01], 11);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:19, attribution:'&copy; OpenStreetMap'}).addTo(map);
for (const s of sites) {
  const fixed = s.kind === 'fixed';
  const live = !!s.live;
  const c = live ? '#ff3b6b' : fixed ? '#4f9cff' : (colours[s.severity] || '#8bd450');
  L.circleMarker([s.lat, s.lng], {
    radius: live ? 9 : fixed ? 6 : 5 + Math.min(10, s.days), color: c,
    weight: live ? 3 : 2, fillColor: c, fillOpacity: fixed ? .55 : live ? .4 : .35
  }).bindPopup(
    '<b>' + (s.street || 'unbekannt') + '</b>' + (s.city ? ', ' + s.city : '') +
    (live ? '<br><b style="color:#ff3b6b">LIVE</b> gemeldet ' + (s.live_at || '') : '') +
    (fixed ? '<br>Fester Blitzer' : '<br>' + s.days + ' Tag(e) gemeldet &middot; ' + s.reports + ' Meldung(en)') +
    (s.last_seen ? '<br>Letzte: ' + s.last_seen : '') +
    (s.vmax && s.vmax.length ? '<br>Limit: ' + s.vmax.join(', ') + ' km/h' : '')
  ).addTo(map);
}
if (sites.length) {
  map.fitBounds(sites.map(s => [s.lat, s.lng]), {padding:[40,40], maxZoom:14});
}
</script></body></html>
"""


def report_new(conn, days=30):
    """List spots the archive learned about recently.

    Uses the same merged view the app shows, so the daily log and the "Neu" tab
    agree: town-page spots plus live-only spots that never hit a town page.
    `seeded_at` is the moment the archive was created. Anything first seen
    after it is genuinely new; rows from the initial import are Bestand.
    """
    seeded = meta_get(conn, "seeded_at") or ""
    sites = build_sites(conn)
    if days and days > 0:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        sites = [s for s in sites if s["first_seen"] >= cutoff_iso]

    fresh = [s for s in sites if not seeded or s["first_seen"] > seeded]
    fresh.sort(key=lambda s: s["first_seen"], reverse=True)
    base = len(sites) - len(fresh)
    print(f"Archiv seit {seeded[:16] or '?'}  |  Fenster: {days or 'alles'} Tag(e)")
    print(f"{len(sites)} Stelle(n) im Fenster, davon {len(fresh)} neu, {base} Bestand\n")
    for s in fresh[:200]:
        tag = "FEST" if s.get("kind") == "fixed" else f"{s['days'] or 0:>2}d"
        state = ""
        if not s.get("active", True):
            state = "  [nicht mehr gelistet]"
        elif s.get("live"):
            state = "  [live]"
        label = ", ".join(filter(None, [s["street"], s["city"]])) or \
            f"{s['lat']:.4f},{s['lng']:.4f}"
        extra = s.get("art") or (f"{s['vmax'][0]} km/h" if s.get("vmax") else "")
        print(f"  NEU {s['first_seen'][:10]}  {tag:<6} {label:<44} {extra}{state}")
    if len(fresh) > 200:
        print(f"  … {len(fresh) - 200} weitere")
    return len(fresh)


def export(conn, out_dir=OUT_DIR):
    sites = build_sites(conn)
    if not sites:
        print("no data yet - run `once` first")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    write_geojson(sites, out_dir / "hotspots.geojson")
    write_kml(sites, out_dir / "hotspots.kml")
    write_gpx(sites, out_dir / "hotspots.gpx")

    days = {d for (d,) in conn.execute("SELECT DISTINCT day FROM sighting_days")}
    reports = conn.execute("SELECT COALESCE(SUM(hits),0) FROM sightings").fetchone()[0]
    page = (
        MAP_HTML.replace("__DATA__", json.dumps(sites, ensure_ascii=False))
        .replace("__COUNT__", str(len(sites)))
        .replace("__REPORTS__", str(reports))
        .replace("__DAYS__", str(len(days)))
    )
    (out_dir / "map.html").write_text(page, encoding="utf-8")

    print(f"{len(sites)} hotspot(s) aus {reports} Meldung(en), {len(days)} Tag(e)")
    live = sum(1 for s in sites if s.get("live"))
    if live:
        print(f"  {live} davon live gemeldet (letzte {LIVE_HOURS:.0f} h)")
    for s in sites[:15]:
        label = ", ".join(filter(None, [s["street"], s["city"]])) or f"{s['lat']:.4f},{s['lng']:.4f}"
        print(f"  {label:<46} {s['days']:>3}d  {s['reports']:>4}x  {s['last_seen']}")
    print(f"\nwrote {out_dir}")
    return len(sites)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #

def parse_bbox(text):
    parts = [float(p) for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox needs swlat,swlng,nelat,nelng")
    return tuple(parts)


def main(argv=None):
    # Windows consoles default to cp1252 and drop German street names on print.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="rebuild the Blitzer.de mobile hotspot layer")
    ap.add_argument(
        "command", choices=["sites", "once", "watch", "new", "export", "stats"]
    )
    ap.add_argument("--bbox", type=parse_bbox, default=DEFAULT_BBOX)
    ap.add_argument("--types", default=MOBILE_TYPES)
    ap.add_argument(
        "--orte",
        default=",".join(DEFAULT_ORTE),
        help="comma separated <Ort>-<Bundesland> slugs for the sites command",
    )
    ap.add_argument("--interval", type=int, default=900, help="seconds between sweeps in watch mode")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--days", type=int, default=30, help="window for the new command, 0 = everything")
    ap.add_argument("--auto-export", action="store_true", help="export after every sweep")
    ap.add_argument(
        "--live", action="store_true",
        help="sites: also sample the live feed, so current traps are marked live",
    )
    ap.add_argument("--with-fixed", action="store_true", help="also collect stationary cameras")
    ap.add_argument("--no-fixed", action="store_true", help="skip the stationary camera pages")
    ap.add_argument(
        "--reseed",
        action="store_true",
        help="treat everything currently in the db as Bestand (neues Archiv ab jetzt)",
    )
    args = ap.parse_args(argv)

    conn = open_db(args.db)

    if args.command == "sites":
        orte = [o.strip() for o in args.orte.split(",") if o.strip()]
        print(f"scraping {len(orte)} Ort(e)")
        spots = scrape_orte(orte, with_fixed=not args.no_fixed)
        if args.live:
            run_once(conn, args.bbox, args.types, label="live ")
        stored, rekeyed = store_webspots(conn, spots)
        gone = mark_missing(conn, spots)
        # First run ever: everything just imported is the baseline, not "new".
        if meta_get(conn, "seeded_at") is None or args.reseed:
            meta_set(conn, "seeded_at", _now_iso())
        extra = f", {rekeyed} URL(s) neu zugeordnet" if rekeyed else ""
        print(f"{stored} Stelle(n) in db, {gone} neu archiviert{extra}")
        if args.auto_export:
            export(conn, args.out)

    elif args.command == "new":
        report_new(conn, args.days)

    elif args.command == "once":
        run_once(conn, args.bbox, args.types)
        if args.with_fixed:
            run_once(conn, args.bbox, FIXED_TYPES, label="fixed ")
        if args.auto_export:
            export(conn, args.out)

    elif args.command == "watch":
        print(f"watching {args.bbox} every {args.interval}s - ctrl-c to stop")
        while True:
            try:
                run_once(conn, args.bbox, args.types)
                if args.with_fixed:
                    run_once(conn, args.bbox, FIXED_TYPES, label="fixed ")
                if args.auto_export:
                    export(conn, args.out)
            except KeyboardInterrupt:
                print("\nstopped")
                break
            except Exception as exc:  # keep the watch alive across network hiccups
                print(f"sweep failed: {exc}")
            time.sleep(args.interval)

    elif args.command == "export":
        export(conn, args.out)

    elif args.command == "stats":
        total = conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0]
        web = conn.execute("SELECT COUNT(*) FROM webspots").fetchone()[0]
        fixed = conn.execute(
            "SELECT COUNT(*) FROM webspots WHERE kind='fixed'"
        ).fetchone()[0]
        archived = conn.execute(
            "SELECT COUNT(*) FROM webspots WHERE active=0"
        ).fetchone()[0]
        seeded = meta_get(conn, "seeded_at") or "-"
        merged = build_sites(conn)
        fresh = sum(1 for s in merged if seeded != "-" and s["first_seen"] > seeded)
        days = conn.execute("SELECT COUNT(DISTINCT day) FROM sighting_days").fetchone()[0]
        hits = conn.execute("SELECT COALESCE(SUM(hits),0) FROM sightings").fetchone()[0]
        span = conn.execute("SELECT MIN(first_seen), MAX(last_seen) FROM sightings").fetchone()
        print(f"reports in db : {total}")
        print(f"web spots     : {web}  ({fixed} fest, {web - fixed} mobil)")
        print(f"merged view   : {len(merged)}  ({len(merged) - web} nur aus dem Live-Feed)")
        live_n = sum(1 for s in merged if s.get("live"))
        print(f"live now      : {live_n}  (letzte {LIVE_HOURS:.0f} h)")
        print(f"new since     : {fresh}  (Archiv seit {seeded[:16] or '-'})")
        print(f"not listed    : {archived}")
        print(f"total hits    : {hits}")
        print(f"days observed : {days}")
        print(f"first / last  : {span[0]} / {span[1]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
