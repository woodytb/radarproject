"""Peilung: Wo verlieren Flugzeuge über der Ostsee ihr GPS, und wo bekommen sie es zurück?

Aus den Silver-Daten werden je Flug die Übergänge gut -> schlecht ("ein") und
schlecht -> gut ("aus") bestimmt. Durch diese Punkte wird ein Kreis gelegt
(RANSAC, dann feinere Gittersuche). Drei Prüfungen ordnen das Ergebnis ein:

- Stabilität: Mittelpunkt je Tag
- Richtung: Fliegen die Maschinen beim Ausfall auf den Mittelpunkt zu? Gegenprobe mit Kontrollorten
- Höhe: Wächst der Radius mit der Flughöhe, wie es ein einzelner Sender am Boden
  mit Sichtverbindung (Funkhorizont) erwarten ließe?

Der Mittelpunkt ist ein Schwerpunkt der Störung, kein Senderstandort.

    python -m gpsjam.peilung
"""
from __future__ import annotations

import base64
import json

import numpy as np
import pandas as pd

from . import config as C

BOX = dict(lat0=51.0, lat1=59.0, lon0=12.0, lon1=29.0)   # südliche und mittlere Ostsee
MAX_GAP_S = 180          # Übergang nur zwischen zwei Meldungen, die höchstens 3 min auseinanderliegen
MIN_ALT_FT = 5000
RANSAC_TRIES, RANSAC_BAND_KM, SEED = 6000, 20, 1
R_EARTH = 6371.0
REF = dict(lat=55.0, lon=20.0)  # Bezugspunkt der lokalen Projektion
CANDIDATES = {
    "Kaliningrad": (54.71, 20.51), "Baltijsk": (54.65, 19.91), "Danzig": (54.35, 18.65),
    "Gotland (Kontrolle)": (57.5, 18.5), "Riga (Kontrolle)": (56.95, 24.1),
}
ALT_BANDS = [(5000, 15000), (15000, 25000), (25000, 32000), (32000, 37000), (37000, 45000)]
OUT = C.GOLD_DIR / "peilung.json"


def _xy(lat, lon, lat0=REF["lat"], lon0=REF["lon"]):
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    return np.radians(lon - lon0) * R_EARTH * np.cos(np.radians(lat0)), np.radians(lat - lat0) * R_EARTH


def _ll(x, y):
    return REF["lat"] + np.degrees(y / R_EARTH), REF["lon"] + np.degrees(x / (R_EARTH * np.cos(np.radians(REF["lat"]))))


def transitions() -> pd.DataFrame:
    """Alle Übergänge im Kartenausschnitt der Peilung, mit dem Punkt davor."""
    rows = []
    for day in sorted((C.SILVER_DIR / "positions").glob("date=*")):
        t = pd.read_parquet(day, columns=["icao", "ts", "lat", "lon", "alt_ft", "nic", "afterglow"])
        t["ts"] = pd.to_datetime(t["ts"], utc=True)
        t = t.sort_values(["icao", "ts"])
        p = t.shift()
        bad, prev_bad = t["nic"] < C.NIC_BAD_BELOW, p["nic"] < C.NIC_BAD_BELOW
        ok = (t["icao"].eq(p["icao"]) & ((t["ts"] - p["ts"]).dt.total_seconds() <= MAX_GAP_S)
              & (t["alt_ft"] >= MIN_ALT_FT) & (p["alt_ft"] >= MIN_ALT_FT) & p["nic"].notna())
        for kind, m in (("ein", ok & bad & ~prev_bad & ~t["afterglow"]),
                        ("aus", ok & ~bad & prev_bad & ~p["afterglow"].astype(bool))):
            s, q = t[m], p[m]
            rows.append(pd.DataFrame(dict(
                date=day.name.split("=")[1], kind=kind,
                lat=(s["lat"].values + q["lat"].values) / 2, lon=(s["lon"].values + q["lon"].values) / 2,
                alt_ft=(s["alt_ft"].values + q["alt_ft"].values) / 2,
                lat_prev=q["lat"].values, lon_prev=q["lon"].values, lat_next=s["lat"].values, lon_next=s["lon"].values)))
    tr = pd.concat(rows, ignore_index=True)
    return tr[tr["lat"].between(BOX["lat0"], BOX["lat1"]) & tr["lon"].between(BOX["lon0"], BOX["lon1"])].reset_index(drop=True)


def _circle3(p):
    (x1, y1), (x2, y2), (x3, y3) = p
    a = 2 * np.array([[x2 - x1, y2 - y1], [x3 - x1, y3 - y1]])
    if abs(np.linalg.det(a)) < 1e-6:
        return None
    c = np.linalg.solve(a, [x2**2 - x1**2 + y2**2 - y1**2, x3**2 - x1**2 + y3**2 - y1**2])
    return c, float(np.hypot(*(p[0] - c)))


def _spread(q, cx, cy):
    d = np.hypot(q[:, 0] - cx, q[:, 1] - cy)
    return float(np.median(np.abs(d - np.median(d))))


def _refine(q, cx, cy, reach, step):
    best = min((_spread(q, cx + dx, cy + dy), cx + dx, cy + dy)
               for dx in range(-reach, reach + 1, step) for dy in range(-reach, reach + 1, step))
    return best[1], best[2], best[0]


