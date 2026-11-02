"""Standbilder der Videoszenen als HTML (1920x1080), gestaltet wie Karte und Deck.
Gerendert werden sie per Headless-Chrome (siehe build_video.py)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHARTS = ROOT / "output" / "charts"
ASSETS = ROOT / "video" / "assets"

CHAPTERS = ["Frage", "Idee", "Methode", "Daten", "Qualität", "Statistik", "Ergebnis", "Fazit"]
CHAPTER_OF = dict(intro=0, frage=0, idee=1, nic=1, crispdm=2, daten=3, pipeline=3, nachwirkung=4,
                  methode=5, karte=6, regionen=6, tagesgang=6, grenzen=7, fazit=7)

CSS = """
:root { --bg:#0D171B; --panel:#14252B; --line:#1E3138; --ink:#E6EEEE; --ink2:#A9BCBF; --ink3:#71898F;
        --h1:#6E3420; --h2:#B2482A; --h3:#EC6A3E; --h4:#FFB58A; --cool:#5F8F9C; }
* { box-sizing:border-box; margin:0; }
html, body { width:1920px; height:1080px; overflow:hidden; background:var(--bg); color:var(--ink);
  font-family:"IBM Plex Sans", "Helvetica Neue", Arial, sans-serif; }
.stage { position:relative; width:1920px; height:1080px; padding:110px 128px 150px; display:flex; flex-direction:column; gap:44px; }
.eyebrow { font-family:"IBM Plex Mono", Menlo, monospace; font-size:24px; letter-spacing:3px; text-transform:uppercase; color:var(--h3); }
h1 { font-family:"IBM Plex Sans Condensed", "Arial Narrow", sans-serif; font-size:124px; font-weight:700; line-height:1.0; }
h2 { font-family:"IBM Plex Sans Condensed", "Arial Narrow", sans-serif; font-size:68px; font-weight:700; line-height:1.08; text-wrap:balance; }
h3 { font-family:"IBM Plex Sans Condensed", "Arial Narrow", sans-serif; font-size:40px; font-weight:600; }
p { font-size:30px; line-height:1.4; color:var(--ink2); }
.mono { font-family:"IBM Plex Mono", Menlo, monospace; font-variant-numeric:tabular-nums; }
.row { display:flex; gap:28px; }
.card { flex:1; background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:34px; display:flex; flex-direction:column; gap:14px; }
.card.hot { border:2px solid var(--h3); }
.arrow { align-self:center; width:0; height:0; border-top:16px solid transparent; border-bottom:16px solid transparent; border-left:26px solid var(--cool); flex:none; }
.chapters { position:absolute; left:128px; right:128px; bottom:56px; display:flex; gap:10px; }
.chapters div { flex:1; display:flex; flex-direction:column; gap:10px; }
.chapters i { display:block; height:4px; border-radius:2px; background:var(--line); }
.chapters span { font-family:"IBM Plex Mono", Menlo, monospace; font-size:20px; color:var(--ink3); letter-spacing:1px; }
.chapters .on i { background:var(--h3); } .chapters .on span { color:var(--ink); }
.chapters .done i { background:var(--h2); }
img.fig { display:block; object-fit:contain; align-self:center; }
.big { font-family:"IBM Plex Mono", Menlo, monospace; font-size:104px; font-weight:500; line-height:1; color:var(--ink); }
.hl { color:var(--h3); }
"""

HEAD = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
{extra}<style>{css}</style></head><body><div class="stage">"""


def _chapters(shot: str) -> str:
    cur = CHAPTER_OF[shot]
    parts = []
    for i, name in enumerate(CHAPTERS):
        cls = "on" if i == cur else "done" if i < cur else ""
        parts.append(f'<div class="{cls}"><i></i><span>{name}</span></div>')
    return f'<div class="chapters">{"".join(parts)}</div>'


def _head(eyebrow: str, title: str) -> str:
    return f'<div style="display:flex;flex-direction:column;gap:18px"><div class="eyebrow">{eyebrow}</div><h2>{title}</h2></div>'


def _img(name: str, w: int, h: int) -> str:
    return f'<img class="fig" src="{(CHARTS / name).as_uri()}" style="width:{w}px;height:{h}px">'


def _crisp_cycle() -> str:
    """Die sechs Phasen auf einem Ring, Pfeile im Uhrzeigersinn, Rücksprung Evaluation -> Data Preparation."""
    import math
    names = ["Business Understanding", "Data Understanding", "Data Preparation", "Modeling", "Evaluation", "Deployment"]
    cx, cy, rx, ry, bw, bh = 832, 255, 620, 200, 360, 104
    pos = [(cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a))) for a in (-120, -60, 0, 60, 120, 180)]

    def exit_t(dx: float, dy: float) -> float:
        return min((bw / 2 + 14) / abs(dx) if dx else 9, (bh / 2 + 14) / abs(dy) if dy else 9)

    lines = []
    for i in range(6):
        (x1, y1), (x2, y2) = pos[i], pos[(i + 1) % 6]
        dx, dy = x2 - x1, y2 - y1
        t = exit_t(dx, dy)
        lines.append(f'<line x1="{x1 + dx * t:.0f}" y1="{y1 + dy * t:.0f}" x2="{x2 - dx * t:.0f}" y2="{y2 - dy * t:.0f}"/>')
    (ex, ey), (px, py) = pos[4], pos[2]
    back = f'<path d="M {ex + 60:.0f} {ey - bh / 2 - 12:.0f} Q {cx + 120} {cy + 40} {px - bw / 2 - 14:.0f} {py + 20:.0f}"/>'
    boxes = "".join(
        f'<div class="card{" hot" if i in (2, 4) else ""}" style="position:absolute;left:{x - bw / 2:.0f}px;top:{y - bh / 2:.0f}px;'
        f'width:{bw}px;height:{bh}px;padding:18px 24px;gap:4px;justify-content:center">'
        f'<div class="mono hl" style="font-size:20px">{i + 1}</div><h3 style="font-size:32px;line-height:1.05">{n}</h3></div>'
        for i, ((x, y), n) in enumerate(zip(pos, names)))
    return f"""<div style="position:relative;flex:1">
<svg width="1664" height="640" viewBox="0 0 1664 640" style="position:absolute;inset:0">
<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#5F8F9C"/></marker>
<marker id="b" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#EC6A3E"/></marker></defs>
<g stroke="#5F8F9C" stroke-width="3" marker-end="url(#a)">{"".join(lines)}</g>
<g stroke="#EC6A3E" stroke-width="4" stroke-dasharray="12 9" fill="none" marker-end="url(#b)">{back}</g>
</svg>{boxes}
<p style="position:absolute;left:0;top:{cy + ry + 80}px;width:1664px;font-size:30px;color:var(--ink)"><span class="mono hl">Wichtigster Rücksprung:</span> Evaluation findet falsche Störstreifen → zurück zur Datenaufbereitung</p>
</div>"""


