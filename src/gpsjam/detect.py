"""Anomalieerkennung auf den Gold-Aggregaten.

Zwei Detektoren mit unterschiedlichen Fragen:

* räumlich – "Wo wird gestört?"  Binomialtest je Zelle gegen die
  Hintergrundrate p0 schlechter Navigation (Avionik-Rauschen). Eine Zelle
  ist gestört, wenn ihr Anteil >= 10 % liegt, mindestens 2 Flugzeuge
  betroffen sind UND der Test p < 1e-4 ergibt; so lösen Zellen mit wenig
  Verkehr oder ein einzelnes defektes Flugzeug keinen Fehlalarm aus.
* zeitlich – "Wo ändert sich etwas?"  Robuster z-Score (Median/MAD) des
  Tagesanteils gegen den eigenen 14-Tage-Verlauf der Zelle; findet
  aufflammende Störungen, die nicht dauerhaft da sind.

Für stabile Zeitreihen wird jede gestörte Zelle zusätzlich einer festen
Großregion zugeordnet (die täglichen Zonen wechseln Form und Schwerpunkt).

Benachbarte gestörte Zellen werden pro Tag zu Störzonen zusammengefasst
(Zusammenhangskomponenten im H3-Gitter). Der mit gestörten Flugzeugen
gewichtete Schwerpunkt ist eine grobe Schätzung der Senderposition.

    python -m gpsjam.detect
"""
from __future__ import annotations

import json

import h3
import numpy as np
import pandas as pd
from scipy.stats import binom

from . import config as C

GAZETTEER = [
    ("Kaliningrad", 54.71, 20.51), ("Ostsee / Gotland", 57.4, 18.9), ("Danziger Bucht", 54.6, 18.9),
    ("Litauen", 55.2, 23.9), ("Lettland", 56.9, 24.6), ("Estland", 58.8, 25.5),
    ("Finnischer Meerbusen", 60.0, 27.0), ("St. Petersburg", 59.9, 30.3), ("Moskau", 55.75, 37.6), ("Ostfinnland", 61.8, 29.5), ("Kola / Murmansk", 68.6, 33.5),
    ("Nordost-Polen", 53.8, 22.6), ("Belarus", 53.7, 27.9), ("Ukraine", 49.0, 31.4),
    ("Moldau", 47.0, 28.8), ("Rumänien / Konstanza", 44.2, 28.6), ("Schwarzes Meer / Krim", 44.6, 33.5),
    ("Westliches Schwarzes Meer", 43.0, 31.0), ("Östliches Schwarzes Meer", 42.0, 40.5), ("Ostanatolien", 39.5, 42.0), ("Kaukasus", 42.3, 44.0), ("Ostmittelmeer / Zypern", 35.0, 33.4),
    ("Israel", 32.0, 35.0), ("Libanon / Westsyrien", 34.2, 36.0), ("Syrien", 35.0, 38.5), ("Nordirak", 36.5, 44.0),
    ("Ägypten / Sinai", 30.0, 32.5), ("Zentralanatolien", 39.5, 32.8), ("Persischer Golf", 28.0, 49.5), ("Libyen", 32.5, 15.0), ("Norwegen Finnmark", 70.0, 25.5),
]


# Feste Großregionen (lat_min, lat_max, lon_min, lon_max) – erste passende gewinnt
REGIONS = [
    ("Ostsee & Baltikum", 53.0, 61.5, 12.0, 32.0),
    ("Finnland & Kola", 61.5, 72.0, 18.0, 45.0),
    ("Schwarzes Meer", 40.5, 47.5, 27.0, 42.0),
    ("Ostmittelmeer & Levante", 29.0, 37.5, 26.0, 41.0),
    ("Kaukasus & Ostanatolien", 36.0, 44.5, 38.0, 50.0),
    ("Westrussland", 50.0, 61.5, 32.0, 50.0),
    ("Ostflanke Polen–Rumänien", 44.0, 53.0, 18.0, 32.0),
    ("Zentralanatolien", 36.0, 40.5, 28.0, 38.0),
    ("Persischer Golf", 23.0, 31.0, 45.0, 50.0),
]


def region_of(lat: float, lon: float) -> str:
    for name, la0, la1, lo0, lo1 in REGIONS:
        if la0 <= lat < la1 and lo0 <= lon < lo1:
            return name
    return "Sonstige"


def place_name(lat: float, lon: float, max_km: float = 450) -> str:
    best, best_d = None, max_km
    for name, la, lo in GAZETTEER:
        d = h3.great_circle_distance((lat, lon), (la, lo), unit="km")
        if d < best_d:
            best, best_d = name, d
    return best or f"{lat:.1f}° N, {lon:.1f}° O"


def background_rate(df: pd.DataFrame) -> float:
    """Gepoolter Anteil gestörter Flugzeuge in den 'ruhigen' 90 % der Zellen."""
    quiet = df[df["ratio"] <= df["ratio"].quantile(0.90)]
    return max(quiet["n_bad"].sum() / quiet["n_ac"].sum(), 1e-3)


