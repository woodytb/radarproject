"""Baut die animierte Störkarte (eine eigenständige HTML-Datei).

Pro Stunde ein Frame; jeder Wert ist der Anteil gestörter Flugzeuge in
einem gleitenden 3-Stunden-Fenster (glättet nächtliche Verkehrslücken).
Die Daten werden kompakt als Base64-Uint8-Matrizen eingebettet.

Dazu kommen die Ringe beim Aufflammen (flare_onsets) und die Detailansicht mit
einzelnen Flugzeugen (detail_flights). Die Flugzeugdaten sind anonymisiert: Die
Kennung (ICAO) wird nur zum Bilden der Spuren verwendet und nicht exportiert.

    python -m gpsjam.build_map
"""
from __future__ import annotations

import base64
import json

import h3
import numpy as np
import pandas as pd

from . import config as C

TEMPLATE = C.ROOT / "src" / "gpsjam" / "templates" / "map.html"
WINDOW_H = 3
NO_DATA = 255


def _b64(a: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(a, dtype=np.uint8).tobytes()).decode()


def _land() -> list[list[float]]:
    """TopoJSON (Natural Earth 1:50m) -> Länderumrisse im Kartenausschnitt."""
    topo = json.loads((C.ROOT / "data" / "ref" / "countries-50m.json").read_text())
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)

    def ring(idx: list[int]) -> list[tuple[float, float]]:
        out = []
        for i in idx:
            out += arcs[i] if i >= 0 else arcs[~i][::-1]
        return out

    rings = []
    for g in topo["objects"]["countries"]["geometries"]:
        polys = g.get("arcs", [])
        polys = polys if g["type"] == "MultiPolygon" else [polys]
        for poly in polys:
            for r in poly:
                pts = ring(r)
                if not any(-40 <= lo <= 65 and 20 <= la <= 80 for lo, la in pts):
                    continue
                flat = []
                # Tschukotka liegt jenseits der Datumsgrenze (-180°): auf +180° ff. umrechnen,
                # dann auf den Ausschnitt klemmen – sonst zieht Russland beim Füllen ein Band quer über die Karte
                for lo, la in ((min(max(lo + 360 if lo < -100 else lo, -75), 110), min(max(la, 5), 84)) for lo, la in pts):
                    if not flat or abs(flat[-2] - lo) + abs(flat[-1] - la) > 0.04:
                        flat += [round(lo, 2), round(la, 2)]
                if len(flat) >= 6:
                    rings.append(flat)
    return rings


def hourly_frames(hour: pd.DataFrame) -> tuple[np.ndarray, pd.DatetimeIndex, np.ndarray, np.ndarray]:
    """Zellen x Stunden: Anteil gestörter Flugzeuge in % (NO_DATA = zu wenig Verkehr)
    im gleitenden 3-h-Fenster, dazu die Flugzeugzahl im Fenster."""
    hours = pd.date_range(hour["hour"].min(), hour["hour"].max(), freq="h")
    cells = np.sort(hour["h3"].unique())
    ci = {c: i for i, c in enumerate(cells)}
    ti = {t: i for i, t in enumerate(hours)}

    n_ac = np.zeros((len(cells), len(hours)), np.int32)
    n_bad = np.zeros_like(n_ac)
    r, c = hour["h3"].map(ci).to_numpy(), hour["hour"].map(ti).to_numpy()
    n_ac[r, c], n_bad[r, c] = hour["n_ac"], hour["n_bad"]

    # gleitendes, zentriertes 3-h-Fenster
    k = np.ones(WINDOW_H, np.int32)
    roll = lambda m: np.apply_along_axis(lambda v: np.convolve(v, k, "same"), 1, m)  # noqa: E731
    w_ac, w_bad = roll(n_ac), roll(n_bad)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.where(w_ac >= C.MIN_AIRCRAFT_HOUR, np.round(100 * w_bad / w_ac), NO_DATA)
    # einzelnes betroffenes Flugzeug -> als ungestört zeigen (siehe MIN_BAD_AIRCRAFT)
    ratio = np.where((ratio != NO_DATA) & (w_bad < C.MIN_BAD_AIRCRAFT), 0, ratio)

    # nur Zellen behalten, die irgendwann Verkehr hatten
    keep = (ratio != NO_DATA).any(1)
    return cells[keep], hours, ratio[keep], np.minimum(w_ac[keep], 254)