def fit_circle(x, y):
    """Kreis mit den meisten Punkten im Band ±20 km, danach Mittelpunkt mit der geringsten Streuung."""
    pts = np.column_stack([x, y])
    rng = np.random.default_rng(SEED)
    best = (0, None)
    for _ in range(RANSAC_TRIES):
        c = _circle3(pts[rng.choice(len(pts), 3, replace=False)])
        if c is None or not 80 < c[1] < 600:
            continue
        n = int((np.abs(np.hypot(pts[:, 0] - c[0][0], pts[:, 1] - c[0][1]) - c[1]) < RANSAC_BAND_KM).sum())
        if n > best[0]:
            best = (n, (c[0][0], c[0][1], c[1]))
    n_ransac, (cx, cy, r) = best
    inl = np.abs(np.hypot(pts[:, 0] - cx, pts[:, 1] - cy) - r) < 2 * RANSAC_BAND_KM
    cx, cy, spread = _refine(pts[inl], cx, cy, 60, 3)
    r = float(np.median(np.hypot(pts[inl, 0] - cx, pts[inl, 1] - cy)))
    return dict(cx=cx, cy=cy, r=r, spread=spread, n_ransac=n_ransac), inl


def run() -> dict:
    tr = transitions()
    x, y = _xy(tr["lat"], tr["lon"])
    fit, inl = fit_circle(x, y)
    lat, lon = _ll(fit["cx"], fit["cy"])
    pts = np.column_stack([x, y])

    days = []
    for i, (date, idx) in enumerate(tr[inl].groupby("date").groups.items()):
        q = pts[np.asarray(idx)]
        if len(q) < 80:
            continue
        dx, dy, _ = _refine(q, fit["cx"], fit["cy"], 90, 6)
        la, lo = _ll(dx, dy)
        days.append(dict(date=date, n=len(q), lat=round(float(la), 3), lon=round(float(lo), 3),
                         off_km=round(float(np.hypot(dx - fit["cx"], dy - fit["cy"])))))

    direction = {}
    for name, (la, lo) in CANDIDATES.items():
        x0, y0 = _xy(tr["lat_prev"], tr["lon_prev"], la, lo)
        x1, y1 = _xy(tr["lat_next"], tr["lon_next"], la, lo)
        r0, r1 = np.hypot(x0, y0), np.hypot(x1, y1)
        near = (r1 < 350) & (np.hypot(x1 - x0, y1 - y0) > 1)
        ein = near & (tr["kind"] == "ein").values
        direction[name] = dict(n=int(ein.sum()), toward=round(float(np.mean(r1[ein] < r0[ein])), 3))

    dist = np.hypot(x - fit["cx"], y - fit["cy"])
    ring = (dist > 60) & (dist < 450)
    alt = tr["alt_ft"].values
    bands = []
    for lo_ft, hi_ft in ALT_BANDS:
        m = ring & (alt >= lo_ft) & (alt < hi_ft)
        h_m = (lo_ft + hi_ft) / 2 * 0.3048
        bands.append(dict(alt_ft=[lo_ft, hi_ft], n=int(m.sum()), median_km=round(float(np.median(dist[m]))),
                          horizon_km=round(4.12 * (np.sqrt(30) + np.sqrt(h_m)))))
    rho = float(pd.Series(alt[ring]).corr(pd.Series(dist[ring]), method="spearman"))

    near_km = {name: round(float(np.hypot(*_xy(lat, lon, la, lo)))) for name, (la, lo) in CANDIDATES.items()}
    q16 = lambda v: base64.b64encode(np.round(np.asarray(v) * 100).astype("<i2").tobytes()).decode()
    day_idx = pd.Categorical(tr["date"]).codes.astype("u1")
    kind = (tr["kind"] == "aus").to_numpy().astype("u1")
    res = dict(
        box=BOX, dates=sorted(tr["date"].unique().tolist()), n=len(tr),
        n_ein=int((tr["kind"] == "ein").sum()), n_aus=int((tr["kind"] == "aus").sum()),
        center=dict(lat=round(float(lat), 3), lon=round(float(lon), 3)), radius_km=round(fit["r"]),
        spread_km=round(fit["spread"]), n_ransac=fit["n_ransac"], days=days,
        days_within_20km=sum(d["off_km"] <= 20 for d in days),
        direction=direction, altitude=bands, altitude_spearman=round(rho, 3), center_to_km=near_km,
        lat=q16(tr["lat"]), lon=q16(tr["lon"]),
        day=base64.b64encode(day_idx.tobytes()).decode(), kind=base64.b64encode(kind.tobytes()).decode(),
    )
    OUT.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    return res


if __name__ == "__main__":
    r = run()
    print(f"{r['n']} Übergänge ({r['n_ein']} ein, {r['n_aus']} aus)")
    print(f"Mittelpunkt {r['center']['lat']} N {r['center']['lon']} O, Radius {r['radius_km']} km, Streuung {r['spread_km']} km")
    print(f"Tage mit Mittelpunkt <= 20 km daneben: {r['days_within_20km']} von {len(r['days'])}")
    print("Auf den Ort zu beim Ausfall:", {k: v["toward"] for k, v in r["direction"].items()})
    print("Radius je Höhe:", [(b["alt_ft"], b["median_km"], b["horizon_km"]) for b in r["altitude"]], "Spearman", r["altitude_spearman"])
    print("Abstand Mittelpunkt zu:", r["center_to_km"])