def score(df: pd.DataFrame, p0: float) -> pd.DataFrame:
    df = df.copy()
    df["p_value"] = binom.sf(df["n_bad"] - 1, df["n_ac"], p0)
    df["level"] = np.select([df["ratio"] > C.RATIO_HIGH, df["ratio"] >= C.RATIO_LOW], [2, 1], 0)
    df["jammed"] = ((df["ratio"] >= C.RATIO_HIGH) & (df["n_bad"] >= C.MIN_BAD_AIRCRAFT)
                    & (df["p_value"] < C.BINOM_ALPHA))
    return df


def temporal_anomalies(day: pd.DataFrame, min_days: int = 7) -> pd.DataFrame:
    g = day.groupby("h3")["ratio"]
    med = g.transform("median")
    mad = g.transform(lambda s: (s - s.median()).abs().median())
    n = g.transform("count")
    day = day.assign(robust_z=(day["ratio"] - med) / (1.4826 * mad + 0.01))
    day["flare_up"] = (n >= min_days) & (day["robust_z"] > 3.5) & day["jammed"]
    return day


def zones(day: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for date, g in day[day["jammed"]].groupby("date"):
        cells = {h3.int_to_str(int(c)): r for c, r in zip(g["h3"], g.itertuples())}
        seen: set[str] = set()
        for start in cells:
            if start in seen:
                continue
            comp, stack = [], [start]
            seen.add(start)
            while stack:
                c = stack.pop()
                comp.append(c)
                for nb in h3.grid_disk(c, 1):
                    if nb in cells and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            recs = [cells[c] for c in comp]
            w = np.array([r.n_bad for r in recs], float)
            ll = np.array([h3.cell_to_latlng(c) for c in comp])
            lat, lon = (ll * w[:, None]).sum(0) / w.sum()
            rows.append(dict(
                date=date, zone=place_name(lat, lon), n_cells=len(comp),
                area_km2=round(sum(h3.cell_area(c, "km^2") for c in comp)),
                n_ac=int(sum(r.n_ac for r in recs)), n_bad=int(w.sum()),
                ratio=float(w.sum() / sum(r.n_ac for r in recs)),
                center_lat=float(lat), center_lon=float(lon),
            ))
    z = pd.DataFrame(rows)
    return z[z["n_cells"] >= 2].sort_values(["date", "area_km2"], ascending=[True, False])


def regions(day: pd.DataFrame) -> pd.DataFrame:
    """Gestörte Fläche und Anteil je Großregion und Tag."""
    cells = day["h3"].unique()
    meta = pd.DataFrame({"h3": cells})
    ll = [h3.cell_to_latlng(h3.int_to_str(int(c))) for c in cells]
    meta["region"] = [region_of(la, lo) for la, lo in ll]
    meta["area_km2"] = [h3.cell_area(h3.int_to_str(int(c)), "km^2") for c in cells]
    d = day.merge(meta, on="h3")
    j = d[d["jammed"]]
    out = j.groupby(["region", "date"]).agg(cells=("h3", "count"), area_km2=("area_km2", "sum"),
                                            n_ac=("n_ac", "sum"), n_bad=("n_bad", "sum")).reset_index()
    out["ratio"] = out["n_bad"] / out["n_ac"]
    return out


def run() -> dict:
    hour = pd.read_parquet(C.GOLD_DIR / "hex_hour.parquet")
    day = pd.read_parquet(C.GOLD_DIR / "hex_day.parquet")

    p0 = background_rate(day)
    day = temporal_anomalies(score(day, p0))
    hour = score(hour, background_rate(hour))
    z = zones(day)
    reg = regions(day)

    day.to_parquet(C.GOLD_DIR / "hex_day_scored.parquet", index=False)
    hour.to_parquet(C.GOLD_DIR / "hex_hour_scored.parquet", index=False)
    z.to_parquet(C.GOLD_DIR / "zones_day.parquet", index=False)
    reg.to_parquet(C.GOLD_DIR / "regions_day.parquet", index=False)

    summary = dict(
        days=int(day["date"].nunique()), p0=round(float(p0), 4),
        cell_days=len(day), cell_days_jammed=int(day["jammed"].sum()),
        flare_ups=int(day["flare_up"].sum()),
        mean_daily_jammed_area_km2=round(float(z.groupby("date")["area_km2"].sum().mean())) if len(z) else 0,
        regions=(reg.groupby("region").agg(days=("date", "nunique"), mean_area_km2=("area_km2", "mean"),
                                           max_area_km2=("area_km2", "max"), mean_ratio=("ratio", "mean"))
                    .sort_values("mean_area_km2", ascending=False)
                    .round(3).reset_index().to_dict("records")),
    )
    (C.GOLD_DIR / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