def flare_onsets(ratio: np.ndarray) -> list[list[int]]:
    """[Stunde, Zelle] jeder Wabe, die nach einer Ruhephase dauerhaft über 10 % steigt."""
    valid = ratio != NO_DATA
    jam = valid & (ratio >= 100 * C.RATIO_HIGH)
    quiet, after = C.FLARE_QUIET_H, C.FLARE_AFTER_H
    onsets = []
    for f in range(quiet, ratio.shape[1]):
        before = jam[:, f - quiet:f].any(1)
        enough = valid[:, f - quiet:f].sum(1) >= C.FLARE_MIN_VALID_H
        lasting = jam[:, f + 1:f + 1 + after].sum(1) >= C.FLARE_AFTER_MIN
        onsets += [[f, int(c)] for c in np.flatnonzero(jam[:, f] & ~before & enough & lasting)]
    return onsets


def _detail_day(path) -> pd.DataFrame:
    """Silver eines Tages mit denselben Filtern wie die Aggregation, auf den Datenausschnitt begrenzt."""
    b = C.DETAIL_DATA_BOX
    df = pd.read_parquet(path, columns=["icao", "ts", "lat", "lon", "nic", "version", "afterglow"])
    df = df[df["version"].fillna(0) >= 1]
    df = df[df.groupby("icao")["nic"].transform("max") >= C.NIC_BAD_BELOW]
    df = df[df["lat"].between(b["lat0"], b["lat1"]) & df["lon"].between(b["lon0"], b["lon1"])]
    return df.assign(hour=df["ts"].dt.floor("h"), bad=df["nic"] < C.NIC_BAD_BELOW)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = np.radians(lat1), np.radians(lat2), np.radians(lon2 - lon1)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(12742 * np.arcsin(min(1.0, np.sqrt(a))))


def detail_flights() -> dict:
    """Spuren einzelner Flugzeuge im Fenster mit den meisten betroffenen Flugzeugen am Tag."""
    best, best_score = None, -1
    lo, hi = C.DETAIL_DAY_HOURS
    for day in sorted((C.SILVER_DIR / "positions").glob("date=*")):
        df = _detail_day(day)
        for start in sorted(df["hour"].unique()):
            start = pd.Timestamp(start)
            if not lo <= start.hour <= hi - C.DETAIL_HOURS:
                continue
            w = df[(df["hour"] >= start) & (df["hour"] < start + pd.Timedelta(hours=C.DETAIL_HOURS))]
            score = w.loc[w["bad"] | w["afterglow"], "icao"].nunique()
            if score > best_score:
                best, best_score = (day, start), score

    day, start = best
    df = _detail_day(day)
    df = df[(df["ts"] >= start) & (df["ts"] < start + pd.Timedelta(hours=C.DETAIL_HOURS))]
    t0 = int(start.timestamp())
    tracks: list[list[int]] = []
    for _, g in df.sort_values("ts").groupby("icao", sort=False):
        cur: list[int] = []
        prev = None  # t, lat, lon der letzten übernommenen Meldung
        prev_state = None
        for t, lat, lon, nic, glow in zip(g["ts"].map(lambda x: int(x.timestamp())), g["lat"], g["lon"], g["nic"], g["afterglow"]):
            state = (nic < C.NIC_BAD_BELOW, bool(glow))
            if prev is not None:
                dt = t - prev[0]
                if dt <= 0:
                    continue
                if dt > C.DETAIL_MAX_GAP_S or _km(prev[1], prev[2], lat, lon) / dt * 3600 > C.DETAIL_MAX_SPEED_KMH:
                    if len(cur) >= 10:
                        tracks.append(cur)
                    cur, prev, prev_state = [], None, None
                elif dt < C.DETAIL_MIN_STEP_S and state == prev_state:
                    continue
            cur += [t - t0, round(float(lon) * 1000), round(float(lat) * 1000), int(nic), int(glow)]
            prev, prev_state = (t, lat, lon), state
        if len(cur) >= 10:
            tracks.append(cur)
    return dict(name=C.DETAIL_NAME, t0=t0, dur=C.DETAIL_HOURS * 3600, box=C.DETAIL_VIEW_BOX, tracks=tracks,
                affected=int(best_score))