BODIES = {
    "intro": lambda: f"""
<img src="{(ASSETS / 'hex.svg').as_uri()}" style="position:absolute;right:120px;top:120px;width:720px;height:780px">
<div style="flex:1;display:flex;flex-direction:column;justify-content:center;gap:40px;width:980px">
  <div class="eyebrow">Big-Data-Projekt</div>
  <h1>GPS-Störradar Europa</h1>
  <p style="font-size:44px;line-height:1.3">Wie 860 Millionen Flugzeugmeldungen zeigen, wo Störsender aktiv sind</p>
  <p class="mono" style="font-size:24px;color:var(--ink3)">ADS-B · adsb.lol · 15.–22. Oktober 2026</p>
</div>""",

    "frage": lambda: _head("Die Frage", "Wo in Europa wird GPS gestört?") + """
<div class="row" style="align-items:stretch">
  <div class="card"><div class="mono hl" style="font-size:24px">Luftfahrt</div><h3>Flugsicherheit</h3><p>Satellitengestützte Anflüge und Warnsysteme fallen aus, die Crew fliegt nach Ersatzverfahren.</p></div>
  <div class="card"><div class="mono hl" style="font-size:24px">See</div><h3>Schifffahrt</h3><p>Dieselben Störsender treffen Schiffe auf Ostsee und Schwarzem Meer.</p></div>
  <div class="card"><div class="mono hl" style="font-size:24px">Politik</div><h3>Transparenz</h3><p>Wo und wann gestört wird, ist kaum öffentlich messbar.</p></div>
</div>
<p style="font-size:40px;line-height:1.35;color:var(--ink);width:1500px">Kann man das mit <span class="hl">frei verfügbaren Daten</span> selbst messen?</p>""",

    "idee": lambda: _head("Die Idee", "Jedes Flugzeug ist ein Messfühler") + """
<div class="row" style="align-items:stretch">
  <div class="card"><div class="mono hl" style="font-size:24px">1</div><h3>GPS-Empfang</h3><p>Bordrechner bestimmt Position und deren Zuverlässigkeit</p></div><div class="arrow"></div>
  <div class="card"><div class="mono hl" style="font-size:24px">2</div><h3>ADS-B</h3><p>Sendet beides jede Sekunde auf 1090 MHz</p></div><div class="arrow"></div>
  <div class="card"><div class="mono hl" style="font-size:24px">3</div><h3>Empfänger</h3><p>Freiwillige betreiben Bodenstationen</p></div><div class="arrow"></div>
  <div class="card"><div class="mono hl" style="font-size:24px">4</div><h3>adsb.lol</h3><p>Tagesarchiv aller Flugspuren, offene Lizenz</p></div>
</div>
<p style="font-size:40px;line-height:1.35;color:var(--ink);width:1500px">Wird GPS gestört, sinkt der gemeldete <span class="hl">NIC-Wert</span> (Navigation Integrity Category, 0–11).</p>""",

    "nic": lambda: _head("Das Signal", "In Störzonen bricht der NIC auf 0 ein") + _img("nic_verteilung.png", 1664, 618),

    "crispdm": lambda: _head("Vorgehensmodell", "CRISP-DM: sechs Phasen, sechs Rücksprünge") + _crisp_cycle(),

    "daten": lambda: _head("Die Daten", "Acht Tage, 152 GB Flugspuren") + """
<div class="row" style="gap:64px">
  <div><div class="big">860 Mio.</div><p>Positionsmeldungen</p></div>
  <div><div class="big">609.038</div><p>Flugspuren</p></div>
  <div><div class="big">23,4 GB</div><p>gzip · ≈ 152 GB JSON</p></div>
</div>
<div style="display:flex;flex-direction:column;gap:14px">
""" + "".join(
        f'<div style="display:grid;grid-template-columns:520px 1fr 230px;gap:24px;align-items:center">'
        f'<p style="font-size:28px">{label}</p><div style="height:30px;width:{w}%;background:{c};border-radius:4px"></div>'
        f'<p class="mono" style="font-size:28px;color:var(--ink);text-align:right">{v}</p></div>'
        for label, w, c, v in [("Alle Meldungen", 100, "#EC6A3E", "859.853.469"),
                               ("mit NIC, in der Luft", 91.8, "#B2482A", "158.402.701"),
                               ("im Untersuchungsgebiet", 85.2, "#6E3420", "41.149.489"),
                               ("Zellen-Stunden", 64.7, "#5F8F9C", "601.503")]) + """
<p style="font-size:22px;color:var(--ink3)">Balken in logarithmischer Skala</p></div>""",

    "pipeline": lambda: _head("Die Pipeline", "Bronze, Silver, Gold – mit Dask und Spark") + """
<div class="row" style="align-items:stretch">
  <div class="card"><div class="mono" style="font-size:24px;color:#C9873E">Bronze</div><h3>Rohdaten</h3><p>gzip-JSON, 1 Datei je Flugzeug und Tag</p></div><div class="arrow"></div>
  <div class="card"><div class="mono" style="font-size:24px;color:var(--ink2)">Silver</div><h3>Parsen</h3><p>Dask, 5 Prozesse, ~35 s pro Tag; Filter, H3-Wabe, Nachwirkung</p></div><div class="arrow"></div>
  <div class="card"><div class="mono" style="font-size:24px;color:var(--h4)">Gold</div><h3>Zählen</h3><p>Flugzeuge und gestörte Flugzeuge je Wabe und Stunde</p></div><div class="arrow"></div>
  <div class="card hot"><div class="mono hl" style="font-size:24px">Analyse</div><h3>Erkennen</h3><p>Binomialtest, Zonen, Karte</p></div>
</div>
<div class="row" style="gap:64px;align-items:center">
  <div><div class="big hl">100 %</div><p>Dask = Spark, 601.503 Zellen-Stunden</p></div>
  <div><div class="big">1.770 km²</div><p>je H3-Wabe, genau 6 Nachbarn</p></div>
</div>""",

    "nachwirkung": lambda: _head("Datenqualität", "Die Störung fliegt mit") + _img("nachwirkung_vorher_nachher.png", 1290, 661),

    "methode": lambda: _head("Statistik", "Ein Binomialtest für jede Wabe") + r"""
<div id="f" style="font-size:46px;color:var(--ink)"></div>
<div class="row" style="gap:28px">
  <div class="card"><p class="mono" style="font-size:26px">p₀ = 0,29 % Grundrauschen</p><p style="color:var(--ink)">Wie wahrscheinlich ist so viel Störung durch Zufall?</p></div>
  <div class="card"><p class="mono" style="font-size:26px">1 von 5 gestört</p><p style="color:var(--ink)">p = 0,014 → <b>kann Zufall sein</b></p></div>
  <div class="card hot"><p class="mono" style="font-size:26px">6 von 30 gestört</p><p style="color:var(--ink)">p = 3·10⁻¹⁰ → <b class="hl">gestört</b></p></div>
</div>
<script>
katex.render(String.raw`\text{gestört} = \Big[\tfrac{k}{n} \ge 10\,\%\Big] \wedge \big[k \ge 2\big] \wedge \Bigg[\sum_{i=k}^{n} \binom{n}{i}\, p_0^{\,i}(1-p_0)^{\,n-i} < 10^{-4}\Bigg]`,
  document.getElementById('f'), {displayMode:true});
</script>""",

    "regionen": lambda: _head("Ergebnis", "Täglich 479.000 km² gestört – mehr als Deutschland") + _img("regionen_verlauf.png", 1450, 652),

    "tagesgang": lambda: _head("Tagesgang", "Nachts trifft es mehr Flugzeuge") + """
<div class="row" style="align-items:center;gap:56px">""" + _img("tagesgang.png", 1100, 572) + """
  <div style="display:flex;flex-direction:column;gap:28px">
    <div><div class="big hl">35 %</div><p>um 01 Uhr UTC</p></div>
    <div><div class="big">13 %</div><p>um 07 Uhr UTC</p></div>
    <p style="font-size:26px">Vorsicht: nachts anderer Verkehrsmix</p>
  </div>
</div>""",

    "grenzen": lambda: _head("Grenzen und Ausblick", "Was die Karte nicht zeigt – und was als Nächstes kommt") + """
<div class="row" style="align-items:stretch">
  <div class="card" style="gap:22px"><h3>Grenzen</h3>
    <p style="font-size:36px;color:var(--ink)">Nur wo geflogen wird: über Russland, Belarus, Ukraine keine Daten</p>
    <p style="font-size:36px;color:var(--ink)">NIC unterscheidet nicht zwischen Stören und Fälschen</p>
    <p style="font-size:36px;color:var(--ink)">Zonenmittelpunkt ≠ Senderstandort</p></div>
  <div class="card hot" style="gap:22px"><h3>Nächste Schritte</h3>
    <p style="font-size:36px;color:var(--ink)">Live-Radar mit Spark Structured Streaming</p>
    <p style="font-size:36px;color:var(--ink)">Spoofing über unmögliche Positionssprünge erkennen</p>
    <p style="font-size:36px;color:var(--ink)">Monate statt Tage auf einem Cluster</p></div>
</div>""",

    "fazit": lambda: """
<div style="position:absolute;inset:0;background:var(--h3)"></div>
<div style="position:relative;flex:1;display:flex;flex-direction:column;justify-content:center;gap:48px">
  <div class="eyebrow" style="color:var(--bg)">Fazit</div>
  <h1 style="font-size:96px;color:var(--bg);width:1600px">Offene Flugdaten reichen, um Störsender über Europa sichtbar zu machen.</h1>
  <p style="font-size:36px;color:var(--bg)">Rechnen: Dask und Spark · Die eigentliche Arbeit: Datenqualität</p>
  <p class="mono" style="font-size:26px;color:var(--bg)">Danke fürs Zuschauen · Daten: adsb.lol (ODbL)</p>
</div>""",
}


def html_for(shot: str) -> str:
    extra = ""
    if shot == "methode":
        extra = ('<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css">'
                 '<script src="https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js"></script>')
    chapters = "" if shot in ("intro", "fazit") else _chapters(shot)
    return HEAD.format(extra=extra, css=CSS) + BODIES[shot]() + chapters + "</div></body></html>"
