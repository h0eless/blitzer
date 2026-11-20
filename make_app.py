#!/usr/bin/env python3
"""
make_app.py - build the installable hotspot app from hotspots.sqlite3.

Reads the same DB the scraper fills, bakes the sites into a single HTML file
with an embedded JSON payload, and writes blitzer_app.html next to it.

The output is a self-contained iOS-style web app: map, distance-sorted list,
a "Neu" tab fed by the archive (what the daily scrape discovered, plus every
spot ever seen), search, heat filtering, a detail sheet with the 12-month
measurement chart, and Add-to-Home-Screen metadata. No server, no build step.

Usage:
    python make_app.py
    python make_app.py --db hotspots.sqlite3 --out blitzer_app.html
"""

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import mobile_hotspots as mh

TOKENS = (
    "__DATA__", "__COUNT__", "__HEADLINE__", "__UPDATED__", "__SEEDED__",
    "__SEEDED_DAY__", "__ORTE__", "__TOWNS__", "__TITLE__",
)

APP = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="__TITLE__">
<meta name="theme-color" content="#0b0d10">
<title>__TITLE__</title>
__LEAFLET_CSS__
<style>
:root{
  --bg:#0b0d10; --bg2:#12151a; --card:#171b21; --line:#232833;
  --txt:#eef1f5; --dim:#8d97a6; --dim2:#5d6775;
  --hot:#ff4d4f; --warm:#ff9f43; --cool:#ffd166; --cold:#3ad29f;
  --live:#ff3b6b;
  --accent:#3ad29f;
  --safe-t:env(safe-area-inset-top,0px);
  --safe-b:env(safe-area-inset-bottom,0px);
  --head:calc(var(--safe-t) + 104px);
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);
  font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,sans-serif;
  overscroll-behavior:none}
body{display:flex;flex-direction:column;min-height:100dvh}

/* ---------- header ---------- */
header{position:fixed;top:0;left:0;right:0;z-index:1200;
  padding:calc(var(--safe-t) + 10px) 16px 12px;
  background:linear-gradient(180deg,rgba(11,13,16,.96) 60%,rgba(11,13,16,.72));
  -webkit-backdrop-filter:blur(18px);backdrop-filter:blur(18px);
  border-bottom:1px solid rgba(255,255,255,.05)}
.hrow{display:flex;align-items:center;justify-content:space-between;gap:12px}
.brand{display:flex;flex-direction:column;gap:2px;min-width:0}
.brand h1{margin:0;font-size:19px;letter-spacing:-.02em;font-weight:650}
.brand small{color:var(--dim);font-size:12px;letter-spacing:.01em}
.geobtn{flex:0 0 auto;display:flex;align-items:center;gap:6px;
  border:1px solid var(--line);background:var(--bg2);color:var(--txt);
  padding:8px 12px;border-radius:999px;font-size:13px;font-weight:550}
.geobtn[data-on="1"]{border-color:rgba(58,210,159,.5);color:var(--accent)}
.geobtn svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:1.9}

/* town picker: one button instead of a bar full of pills */
.townbtn{display:flex;align-items:center;gap:8px;width:100%;margin-top:9px;
  border:1px solid var(--line);background:var(--bg2);color:var(--txt);
  padding:9px 12px;border-radius:12px;font-size:13.5px;font-weight:600;text-align:left;
  touch-action:manipulation}