def build() -> str:
    hour = pd.read_parquet(C.GOLD_DIR / "hex_hour.parquet")
    zones = pd.read_parquet(C.GOLD_DIR / "zones_day.parquet")
    # gleichnamige Komponenten eines Tages zusammenfassen (Mittelpunkt der größten behalten)
    zones = (zones.sort_values("area_km2", ascending=False)
                  .groupby(["date", "zone"], as_index=False)
                  .agg(area_km2=("area_km2", "sum"), n_ac=("n_ac", "sum"), n_bad=("n_bad", "sum"),
                       center_lat=("center_lat", "first"), center_lon=("center_lon", "first")))
    zones["ratio"] = zones["n_bad"] / zones["n_ac"]
    zones = zones.sort_values(["date", "area_km2"], ascending=[True, False])
    summary = json.loads((C.GOLD_DIR / "summary.json").read_text())
    cells, hours, ratio, w_ac = hourly_frames(hour)

    strs = [h3.int_to_str(int(x)) for x in cells]
    polys = [[round(v, 3) for lat, lon in h3.cell_to_boundary(s) for v in (lon, lat)] for s in strs]
    area = np.array([h3.cell_area(s, "km^2") for s in strs])

    jam = (ratio >= 100 * C.RATIO_HIGH) & (ratio != NO_DATA)
    timeline = (jam * area[:, None]).sum(0)

    data = dict(
        t0=int(hours[0].timestamp()), n_frames=len(hours), n_cells=len(cells),
        polys=polys, area=np.round(area).astype(int).tolist(),
        ratio=_b64(ratio.T), n_ac=_b64(w_ac.T),  # frame-major: [frame][cell]
        timeline=np.round(timeline).astype(int).tolist(),
        zones=[dict(d=str(z.date)[:10], name=z.zone, area=int(z.area_km2), ratio=round(z.ratio, 3),
                    n_ac=int(z.n_ac), lat=round(z.center_lat, 2), lon=round(z.center_lon, 2))
               for z in zones.itertuples()],
        land=_land(), summary={k: v for k, v in summary.items() if k != "top_zones"},
        onsets=flare_onsets(ratio), detail=detail_flights(), nic_bad=C.NIC_BAD_BELOW,
        afterglow_km=C.AFTERGLOW_MAX_KM,
    )
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Fragment fürs Hosting als Artifact (das Gerüst ergänzt der Host) …
    (C.OUTPUT_DIR / "stoerkarte.fragment.html").write_text(html)
    # … und eine eigenständige Datei zum lokalen Öffnen im Browser
    out = C.OUTPUT_DIR / "stoerkarte.html"
    out.write_text('<!doctype html>\n<html lang="de"><head><meta charset="utf-8">'
                   '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
                   '<style>body{margin:0}[hidden]{display:none!important}</style></head><body>\n'
                   + html + "\n</body></html>\n")
    return f"{out} ({out.stat().st_size / 2**20:.1f} MB, {len(cells)} Zellen x {len(hours)} Stunden)"


if __name__ == "__main__":
    print(build())