.townbtn:active{background:#1b2027}
.townbtn svg{flex:0 0 auto;width:16px;height:16px;fill:none;stroke:currentColor;
  stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;opacity:.75}
.townbtn .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.townbtn .cnt{color:var(--dim2);font-size:11.5px;font-weight:800;
  font-variant-numeric:tabular-nums}
.townbtn.on{border-color:rgba(58,210,159,.45)}
.townbtn.on .nm{color:var(--accent)}

/* town sheet */
.sheet--town{display:flex;flex-direction:column;max-height:88dvh;overflow:hidden}
.sheet--town .sub{margin-bottom:12px}
.tshead{display:flex;align-items:center;justify-content:space-between;gap:12px}
.tsclose{flex:0 0 auto;width:30px;height:30px;border-radius:50%;border:1px solid var(--line);
  background:var(--card);color:var(--dim);font-size:15px;line-height:1;
  display:flex;align-items:center;justify-content:center;touch-action:manipulation}
.tsclose:active{background:#1b2027;color:var(--txt)}
.tslist{flex:1;min-height:0;overflow-y:auto;-webkit-overflow-scrolling:touch;
  overscroll-behavior:contain;padding-bottom:calc(var(--safe-b) + 10px)}
.tsrow{display:flex;align-items:center;gap:10px;width:100%;
  padding:12px 2px;border:0;border-bottom:1px solid var(--line);
  background:none;color:var(--txt);font:inherit;text-align:left;touch-action:manipulation}
.tsrow:active{background:rgba(255,255,255,.04)}
.tsrow .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tsrow .km{color:var(--dim2);font-size:12px;font-variant-numeric:tabular-nums}
.tsrow .n{color:var(--dim);font-size:12px;font-weight:700;min-width:28px;text-align:right;
  font-variant-numeric:tabular-nums}
.tsrow.on .nm,.tsrow.on .n{color:var(--accent)}
.tsempty{padding:22px 2px;color:var(--dim);font-size:13.5px}

/* ---------- views ---------- */
main{flex:1;position:relative;padding-top:var(--head)}
.view{display:none}
.view.on{display:block}

/* map */
#map{position:fixed;top:var(--head);left:0;right:0;bottom:calc(var(--safe-b) + 64px);
  background:radial-gradient(circle at 50% 40%,#151a21 0%,#0b0d10 70%)}
.leaflet-container{background:transparent;font:inherit}
.leaflet-control-attribution{background:rgba(11,13,16,.7)!important;color:var(--dim2)!important;
  font-size:10px!important;border-radius:6px}
.leaflet-control-attribution a{color:var(--dim)!important}
.pin{border-radius:50%;border:2px solid rgba(0,0,0,.55);
  box-shadow:0 0 0 3px rgba(0,0,0,.25),0 6px 14px rgba(0,0,0,.5)}
.pin span{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  font-size:10px;font-weight:800;color:#08120e}

/* floating buttons on the map */
.fab{position:fixed;right:14px;z-index:1100;width:46px;height:46px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;border:1px solid rgba(255,255,255,.14);
  background:rgba(23,27,33,.94);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
  color:#cfd6e0;box-shadow:0 6px 18px rgba(0,0,0,.45)}
.fab.on{background:var(--accent);border-color:var(--accent);color:#06231a}
.fab svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.9;
  stroke-linecap:round;stroke-linejoin:round}
#fabFollow{bottom:calc(var(--safe-b) + 128px)}
#fabZoom{bottom:calc(var(--safe-b) + 76px)}
.fab:active{transform:scale(.94)}

/* my position */
.me{position:relative}
.me__dot{width:16px;height:16px;border-radius:50%;background:#2f7cf6;
  border:3px solid #fff;box-shadow:0 0 0 2px rgba(47,124,246,.35),0 3px 10px rgba(0,0,0,.5)}
.me__pulse{position:absolute;inset:-6px;border-radius:50%;background:rgba(47,124,246,.28);
  animation:pulse 2s ease-out infinite}
@keyframes pulse{0%{transform:scale(.6);opacity:.9}100%{transform:scale(2.4);opacity:0}}

/* toast + warning */
.toast{position:fixed;left:50%;bottom:calc(var(--safe-b) + 96px);transform:translate(-50%,14px);
  z-index:2000;max-width:86vw;padding:11px 16px;border-radius:14px;font-size:13px;
  background:rgba(32,38,47,.97);border:1px solid var(--line);color:var(--txt);text-align:center;
  opacity:0;pointer-events:none;transition:all .24s}
.toast.on{opacity:1;transform:translate(-50%,0)}
.warn{position:fixed;top:var(--head);left:0;right:0;z-index:1150;
  padding:9px 16px;font-size:12.5px;line-height:1.35;
  background:rgba(255,159,67,.14);border-bottom:1px solid rgba(255,159,67,.3);
  color:#ffcb8a;display:none}
.warn.on{display:block}

/* list */
.listwrap{padding:6px 14px calc(var(--safe-b) + 84px)}
.chips{position:sticky;top:var(--head);z-index:900;display:flex;gap:8px;
  padding:10px 14px 12px;margin:0 -14px 6px;
  background:linear-gradient(180deg,var(--bg) 70%,rgba(11,13,16,.85));
  overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none}
.chips::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;border:1px solid var(--line);background:var(--bg2);color:var(--dim);
  padding:7px 13px;border-radius:999px;font-size:13px;font-weight:550;white-space:nowrap}
.chip.on{background:var(--txt);color:#0b0d10;border-color:var(--txt)}
.search{width:100%;margin:0 0 12px;padding:12px 14px;border-radius:14px;
  border:1px solid var(--line);background:var(--card);color:var(--txt);font-size:16px;outline:none}
.search::placeholder{color:var(--dim2)}
.card{position:relative;display:flex;gap:12px;align-items:center;
  padding:13px 14px;margin-bottom:9px;background:var(--card);border:1px solid var(--line);
  border-radius:16px;content-visibility:auto;contain-intrinsic-size:auto 70px}
.card:active{background:#1b2027}
.sign{flex:0 0 42px;width:42px;height:42px;border-radius:50%;display:flex;
  align-items:center;justify-content:center;font-weight:800;font-size:15px;
  border:2.5px solid;background:#0e1116}
.card__t{flex:1;min-width:0}
.card__t b{display:block;font-size:15.5px;font-weight:600;letter-spacing:-.01em;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.card__t small{display:block;color:var(--dim);font-size:12.5px;margin-top:2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.heat{position:absolute;right:14px;bottom:12px;height:3px;width:56px;border-radius:2px;
  background:var(--line);overflow:hidden}
.heat i{display:block;height:100%;border-radius:2px}
.dist{flex:0 0 auto;color:var(--dim);font-size:12.5px;font-variant-numeric:tabular-nums;
  align-self:flex-start;margin-top:2px}
.empty{padding:40px 8px;text-align:center;color:var(--dim)}
.more{display:block;width:100%;margin:6px 0 10px;padding:13px;border-radius:14px;
  border:1px dashed var(--line);background:none;color:var(--dim);font:inherit;
  font-size:13.5px;font-weight:600;touch-action:manipulation}
.more:active{background:var(--card)}

/* fixed cameras: a blue square, deliberately not part of the heat scale */
.pin-fixed{width:26px;height:26px;border-radius:7px;display:flex;align-items:center;
  justify-content:center;background:#10283d;border:2.5px solid #4f9cff;color:#a9d4ff;
  font-size:11px;font-weight:800;box-shadow:0 0 0 2px rgba(0,0,0,.35),0 6px 14px rgba(0,0,0,.5)}
.sign.is-fixed{border-color:#4f9cff;color:#a9d4ff}

/* live mobile traps: pink ring, speed limit inside, sonar pulse while fresh.
   Deliberately different from the blue square of the fixed cameras. */
.pin-live{filter:drop-shadow(0 0 6px rgba(255,59,107,.55))}
.pin-pulse{animation:sonar 2.2s ease-out infinite}
@keyframes sonar{
  0%{stroke-opacity:.9;stroke-width:2}
  75%{stroke-opacity:0;stroke-width:16}
  100%{stroke-opacity:0;stroke-width:16}
}
.pinlabel-live{background:none;border:0;box-shadow:none;color:#fff;font-size:12px;
  font-weight:800;padding:0;text-shadow:0 1px 3px rgba(0,0,0,.9)}
.sign.is-live{border-color:var(--live);color:#ffd7e0;background:#2a0f18}
.tag.live{background:rgba(255,59,107,.18);color:#ff8aa6;border:1px solid rgba(255,59,107,.4)}

/* archive / new view */
.newhead{padding:4px 0 14px}
.newhead .tiles{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}
.newhead .tile{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:11px 12px}
.newhead .tile b{display:block;font-size:23px;font-weight:700;letter-spacing:-.03em;
  font-variant-numeric:tabular-nums}
.newhead .tile small{display:block;color:var(--dim2);font-size:10.5px;text-transform:uppercase;
  letter-spacing:.07em;margin-top:2px}
.newhead .note{margin-top:11px;color:var(--dim);font-size:12px;line-height:1.4}
.tag{flex:0 0 auto;align-self:flex-start;margin-top:2px;font-size:9.5px;font-weight:800;
  letter-spacing:.07em;padding:3px 7px;border-radius:6px;white-space:nowrap}
.tag.new{background:rgba(58,210,159,.16);color:var(--accent);border:1px solid rgba(58,210,159,.35)}
.tag.base{background:#1b2027;color:var(--dim2);border:1px solid var(--line)}
.tag.gone{background:rgba(255,159,67,.14);color:#ffcb8a;border:1px solid rgba(255,159,67,.3)}

/* info */
.info{padding:8px 18px calc(var(--safe-b) + 84px)}
.info h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim2);
  margin:22px 0 8px}
.info p{margin:0 0 12px;color:#c3cad4;font-size:14.5px}
.info .row{display:flex;align-items:baseline;gap:14px;padding:9px 0;border-bottom:1px solid var(--line)}
.info .row:last-child{border-bottom:0}
.info .row b{font-size:14px;font-weight:600;flex:0 0 auto}
.info .row span{color:var(--dim);font-size:13px;margin-left:auto;text-align:right;
  flex:0 1 auto;min-width:0;max-width:62%;overflow-wrap:anywhere}
.info code{background:#1e242d;padding:1px 5px;border-radius:5px;font-size:12.5px}
.info .row span a{color:var(--accent);text-decoration:none;font-weight:600}

/* tabs */
nav{position:fixed;left:0;right:0;bottom:0;z-index:1200;display:flex;
  padding:6px 10px calc(var(--safe-b) + 6px);
  background:rgba(13,16,20,.92);-webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);
  border-top:1px solid rgba(255,255,255,.06)}
nav button{flex:1;background:none;border:0;color:var(--dim2);font-size:11px;font-weight:600;
  display:flex;flex-direction:column;align-items:center;gap:3px;padding:5px 0;letter-spacing:.01em}
nav button.on{color:var(--txt)}
nav svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.8;
  stroke-linecap:round;stroke-linejoin:round}

/* sheet */
.backdrop{position:fixed;inset:0;z-index:1400;background:rgba(0,0,0,.55);
  opacity:0;pointer-events:none;transition:opacity .22s}
.backdrop.on{opacity:1;pointer-events:auto}
.sheet{position:fixed;left:0;right:0;bottom:0;z-index:1500;
  background:var(--bg2);border-radius:22px 22px 0 0;border-top:1px solid var(--line);
  padding:8px 18px calc(var(--safe-b) + 18px);transform:translateY(102%);
  transition:transform .28s cubic-bezier(.32,.72,0,1);max-height:86dvh;overflow:auto}
.sheet.on{transform:translateY(0)}
.grab{width:38px;height:4px;border-radius:2px;background:#3a4250;margin:6px auto 14px}
.sheet h3{margin:0 0 3px;font-size:20px;letter-spacing:-.02em}
.sheet .sub{color:var(--dim);font-size:13px;margin-bottom:16px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-bottom:16px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 12px}
.stat b{display:block;font-size:21px;font-weight:700;letter-spacing:-.02em}
.stat small{display:block;color:var(--dim2);font-size:11px;text-transform:uppercase;
  letter-spacing:.06em;margin-top:2px}
.bars{display:flex;align-items:flex-end;gap:4px;height:66px;margin:0 0 6px}
.bars div{flex:1;background:#2a3140;border-radius:3px 3px 0 0;position:relative;min-height:3px}
.bars div.hi{background:var(--accent)}
.barlabels{display:flex;gap:4px;color:var(--dim2);font-size:9px;text-align:center}
.barlabels span{flex:1;overflow:hidden}
.openlink{display:block;text-align:center;margin-top:16px;padding:13px;border-radius:14px;
  background:var(--card);border:1px solid var(--line);color:var(--accent);
  text-decoration:none;font-weight:600;font-size:14px}
</style>
</head>
<body>

<header>
  <div class="hrow">
    <div class="brand">
      <h1>__TITLE__</h1>
      <small id="headline">__HEADLINE__</small>
    </div>
    <button class="geobtn" id="geo">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>
      <span id="geotxt">Nähe</span>
    </button>
  </div>
  <button class="townbtn" id="townbtn" aria-haspopup="dialog">
    <svg viewBox="0 0 24 24"><path d="M12 21s7-5.5 7-11a7 7 0 1 0-14 0c0 5.5 7 11 7 11Z"/><circle cx="12" cy="10" r="2.6"/></svg>
    <span class="nm" id="townbtn-name">Alle Orte</span>
    <span class="cnt" id="townbtn-cnt"></span>
    <svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>
  </button>
</header>

<main>
  <div class="warn" id="warn"></div>
  <div class="view on" id="v-map">
    <div id="map"></div>
    <button class="fab" id="fabFollow" aria-label="Standort folgen">
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="2.6"/><circle cx="12" cy="12" r="7" opacity=".5"/><path d="M12 2.2v2.6M12 19.2v2.6M2.2 12h2.6M19.2 12h2.6"/></svg>
    </button>
    <button class="fab" id="fabZoom" aria-label="Auf mich zoomen">
      <svg viewBox="0 0 24 24"><path d="M12 3.2 19 20.4l-7-3.6-7 3.6 7-17.2Z"/></svg>
    </button>
  </div>

  <div class="view" id="v-list">
    <div class="listwrap">
      <div class="chips" id="chips">
        <button class="chip on" data-f="all">Alle</button>
        <button class="chip" data-f="live">Live jetzt</button>
        <button class="chip" data-f="hot">Heiß · 10+ Tage</button>
        <button class="chip" data-f="warm">5–9 Tage</button>
        <button class="chip" data-f="fixed">Feste Blitzer</button>
        <button class="chip" data-f="recent">Zuletzt gemessen</button>
        <button class="chip" data-f="near">In der Nähe</button>
      </div>
      <input class="search" id="q" placeholder="Straße oder Ort suchen" autocomplete="off">
      <div id="cards"></div>
    </div>
  </div>

  <div class="view" id="v-new">
    <div class="listwrap">
      <div class="newhead" id="newhead"></div>
      <div class="chips" id="chips-new">
        <button class="chip on" data-n="7">Neu · 7 Tage</button>
        <button class="chip" data-n="30">Neu · 30 Tage</button>
        <button class="chip" data-n="fixed">Feste Blitzer</button>
        <button class="chip" data-n="all">Meine Datenbank</button>
        <button class="chip" data-n="gone">Archiv</button>
      </div>
      <div id="newcards"></div>
    </div>
  </div>

  <div class="view" id="v-info">
    <div class="info">
      <h2>Was hier steht</h2>
      <p>Jeder Punkt ist eine Stelle, an der gemessen wird — mobil in den letzten zwölf Monaten, oder fest installiert. Die Zahl in den runden Markern ist die Anzahl der Messtage, nicht der Tempoverstöße. Pinke Ringe sind Stellen, die im letzten Live-Abgleich gemeldet wurden; dort steht das Tempolimit im Ring.</p>

      <h2>Die Farbe</h2>
      <div class="row"><b style="color:var(--hot)">Rot</b><span>10+ Messtage — Dauerbrenner</span></div>
      <div class="row"><b style="color:var(--warm)">Orange</b><span>5–9 Messtage — regelmäßig</span></div>
      <div class="row"><b style="color:var(--cool)">Gelb</b><span>2–4 Messtage — gelegentlich</span></div>
      <div class="row"><b style="color:var(--cold)">Grün</b><span>1 Messtag — Einzelfall</span></div>
      <div class="row"><b style="color:#4f9cff">Blau</b><span>feste Anlage — Quadrat statt Kreis</span></div>
      <div class="row"><b style="color:var(--live)">Pink</b><span>live gemeldet — Ring mit Tempolimit, pulsierend solange frisch</span></div>
      <div class="row"><b style="color:#6b7686">Grau</b><span>Archiv — dort stand früher eine Stelle</span></div>

      <h2>Neu &amp; Archiv</h2>
      <p>Der Rechner gleicht die Messstellen täglich mit deiner Datenbank ab. Was neu dazukommt, steht unter <b>Neu</b>. Nichts wird je gelöscht: verschwindet eine Stelle aus der Quelle, bleibt sie im <b>Archiv</b> und als grauer Punkt auf der Karte — als möglicher Standort.</p>

      <h2>Was es nicht ist</h2>
      <p>Pinke Marker zeigen, was beim letzten Abgleich im Live-Feed stand — mit Uhrzeit im Detail. Sie veralten mit dem Tag und ersetzen keine Warnung während der Fahrt. Für unterwegs bleibt die Blitzer.de-App die Quelle; diese Karte sagt dir, wo sie <em>üblicherweise</em> stehen.</p>

      <h2>Daten</h2>
      <div class="row"><b>Orte</b><span>__ORTE__</span></div>
      <div class="row"><b>Gewählter Ort</b><span id="town-link">Alle</span></div>
      <div class="row"><b>Update</b><span>__UPDATED__</span></div>
      <div class="row"><b>Archiv seit</b><span>__SEEDED_DAY__</span></div>
      <div class="row"><b>Quelle</b><span>blitzer.de /ort/ &amp; Live-Feed</span></div>

      <h2>Standort-Status</h2>
      <p>Wenn der Standort nicht kommt, steht hier warum. Safari ortet nur über <b>https</b>.</p>
      <div class="row"><b>Origin</b><span id="st-protocol">–</span></div>
      <div class="row"><b>Secure Context</b><span id="st-secure">–</span></div>
      <div class="row"><b>Berechtigung</b><span id="st-perm">–</span></div>
      <div class="row"><b>Letzte Position</b><span id="st-fix">–</span></div>
      <div class="row"><b>Quelle</b><span id="st-src">–</span></div>
      <div class="row"><b>Letzter Fehler</b><span id="st-err">–</span></div>

      <h2>Installieren</h2>
      <p>Teilen-Symbol unten in Safari → „Zum Home-Bildschirm“. Danach startet die Karte ohne Adresszeile, wie eine App.</p>
    </div>
  </div>
</main>

<nav>
  <button class="on" data-v="map">
    <svg viewBox="0 0 24 24"><path d="M9 4 3 6.5v14L9 18l6 2.5 6-2.5v-14L15 6.5 9 4Z"/><path d="M9 4v14M15 6.5v14"/></svg>
    Karte
  </button>
  <button data-v="list">
    <svg viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
    Liste
  </button>
  <button data-v="new">
    <svg viewBox="0 0 24 24"><path d="M12 3.5 14.6 9l6 .9-4.3 4.2 1 6-5.3-2.8-5.3 2.8 1-6L4.4 9.9l6-.9L12 3.5Z"/></svg>
    Neu
  </button>
  <button data-v="info">
    <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg>
    Info
  </button>
</nav>

<div class="backdrop" id="bd"></div>
<div class="sheet" id="sheet">
  <div class="grab"></div>
  <h3 id="sh-title">—</h3>
  <div class="sub" id="sh-sub"></div>
  <div class="sub" id="sh-note"></div>
  <div class="stats">
    <div class="stat"><b id="sh-days">—</b><small id="sh-l1">Messtage</small></div>
    <div class="stat"><b id="sh-vmax">—</b><small id="sh-l2">Limit</small></div>
    <div class="stat"><b id="sh-last">—</b><small id="sh-l3">zuletzt</small></div>
  </div>
  <div class="bars" id="sh-bars"></div>
  <div class="barlabels" id="sh-labels"></div>
  <a class="openlink" id="sh-link" href="#" target="_blank" rel="noopener">Details auf blitzer.de</a>
</div>

<div class="sheet sheet--town" id="townsheet" role="dialog" aria-label="Ort wählen">
  <div class="grab"></div>
  <div class="tshead">
    <h3>Ort</h3>
    <button class="tsclose" id="ts-close" aria-label="Schließen">✕</button>
  </div>
  <div class="sub" id="ts-sub">Filter für Karte, Liste und Archiv</div>
  <input class="search" id="ts-search" placeholder="Ort suchen" autocomplete="off"
         autocapitalize="off" autocorrect="off" enterkeyhint="search" spellcheck="false">
  <div class="tslist" id="ts-list"></div>
</div>

<div class="toast" id="toast"></div>

__LEAFLET_JS__
<script>
const SITES = __DATA__;
const TOWNS = __TOWNS__;
const SEEDED = "__SEEDED__";
const COLOURS = {4:'#ff4d4f',3:'#ff9f43',2:'#ffd166',1:'#3ad29f'};
const COLOUR_RGB = {4:[255,77,79],3:[255,159,67],2:[255,209,102],1:[58,210,159]};
const FIXED_COLOUR = '#4f9cff';
const LIVE_COLOUR = '#ff3b6b';

const savedTown = (() => { try { return localStorage.getItem('bt-town') || 'all'; } catch(e){ return 'all'; } })();
const state = { filter:'all', query:'', pos:null, sortByDistance:false,
                following:false, watchId:null, newMode:'7', town:savedTown,
                listLimit:80, newLimit:80, lastPos:null };

/* ---------- helpers ---------- */
const $ = s => document.querySelector(s);
function esc(s){
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function haversine(a,b){ // [lat,lng]
  const R=6371000, toRad=x=>x*Math.PI/180;
  const dLat=toRad(b[0]-a[0]), dLng=toRad(b[1]-a[1]);
  const s=Math.sin(dLat/2)**2 + Math.cos(toRad(a[0]))*Math.cos(toRad(b[0]))*Math.sin(dLng/2)**2;
  return 2*R*Math.asin(Math.sqrt(s));
}
function fmtDist(m){
  if(m==null) return '';
  if(m<950) return Math.round(m/10)*10 + ' m';
  return (m/1000).toFixed(m<10000?1:0) + ' km';
}
// heat is derived from the Messtage count, not from a stored field
function sev(s){
  const d = s.days || 0;
  return d >= 10 ? 4 : d >= 5 ? 3 : d >= 2 ? 2 : 1;
}
function shortMonth(s){
  const map = {Januar:'Jan',Februar:'Feb',März:'Mär',April:'Apr',Mai:'Mai',Juni:'Jun',
               Juli:'Jul',August:'Aug',September:'Sep',Oktober:'Okt',November:'Nov',Dezember:'Dez'};
  return (s||'').replace(/^([A-Za-zÄÖÜäöüß]+)/, m => map[m] || m);
}
/* ---------- archive helpers ---------- */
function isFixed(s){ return s.kind === 'fixed'; }
function isNew(s){ return !!s.first_seen && !!SEEDED && s.first_seen > SEEDED; }
/* live = reported in the last full feed sweep; the app ages it out itself */
const LIVE_MAX_H = 12;    // older than this: no longer shown as live
const LIVE_FRESH_H = 4;   // younger than this: pulsing ring
function hoursSince(ts){
  if(!ts) return 1e9;
  const t = Date.parse(ts.replace(' ', 'T') + 'Z');
  if(isNaN(t)) return 1e9;
  return (Date.now() - t) / 3600000;
}
function isLive(s){ return !!s.live && hoursSince(s.live_at) <= LIVE_MAX_H; }
function isLiveFresh(s){ return isLive(s) && hoursSince(s.live_at) <= LIVE_FRESH_H; }
function parseTs(ts){
  // the payload stores UTC "YYYY-MM-DD HH:MM:SS"; show it in the phone's zone
  if(!ts) return null;
  const t = Date.parse(String(ts).replace(' ', 'T') + 'Z');
  return isNaN(t) ? null : new Date(t);
}
function timeOf(ts){
  const d = parseTs(ts);
  if(d){
    const two = n => String(n).padStart(2, '0');
    return two(d.getHours()) + ':' + two(d.getMinutes());
  }
  const p = (ts || '—').split(' ');
  return p.length > 1 ? p[1].slice(0,5) : (ts || '—');
}
function liveAgo(ts){
  const h = hoursSince(ts);
  if(h >= 1e9) return '';
  if(h < 1) return 'gerade eben';
  if(h < 2) return 'vor 1 Std';
  return 'vor ' + Math.floor(h) + ' Std';
}
function ageDays(ts){
  if(!ts) return 99999;
  const t = Date.parse(ts.replace(' ', 'T') + 'Z');
  if(isNaN(t)) return 99999;
  return Math.floor((Date.now() - t) / 86400000);
}
function fmtDate(ts){
  const d = parseTs(ts);
  if(d){
    const two = n => String(n).padStart(2, '0');
    return two(d.getDate()) + '.' + two(d.getMonth() + 1) + '.' + d.getFullYear();
  }
  const p = (ts || '—').slice(0,10).split('-');
  return p.length === 3 ? `${p[2]}.${p[1]}.${p[0]}` : (ts || '—');
}
function countNew(days){
  return SITES.filter(s => s.active !== false && townMatch(s) &&
                           isNew(s) && ageDays(s.first_seen) <= days).length;
}
/* town picker: one filter for map, list and archive */
function siteTown(s){ return (s.city || '').trim() || 'Ohne Ort'; }
function townMatch(s){ return state.town === 'all' || siteTown(s) === state.town; }
function townOf(name){ return TOWNS.find(t => t.name === name) || null; }

function setTown(name){
  state.town = name || 'all';
  state.listLimit = 80; state.newLimit = 80;
  try { localStorage.setItem('bt-town', state.town); } catch(e){}
  renderTownButton();
  updateTownLink();
  drawMap(); renderList(); renderNew();
  fitToView();
}

function renderTownButton(){
  const btn = $('#townbtn');
  if(!btn) return;
  const active = SITES.filter(s => s.active !== false);
  const n = state.town === 'all' ? active.length
          : active.filter(s => siteTown(s) === state.town).length;
  $('#townbtn-name').textContent = state.town === 'all' ? 'Alle Orte' : state.town;
  $('#townbtn-cnt').textContent = n;
  btn.classList.toggle('on', state.town !== 'all');
}

function renderTownSheet(){
  const list = $('#ts-list');
  if(!list) return;
  const q = ($('#ts-search').value || '').toLowerCase().trim();
  const total = SITES.filter(s => s.active !== false).length;
  let rows = [{ name:'all', label:'Alle Orte', n:total }].concat(
    TOWNS.map(t => ({ name:t.name, label:t.name, n:t.n, lat:t.lat, lng:t.lng }))
  );
  if(q) rows = rows.filter(t => t.label.toLowerCase().includes(q));
  const known = state.pos;
  rows.sort((a,b) => {
    if(a.name === 'all') return -1;
    if(b.name === 'all') return 1;
    if(known && a.lat != null && b.lat != null){
      return haversine(known,[a.lat,a.lng]) - haversine(known,[b.lat,b.lng]);
    }
    return (b.n - a.n) || a.label.localeCompare(b.label);
  });
  if(!rows.length){ list.innerHTML = '<div class="tsempty">Kein Ort gefunden.</div>'; return; }
  list.innerHTML = rows.map(t => {
    const km = (known && t.lat != null)
      ? (haversine(known,[t.lat,t.lng]) / 1000).toFixed(haversine(known,[t.lat,t.lng]) < 10000 ? 1 : 0) + ' km'
      : '';
    return `<button class="tsrow${state.town === t.name ? ' on' : ''}" data-t="${esc(t.name)}">` +
      `<span class="nm">${esc(t.label)}</span>` +
      (km ? `<span class="km">${km}</span>` : '') +
      `<span class="n">${t.n}</span></button>`;
  }).join('');
  list.querySelectorAll('.tsrow').forEach(b => {
    b.addEventListener('click', () => { setTown(b.dataset.t); closeTownSheet(); });
  });
}

function openTownSheet(){
  $('#ts-search').value = '';
  renderTownSheet();
  $('#townsheet').classList.add('on');
  bd.classList.add('on');
}
function closeTownSheet(){
  $('#townsheet').classList.remove('on');
  bd.classList.remove('on');
}

function updateTownLink(){
  const el = $('#town-link');
  if(!el) return;
  if(state.town === 'all'){ el.textContent = 'Alle'; return; }
  const t = townOf(state.town);
  el.innerHTML = (t && t.url)
    ? `<a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.name)} ↗</a>`
    : esc(state.town);
}

/* the header grows with the town bar; keep the fixed map under it */
function syncHead(){
  const h = document.querySelector('header');
  if(!h) return;
  const px = Math.round(h.getBoundingClientRect().height);
  if(px > 40) document.documentElement.style.setProperty('--head', px + 'px');
}
let toastTimer = null;
function toast(msg, ms=2600){
  const el = $('#toast');
  el.textContent = msg; el.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('on'), ms);
}
function isRecent(s){
  if(!s.last_seen) return false;
  const now = new Date();
  const months = ['Januar','Februar','März','April','Mai','Juni','Juli','August','September','Oktober','November','Dezember'];
  const cur = months[now.getMonth()] + ' ' + now.getFullYear();
  const prev = months[(now.getMonth()+11)%12] + ' ' + (now.getMonth()===0?now.getFullYear()-1:now.getFullYear());
  return s.last_seen === cur || s.last_seen === prev;
}
function withDistance(){
  return SITES.map((s,i) => ({...s, idx:i, dist: state.pos ? haversine(state.pos, [s.lat, s.lng]) : null}));
}
function passes(s, includeGone){
  // archived spots stay on the map as faint grey memories, but not in the list
  if(s.active === false && !includeGone) return false;
  if(!townMatch(s)) return false;
  if(state.query){
    const q = state.query.toLowerCase();
    if(!(s.street||'').toLowerCase().includes(q) && !(s.city||'').toLowerCase().includes(q)) return false;
  }
  switch(state.filter){
    case 'live': return isLive(s);
    case 'hot': return !isFixed(s) && (s.days||0) >= 10;
    case 'warm': return !isFixed(s) && (s.days||0) >= 5 && (s.days||0) < 10;
    case 'recent': return !isFixed(s) && isRecent(s);
    case 'fixed': return isFixed(s);
    case 'near': return s.dist != null && s.dist < 3000;
    default: return true;
  }
}

/* ---------- map ---------- */
const map = L.map('map', {zoomControl:false, attributionControl:true}).setView([50.87,8.02], 11);

// Esri's dark canvas needs no API key and allows direct tile use.
// It only has real tiles up to zoom 16; above that the service answers with a
// "Map data not yet available" placeholder image (HTTP 200, so no tileerror).
// maxNativeZoom makes Leaflet enlarge the last real level instead, and the
// label layer simply stops at 16 so its text does not blow up.
// If the tiles fail altogether, OSM is the fallback so the map is never blank.
const dark = L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:18, maxNativeZoom:16, attribution:'&copy; Esri, HERE, Garmin, OpenStreetMap'}
).addTo(map);
L.tileLayer(
  'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
  {maxZoom:16, pane:'overlayPane', opacity:.9}
).addTo(map);
let tileErrors = 0;
dark.on('tileerror', () => {
  if(++tileErrors === 6){
    map.removeLayer(dark);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      {maxZoom:18, attribution:'&copy; OpenStreetMap'}).addTo(map);
    toast('Kartenkacheln: OSM-Fallback aktiv');
  }
});
L.control.zoom({position:'bottomleft'}).addTo(map);
setTimeout(() => {
  const zc = document.querySelector('.leaflet-control-zoom');
  if(zc) zc.style.display = 'none';   // pinch + the FABs are enough on a phone
}, 0);

const layer = L.layerGroup().addTo(map);
/* Vector pins go to a single canvas: hundreds of markers stay cheap on a phone.
   The live rings keep the SVG renderer, because their sonar pulse is CSS. */
const canvasRenderer = L.canvas({padding:0.35});
const svgRenderer = L.svg({padding:0.35});
function drawMap(){
  layer.clearLayers();
  const rows = withDistance().filter(s => passes(s, true))
    // order of drawing: archive first, normal in the middle, live on top
    .sort((a,b) => (a.active === false ? 0 : isLive(a) ? 2 : 1) -
                   (b.active === false ? 0 : isLive(b) ? 2 : 1));
  for(const s of rows){
    const live = isLive(s);
    if(s.active === false){
      // vanished spot: kept forever as a faint grey hint where traps used to be
      const g = L.circleMarker([s.lat,s.lng], {
        radius:6, color:'#6b7686', weight:1.5, opacity:.7, dashArray:'2 3',
        fill:false, className:'pin-gone', renderer:canvasRenderer
      });
      g.on('click', () => openSheet(s));
      g.bindTooltip(s.street || 'Archiv', {direction:'top', className:'pinlabel-gone'});
      layer.addLayer(g);
      continue;
    }
    // Same size for every mobile spot: in the car a rare spot must be as easy
    // to see as a frequent one. Frequency is told by colour only.
    const r = isFixed(s) ? 11 : live ? 15 : 13;
    if(isNew(s) && !live){
      // a dashed halo so fresh finds are obvious on the map
      layer.addLayer(L.circleMarker([s.lat,s.lng], {
        radius:r + 8, color:'#3ad29f', weight:1.5, opacity:.85, dashArray:'3 4',
        fill:false, interactive:false, renderer:canvasRenderer
      }));
    }
    if(isFixed(s)){
      const limit = (s.vmax && s.vmax.length) ? s.vmax[0] : '•';
      const m = L.marker([s.lat,s.lng], {
        icon: L.divIcon({className:'', html:`<div class="pin-fixed">${limit}</div>`,
                         iconSize:[26,26], iconAnchor:[13,13]}),
        zIndexOffset: 200
      });
      m.on('click', () => openSheet(s));
      layer.addLayer(m);
      continue;
    }
    if(live){
      // pink ring with the speed limit inside; the sonar ring only while fresh
      if(isLiveFresh(s)){
        layer.addLayer(L.circleMarker([s.lat,s.lng], {
          radius:r + 6, color:LIVE_COLOUR, weight:2, opacity:.9, fill:false,
          className:'pin-pulse', interactive:false, renderer:svgRenderer
        }));
      }
      const m = L.circleMarker([s.lat,s.lng], {
        radius:r, color:LIVE_COLOUR, weight:3, fillColor:LIVE_COLOUR, fillOpacity:.34,
        className:'pin pin-live', renderer:svgRenderer
      });
      m.on('click', () => openSheet(s));
      const limit = (s.vmax && s.vmax.length) ? s.vmax[0] : '•';
      m.bindTooltip(String(limit), {permanent:true, direction:'center',
        className:'pinlabel-live', opacity:1});
      m.bringToFront();
      layer.addLayer(m);
      continue;
    }
    const c = COLOURS[sev(s)] || COLOURS[1];
    const m = L.circleMarker([s.lat,s.lng], {
      radius:r, color:c, weight:3, fillColor:c, fillOpacity:.34,
      className:'pin', renderer:canvasRenderer
    });
    m.on('click', () => openSheet(s));
    layer.addLayer(m);
  }
}
function fitToView(){
  if(state.following) return;
  const rows = withDistance().filter(s => passes(s, true) && s.active !== false);
  if(!rows.length) return;
  const all = state.town === 'all';
  map.fitBounds(rows.map(s=>[s.lat,s.lng]),
    {padding:[all?46:42, all?46:42], maxZoom: all?13:14});
}
const pinLabelStyle = document.createElement('style');
pinLabelStyle.textContent = '.pinlabel-gone{background:rgba(20,24,30,.92);border:1px solid #2c3341;border-radius:6px;'
  + 'color:#98a2b3;font-size:11px;font-weight:600;padding:3px 7px;box-shadow:0 4px 10px rgba(0,0,0,.4)}'
  + '.pinlabel-gone::before{display:none}';
document.head.appendChild(pinLabelStyle);

/* ---------- list ---------- */
function cardHTML(s, opts){
  opts = opts || {};
  const fixed = isFixed(s);
  const live = isLive(s);
  const c = fixed ? FIXED_COLOUR : live ? LIVE_COLOUR : (COLOURS[sev(s)] || COLOURS[1]);
  const limit = (s.vmax && s.vmax.length) ? s.vmax[0] : (live ? '•' : '–');
  const bits = [];
  if(s.city) bits.push(s.city);
  if(opts.showDate) bits.push('seit ' + fmtDate(s.first_seen));
  if(live) bits.push('live ' + timeOf(s.live_at) + ' · ' + liveAgo(s.live_at));
  if(fixed){
    bits.push(s.art || 'Fester Blitzer');
  } else {
    const n = s.days || 0;
    bits.push(`${n} ${n === 1 ? 'Messtag' : 'Messtage'}`);
    if(!opts.showDate && !live) bits.push(s.last_seen || '—');
  }
  const pct = Math.min(100, ((s.days||0)/24)*100);
  let tag = '';
  if(opts.showTag){
    if(live) tag = '<span class="tag live">LIVE</span>';
    else if(!s.active) tag = '<span class="tag gone">ARCHIV</span>';
    else if(isNew(s)) tag = '<span class="tag new">NEU</span>';
    else tag = '<span class="tag base">BESTAND</span>';
  }
  const signCls = fixed ? ' is-fixed' : live ? ' is-live' : '';
  return `<div class="card" data-i="${s.idx}">
      <div class="sign${signCls}" style="border-color:${c};color:${c}">${limit}</div>
      <div class="card__t">
        <b>${(s.street||'Unbekannt').replace(/</g,'&lt;')}</b>
        <small>${bits.join(' · ').replace(/</g,'&lt;')}</small>
      </div>
      ${opts.showDate ? '' : `<div class="dist">${fmtDist(s.dist)}</div>`}
      ${tag}
      ${fixed || live ? '' : `<div class="heat"><i style="width:${pct}%;background:${c}"></i></div>`}
    </div>`;
}

function renderList(){
  let rows = withDistance().filter(s => passes(s))
    .sort((a,b) => {
      if(state.sortByDistance && a.dist!=null && b.dist!=null) return a.dist-b.dist;
      return (isLive(b)?1:0) - (isLive(a)?1:0) ||
             (isFixed(b)?0:1) - (isFixed(a)?0:1) ||
             (b.days||0)-(a.days||0) || (b.reports||0)-(a.reports||0);
    });
  const box = $('#cards');
  if(!rows.length){ box.innerHTML = '<div class="empty">Nichts gefunden.</div>'; return; }
  const LIMIT = state.listLimit;
  const shown = rows.slice(0, LIMIT);
  box.innerHTML = shown.map(s => cardHTML(s)).join('');
  box.querySelectorAll('.card').forEach(el => {
    el.addEventListener('click', () => openSheet(SITES[Number(el.dataset.i)]));
  });
  appendMore(box, rows.length - shown.length, () => { state.listLimit += 80; renderList(); });
}

/* long lists are built in pages: a phone should not lay out 400 cards at once */
function appendMore(box, rest, onClick){
  if(rest <= 0) return;
  const b = document.createElement('button');
  b.className = 'more';
  b.textContent = `Weitere ${Math.min(80, rest)} anzeigen · ${rest} übrig`;
  b.addEventListener('click', onClick);
  box.appendChild(b);
}

/* ---------- new / archive view ---------- */
function newRows(){
  const mode = state.newMode;
  let rows = withDistance().filter(townMatch);
  if(mode === 'fixed') rows = rows.filter(isFixed);
  else if(mode === 'gone') rows = rows.filter(s => !s.active);
  else if(mode === 'all') { /* keep everything, archive included */ }
  else {
    const n = Number(mode) || 7;
    rows = rows.filter(s => s.active !== false && isNew(s) && ageDays(s.first_seen) <= n);
  }
  rows.sort((a,b) => (b.first_seen||'').localeCompare(a.first_seen||'')
                  || (a.street||'').localeCompare(b.street||''));
  return rows;
}

function renderNew(){
  const inTown = SITES.filter(s => s.active !== false && townMatch(s));
  const n7 = countNew(7), n30 = countNew(30);
  const fixedN = inTown.filter(isFixed).length;
  const goneN = SITES.filter(s => !s.active && townMatch(s)).length;
  const townLabel = state.town === 'all' ? '' : ' · ' + esc(state.town);
  $('#newhead').innerHTML = `
    <div class="tiles">
      <div class="tile"><b>${n7}</b><small>neu · 7 Tage</small></div>
      <div class="tile"><b>${n30}</b><small>neu · 30 Tage</small></div>
      <div class="tile"><b>${inTown.length}</b><small>in der Datenbank</small></div>
    </div>
    <div class="note">Archiv seit ${fmtDate(SEEDED)} · ${fixedN} feste Blitzer · ${goneN} nicht mehr gelistet${townLabel}.
    Der Abgleich läuft täglich auf dem Rechner; die App zeigt den letzten Stand.</div>`;

  const rows = newRows();
  const box = $('#newcards');
  if(!rows.length){
    box.innerHTML = '<div class="empty">Nichts Neues in diesem Zeitraum.</div>';
    return;
  }
  const shown = rows.slice(0, state.newLimit);
  box.innerHTML = shown.map(s => cardHTML(s, {showTag:true, showDate:true})).join('');
  box.querySelectorAll('.card').forEach(el => {
    el.addEventListener('click', () => openSheet(SITES[Number(el.dataset.i)]));
  });
  appendMore(box, rows.length - shown.length, () => { state.newLimit += 80; renderNew(); });
}

$('#chips-new').querySelectorAll('.chip').forEach(ch => {
  ch.addEventListener('click', () => {
    $('#chips-new').querySelectorAll('.chip').forEach(x => x.classList.remove('on'));
    ch.classList.add('on');
    state.newMode = ch.dataset.n;
    state.newLimit = 80;
    renderNew();
  });
});

/* ---------- sheet ---------- */
const sheet = $('#sheet'), bd = $('#bd');
function openSheet(s){
  const fixed = isFixed(s);
  const live = isLive(s);
  const c = live ? LIVE_COLOUR : fixed ? FIXED_COLOUR : (COLOURS[sev(s)] || COLOURS[1]);
  const limit = (s.vmax && s.vmax.length) ? s.vmax[0] : '–';

  $('#sh-title').textContent = s.street || 'Unbekannte Stelle';
  const sub = [s.city];
  if(live) sub.push('live gemeldet ' + timeOf(s.live_at) + ' (' + liveAgo(s.live_at) + ')');
  else if(fixed) sub.push(s.art || 'Fester Blitzer');
  else if(s.vmax && s.vmax.length) sub.push(s.vmax[0] + ' km/h');
  if(!s.active) sub.push('nicht mehr gelistet');
  $('#sh-sub').textContent = sub.filter(Boolean).join(' · ');
  $('#sh-note').textContent = s.note || '';

  $('#sh-l1').textContent = fixed ? 'Typ' : ((s.days || 0) === 1 ? 'Messtag' : 'Messtage');
  $('#sh-l2').textContent = 'Limit';
  $('#sh-l3').textContent = fixed ? 'gelistet' : live ? 'gemeldet' : 'zuletzt';

  const first = $('#sh-days');
  first.textContent = fixed ? (s.art || 'Fest') : (s.days || '–');
  first.style.color = c;
  first.style.fontSize = fixed ? '15px' : '';
  first.style.lineHeight = fixed ? '1.15' : '';

  $('#sh-vmax').textContent = limit;
  const last = $('#sh-last');
  last.textContent = fixed ? fmtDate(s.first_seen)
                 : live ? timeOf(s.live_at)
                 : (shortMonth(s.last_seen) || '–');
  last.style.fontSize = fixed ? '15px' : '17px';
  last.style.color = live ? LIVE_COLOUR : '';

  const months = s.months || {};
  const keys = Object.keys(months);
  const bars = $('#sh-bars'), labels = $('#sh-labels');
  if(keys.length){
    const max = Math.max(...keys.map(k=>months[k]), 1);
    bars.innerHTML = keys.map(k => {
      const v = months[k];
      const h = Math.max(3, (v/max)*66);
      return `<div class="${v>=3?'hi':''}" style="height:${h}px" title="${k}: ${v}"></div>`;
    }).join('');
    labels.innerHTML = keys.map(k => `<span>${k.split(' ')[0].slice(0,3)}</span>`).join('');
    bars.style.display = 'flex'; labels.style.display = 'flex';
  } else {
    bars.style.display = 'none'; labels.style.display = 'none';
  }

  const link = $('#sh-link');
  if(s.url){ link.href = s.url; link.style.display = 'block'; }
  else { link.style.display = 'none'; }

  sheet.classList.add('on'); bd.classList.add('on');
}
function closeSheet(){ sheet.classList.remove('on'); closeTownSheet(); }
bd.addEventListener('click', closeSheet);
$('#townbtn').addEventListener('click', openTownSheet);
$('#ts-close').addEventListener('click', closeTownSheet);
$('#ts-search').addEventListener('input', renderTownSheet);
let startY = 0, dragging = false;
sheet.addEventListener('touchstart', e => { startY = e.touches[0].clientY; dragging = true; }, {passive:true});
sheet.addEventListener('touchmove', e => {
  if(!dragging) return;
  const dy = e.touches[0].clientY - startY;
  if(dy > 0) sheet.style.transform = `translateY(${dy}px)`;
}, {passive:true});
sheet.addEventListener('touchend', e => {
  const dy = e.changedTouches[0].clientY - startY;
  dragging = false; sheet.style.transform = '';
  if(dy > 90) closeSheet();
});

/* ---------- tabs ---------- */
document.querySelectorAll('nav button').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach(x=>x.classList.remove('on'));
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('on'));
    b.classList.add('on');
    $('#v-' + b.dataset.v).classList.add('on');
    if(b.dataset.v === 'map') setTimeout(()=>map.invalidateSize(), 30);
    if(b.dataset.v === 'new') renderNew();
  });
});

/* ---------- filters + search ---------- */
document.querySelectorAll('.chip').forEach(ch => {
  ch.addEventListener('click', () => {
    document.querySelectorAll('.chip').forEach(x=>x.classList.remove('on'));
    ch.classList.add('on');
    state.filter = ch.dataset.f;
    state.sortByDistance = (ch.dataset.f === 'near');
    state.listLimit = 80;
    drawMap(); renderList();
  });
});
$('#q').addEventListener('input', e => {
  state.query = e.target.value.trim();
  state.listLimit = 80;
  drawMap(); renderList();
});

/* ---------- geolocation: locate once + continuous follow ---------- */
const meLayer = L.layerGroup().addTo(map);
let meMarker = null, meCircle = null;
const geoState = { protocol:'-', secure:false, permission:'-', fix:null, error:'-', source:'-' };

function setGeoStatus(patch){
  Object.assign(geoState, patch);
  const paint = (id, v) => { const el = document.getElementById(id); if(el) el.textContent = v; };
  paint('st-protocol', geoState.protocol);
  paint('st-secure', geoState.secure ? 'ja' : 'nein');
  paint('st-perm', geoState.permission);
  paint('st-fix', geoState.fix || '–');
  paint('st-src', geoState.source);
  paint('st-err', geoState.error);
}

function secureCheck(){
  const secure = window.isSecureContext === true;
  setGeoStatus({ protocol: location.protocol + '//' + location.hostname, secure });
  if(secure) return true;
  $('#warn').innerHTML = 'Standort braucht <b>HTTPS</b>. Diese Seite läuft auf <code>' +
    location.protocol + '//' + location.hostname + '</code> — Safari blockt dort jede Ortung. ' +
    'Über die https://-Adresse öffnen (GitHub Pages / Netlify).';
  $('#warn').classList.add('on');
  return false;
}

function paintMe(latlng, accuracy){
  const icon = L.divIcon({
    className:'', html:'<div class="me"><div class="me__pulse"></div><div class="me__dot"></div></div>',
    iconSize:[16,16], iconAnchor:[8,8]
  });
  if(!meMarker){
    meMarker = L.marker(latlng, {icon, interactive:false, zIndexOffset:1000}).addTo(meLayer);
  } else {
    meMarker.setLatLng(latlng);
  }
  if(accuracy != null){
    if(!meCircle){
      meCircle = L.circle(latlng, {radius:accuracy, color:'#2f7cf6', weight:1,
        fillColor:'#2f7cf6', fillOpacity:.08, interactive:false}).addTo(meLayer);
    } else {
      meCircle.setLatLng(latlng); meCircle.setRadius(accuracy);
    }
  }
}

// One fix, promise-shaped, with a permission probe first where available.
function locateOnce(){
  return new Promise((resolve, reject) => {
    if(!navigator.geolocation) return reject({code:0, message:'kein Geolocation-Objekt'});
    const opts = {enableHighAccuracy:true, maximumAge:0, timeout:20000};
    navigator.geolocation.getCurrentPosition(
      pos => resolve(pos),
      err => reject(err),
      opts
    );
  });
}

async function probePermission(){
  if(!navigator.permissions || !navigator.permissions.query) return;
  try{
    const st = await navigator.permissions.query({name:'geolocation'});
    setGeoStatus({ permission: st.state });
    st.onchange = () => setGeoStatus({ permission: st.state });
  }catch(e){ /* Safari hat die Query teils nicht */ }
}

function setFollow(on){
  state.following = on;
  $('#fabFollow').classList.toggle('on', on);
  $('#geo').dataset.on = on ? '1' : '0';
  $('#geotxt').textContent = on ? 'Folgen' : 'Nähe';
}

function describeError(err){
  if(!err) return 'unbekannt';
  if(err.code === 1) return 'verweigert (1)';
  if(err.code === 2) return 'nicht verfügbar (2)';
  if(err.code === 3) return 'Timeout (3)';
  return (err.message || 'Fehler') + ' (' + (err.code ?? '?') + ')';
}

// Coarse fallback: 5 km is enough to show where you are when iOS refuses.
async function ipFallback(){
  try{
    const r = await fetch('https://ipapi.co/json/', {cache:'no-store'});
    if(!r.ok) throw new Error('ipapi ' + r.status);
    const j = await r.json();
    if(typeof j.latitude !== 'number') throw new Error('keine Koordinaten');
    return [j.latitude, j.longitude];
  }catch(e){
    return null;
  }
}

async function startFollow(){
  if(!secureCheck()) return;
  if(!navigator.geolocation){ toast('Dieser Browser gibt keinen Standort her'); return; }
  if(state.following){ setFollow(false); return; }

  setFollow(true);
  toast('Standort wird angefragt …', 4000);
  await probePermission();

  // iOS gives a far more reliable permission prompt on a single getCurrentPosition
  // than on a watch, so warm it up with one fix and only then start tracking.
  let first;
  try{
    first = await locateOnce();
  }catch(err){
    setFollow(false);
    setGeoStatus({ error: describeError(err) });
    if(err.code === 1){
      toast('Safari blockt den Standort. Einstellungen → Datenschutz → Ortungsdienste → Safari → Beim Verwenden der App. Danach Seite neu laden.', 8000);
    } else if(err.code === 3){
      toast('Ortung timeout — nochmal tippen, kurz warten.', 5000);
    } else if(err.code === 2){
      toast('Standort nicht verfügbar (Flugmodus? VPN?).', 5000);
    } else {
      toast('Ortung fehlgeschlagen: ' + describeError(err), 5000);
    }
    return;
  }

  const ll = [first.coords.latitude, first.coords.longitude];
  state.pos = ll;
  setGeoStatus({
    fix: ll[0].toFixed(4) + ', ' + ll[1].toFixed(4) + ' ±' + Math.round(first.coords.accuracy || 0) + ' m',
    source: 'GPS', error: '-', permission: 'granted'
  });
  paintMe(ll, first.coords.accuracy);
  map.setView(ll, 16);
  drawMap(); renderList();

  if(state.watchId != null) navigator.geolocation.clearWatch(state.watchId);
  state.watchId = navigator.geolocation.watchPosition(pos => {
    const p = [pos.coords.latitude, pos.coords.longitude];
    const moved = !state.lastPos || haversine(state.lastPos, p) > 30;
    state.pos = p;
    setGeoStatus({ fix: p[0].toFixed(4) + ', ' + p[1].toFixed(4) + ' ±' + Math.round(pos.coords.accuracy || 0) + ' m' });
    paintMe(p, pos.coords.accuracy);
    if(state.following) map.setView(p, Math.max(map.getZoom(), 15), {animate:true});
    // rebuilding 300 markers every three seconds is what makes phones stutter
    if(moved){
      state.lastPos = p;
      drawMap(); renderList();
      if($('#townsheet').classList.contains('on')) renderTownSheet();
    }
  }, err => {
    setGeoStatus({ error: describeError(err) });
    setFollow(false);
  }, {enableHighAccuracy:true, maximumAge:3000, timeout:20000});
}

$('#fabFollow').addEventListener('click', startFollow);

$('#fabZoom').addEventListener('click', async () => {
  if(!secureCheck()) return;
  toast('Standort wird gesucht …', 4000);
  await probePermission();
  try{
    const pos = await locateOnce();
    const ll = [pos.coords.latitude, pos.coords.longitude];
    state.pos = ll;
    setGeoStatus({ fix: ll[0].toFixed(4) + ', ' + ll[1].toFixed(4) + ' ±' +
      Math.round(pos.coords.accuracy||0) + ' m', source:'GPS', error:'-' });
    paintMe(ll, pos.coords.accuracy);
    map.setView(ll, 16);
    drawMap(); renderList();
  }catch(err){
    setGeoStatus({ error: describeError(err) });
    if(err.code === 0 || err.code === 2){
      const fb = await ipFallback();
      if(fb){
        state.pos = fb;
        setGeoStatus({ fix: fb[0].toFixed(3) + ', ' + fb[1].toFixed(3) + ' (IP, grob)',
                       source:'IP-Schätzung', error:'GPS blockiert' });
        paintMe(fb, 5000);
        map.setView(fb, 13);
        drawMap(); renderList();
        toast('GPS blockiert — ungefähre Position über IP', 4000);
      } else {
        toast('Standort blockiert. ' + describeError(err), 5000);
      }
    } else {
      toast('Standort blockiert. ' + describeError(err), 6000);
    }
  }
});

$('#geo').addEventListener('click', startFollow);
setGeoStatus({});

/* ---------- boot ---------- */
setGeoStatus({
  protocol: location.protocol + '//' + location.hostname,
  secure: window.isSecureContext === true
});
// a remembered town that no longer has spots would show an empty map
if(state.town !== 'all' && !townOf(state.town)) state.town = 'all';
drawMap();
renderList();
renderTownButton();
updateTownLink();
renderNew();
syncHead();
window.addEventListener('resize', () => { syncHead(); map.invalidateSize(); });
</script>
</body>
</html>
"""


def _towns(sites):
    """The town picker payload: one entry per city that has active spots.

    `name` is the city as the pages spell it (that is what the app filters on),
    `url` the matching blitzer.de /ort/ page built from the archived slug,
    `lat`/`lng` the mean position, used to sort the picker by distance.
    """
    seen = {}
    order = []
    for s in sites:
        if s.get("active") is False:
            continue
        name = (s.get("city") or "").strip() or "Ohne Ort"
        if name not in seen:
            seen[name] = {"name": name, "n": 0, "url": "", "_lat": 0.0, "_lng": 0.0}
            order.append(name)
        t = seen[name]
        t["n"] += 1
        t["_lat"] += s.get("lat") or 0.0
        t["_lng"] += s.get("lng") or 0.0
        slug = (s.get("ort") or "").strip()
        if slug and not t["url"]:
            t["url"] = f"{mh.SITE_BASE}/ort/blitzer-in-{slug}"
    out = []
    for n in order:
        t = seen[n]
        if t["n"] <= 0:
            continue
        out.append({
            "name": t["name"], "n": t["n"], "url": t["url"],
            "lat": round(t["_lat"] / t["n"], 5),
            "lng": round(t["_lng"] / t["n"], 5),
        })
    out.sort(key=lambda t: (-t["n"], t["name"]))
    return out


def _leaflet_parts():
    """Leaflet inlined from vendor\\ when present, CDN links as a fallback.

    Inlining makes the page self-contained: one less third party that could
    be down or blocked while the map tiles already need the network anyway.
    """
    vjs = Path(__file__).with_name("vendor") / "leaflet-1.9.4.js"
    vcss = Path(__file__).with_name("vendor") / "leaflet-1.9.4.css"
    css_tag = '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
    js_tag = '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
    try:
        css = "<style>" + vcss.read_text(encoding="utf-8") + "</style>"
    except OSError:
        css = css_tag
    try:
        js = "<script>" + vjs.read_text(encoding="utf-8") + "</script>"
    except OSError:
        js = js_tag
    return css, js


def build(db_path, out_path, title="Blitzer Hotspots", orte=""):
    conn = mh.open_db(db_path)
    sites = mh.build_sites(conn)
    updated = conn.execute(
        "SELECT MAX(scraped_at) FROM webspots"
    ).fetchone()[0] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seeded = mh.meta_get(conn, "seeded_at") or updated
    conn.close()

    if not sites:
        raise SystemExit("no sites in db - run `mobile_hotspots.py sites` first")

    mobiles = sum(1 for s in sites if s.get("kind") != "fixed")
    fixed = len(sites) - mobiles
    live = sum(1 for s in sites if s.get("live") and s.get("kind") != "fixed")
    bits = [f"{mobiles} mobil", f"{fixed} fest"]
    if live:
        bits.append(f"{live} live")
    headline = " · ".join(bits) + f" · Stand {updated[:10]}"

    payload = json.dumps(sites, ensure_ascii=False).replace("</", "<\\/")
    towns = json.dumps(_towns(sites), ensure_ascii=False).replace("</", "<\\/")
    leaflet_css, leaflet_js = _leaflet_parts()
    page = (
        APP.replace("__DATA__", payload)
        .replace("__TOWNS__", towns)
        .replace("__LEAFLET_CSS__", leaflet_css)
        .replace("__LEAFLET_JS__", leaflet_js)
        .replace("__COUNT__", str(len(sites)))
        .replace("__HEADLINE__", html.escape(headline))
        .replace("__UPDATED__", html.escape(updated[:10]))
        .replace("__SEEDED__", html.escape(seeded))
        .replace("__SEEDED_DAY__", html.escape(seeded[:10]))
        .replace("__ORTE__", html.escape(orte or "Siegen, Kreuztal"))
        .replace("__TITLE__", html.escape(title))
    )
    out_path.write_text(page, encoding="utf-8")
    return len(sites), out_path


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(description="build the installable hotspot web app")
    ap.add_argument("--db", type=Path, default=mh.DB_PATH)
    ap.add_argument("--out", type=Path, default=Path(__file__).with_name("blitzer_app.html"))
    ap.add_argument("--title", default="Blitzer Hotspots")
    ap.add_argument("--orte", default="Umkreis 30 km um Siegen")
    args = ap.parse_args(argv)

    count, path = build(args.db, args.out, args.title, args.orte)
    size = path.stat().st_size / 1024
    print(f"{count} Messstelle(n) eingebettet -> {path} ({size:.0f} kB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
