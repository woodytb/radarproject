"""Abgleich mit gpsjam.org, einem zweiten Lagebild aus anderen Empfängernetzen.

gpsjam.org veröffentlicht je Tag und H3-Zelle (Auflösung 4) die Zahl der Flugzeuge
mit guter und schlechter Navigationsgenauigkeit, aus airplanes.live und ADS-B
Exchange. Das ist keine Wahrheit (auch dort gibt es keine Senderpositionen), aber
ein eigener Messaufbau: andere Empfängernetze, eigener Schwellenwert, keine
Nachwirkungs-Korrektur. Die Dateien bleiben lokal und werden nicht weitergegeben.

    python -m gpsjam.validate_gpsjam
"""
from __future__ import annotations

import io
import json

import h3
import numpy as np
import pandas as pd
import requests
from scipy.stats import spearmanr

from . import config as C
from .detect import background_rate, region_of, score

URL = "https://gpsjam.org/data/{day}-h3_4.csv"
CACHE = C.ROOT / "data" / "ref" / "gpsjam"
CENTRAL = dict(lat0=46.0, lat1=55.0, lon0=3.0, lon1=16.0)


def load_day(day: str) -> pd.DataFrame:
    path = CACHE / f"{day}.csv"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        r = requests.get(URL.format(day=day), timeout=60)
        r.raise_for_status()
        path.write_text(r.text)
    df = pd.read_csv(io.StringIO(path.read_text()))
    df["h3"] = df["hex"].map(h3.str_to_int).astype("uint64")
    return df.drop(columns="hex")


def compare_cells(ours: pd.DataFrame, theirs: pd.DataFrame) -> pd.DataFrame:
    """Nur Zellen-Tage, die beide Quellen mit mindestens MIN_AIRCRAFT_DAY Flugzeugen bewerten."""
    m = ours.merge(theirs, on=["h3", "date"], how="inner")
    m["g_total"] = m["count_good_aircraft"] + m["count_bad_aircraft"]
    m = m[m["g_total"] >= C.MIN_AIRCRAFT_DAY].copy()
    # Formel von gpsjam.org: (bad - 1) / (good + bad), Rot ab 10 %
    m["g_ratio"] = (m["count_bad_aircraft"] - 1).clip(lower=0) / m["g_total"]
    m["g_red"] = m["g_ratio"] > C.RATIO_HIGH
    return m


def kappa(a: np.ndarray, b: np.ndarray) -> float:
    po = (a == b).mean()
    pe = a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean())
    return float((po - pe) / (1 - pe))


def metrics(m: pd.DataFrame) -> dict:
    a, b = m["jammed"].to_numpy(bool), m["g_red"].to_numpy(bool)
    both = int((a & b).sum())
    rho = spearmanr(m["ratio"], m["g_ratio"]).statistic
    return dict(cell_days=len(m), both=both, only_ours=int((a & ~b).sum()), only_gpsjam=int((~a & b).sum()),
                neither=int((~a & ~b).sum()), share_ours_confirmed=round(both / max(a.sum(), 1), 4),
                share_gpsjam_found=round(both / max(b.sum(), 1), 4), kappa=round(kappa(a, b), 4), spearman=round(float(rho), 4))


def neighbour_tolerant(m: pd.DataFrame) -> float:
    """Anteil unserer gestörten Zellen-Tage mit gpsjam-Rot in derselben oder einer Nachbarzelle."""
    red = {(d, c) for d, c in zip(m.loc[m["g_red"], "date"], m.loc[m["g_red"], "h3"])}
    hits = total = 0
    for d, c in zip(m.loc[m["jammed"], "date"], m.loc[m["jammed"], "h3"]):
        total += 1
        hits += any((d, h3.str_to_int(n)) in red for n in h3.grid_disk(h3.int_to_str(int(c)), 1))
    return round(hits / max(total, 1), 4)


def rule_comparison(m: pd.DataFrame) -> dict:
    """Woran liegt der Unterschied? Schwelle, Rechenregel oder Indikator?"""
    g = m["g_red"].to_numpy(bool)

    def score_of(a: np.ndarray) -> dict:
        return dict(cell_days=int(a.sum()), confirmed=round(float((a & g).sum() / max(a.sum(), 1)), 4),
                    found=round(float((a & g).sum() / max(g.sum(), 1)), 4), kappa=round(kappa(a, g), 4))

    jam = m["jammed"].to_numpy(bool)
    out = dict(standard=score_of(jam))
    for t in (0.15, 0.20, 0.25, 0.30):
        out[f"anteil_{int(t * 100)}"] = score_of(jam & (m["ratio"] >= t).to_numpy())
    out["gpsjam_formel_auf_unsere_zahlen"] = score_of((((m["n_bad"] - 1).clip(lower=0) / m["n_ac"]) > C.RATIO_HIGH).to_numpy())
    return out


def nacp_variant(day: pd.DataFrame, theirs: pd.DataFrame, nacp_bad_below: int = 8) -> dict:
    """Dieselbe Auswertung, aber mit Navigationsgenauigkeit (NACp) statt Integrität (NIC) als Indikator."""
    parent: dict[int, int] = {}
    frames = []
    for path in sorted((C.SILVER_DIR / "positions").glob("date=*")):
        df = pd.read_parquet(path, columns=["icao", "nic", "nac_p", "version", "h3_r5", "afterglow"])
        df = df[(df["version"].fillna(0) >= 1) & ~df["afterglow"] & df["nac_p"].notna()]
        df = df[df.groupby("icao")["nac_p"].transform("max") >= nacp_bad_below]
        for c in df["h3_r5"].unique():
            parent.setdefault(int(c), h3.str_to_int(h3.cell_to_parent(h3.int_to_str(int(c)), C.H3_AGG_RES)))
        df = df.assign(h3=df["h3_r5"].map(lambda c: parent[int(c)]), bad=df["nac_p"] < nacp_bad_below)
        a = df.groupby(["h3", "icao"])["bad"].mean().reset_index()
        a["bad_ac"] = a["bad"] >= C.BAD_SHARE_PER_AIRCRAFT
        g = a.groupby("h3").agg(n_ac=("icao", "count"), n_bad=("bad_ac", "sum")).reset_index()
        g = g[g["n_ac"] >= C.MIN_AIRCRAFT_DAY]
        frames.append(g.assign(date=path.name.split("=")[1], ratio=g["n_bad"] / g["n_ac"]))
    cells = pd.concat(frames, ignore_index=True)
    cells = score(cells, background_rate(cells))
    m = compare_cells(cells, theirs)
    r = metrics(m)
    r["nacp_bad_below"] = nacp_bad_below
    r["ratio_gpsjam_to_ours_in_jammed"] = round(float(((m["count_bad_aircraft"] / m["g_total"]) / m["ratio"].replace(0, np.nan))[m["jammed"]].median()), 3)
    return r


def agreement_map(m: pd.DataFrame) -> dict:
    """Karte: Wo sind sich beide einig? Eine Zelle zählt, wenn sie an mind. der Hälfte ihrer Tage markiert ist."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from matplotlib.patches import Patch

    from .build_map import _land

    g = m.groupby("h3").agg(ours=("jammed", "mean"), them=("g_red", "mean"), yellow=("g_ratio", lambda s: (s >= 0.02).mean())).reset_index()
    both = (g["ours"] >= 0.5) & (g["them"] >= 0.5)
    ours = (g["ours"] >= 0.5) & ~both
    them = (g["them"] >= 0.5) & ~both
    # Farben mit dem Palette-Validator geprüft (dunkler Hintergrund, Farbfehlsichtigkeit)
    style = [("Beide: gestört", both, "#D9582B"), ("Nur wir", ours, "#8880E0"), ("Nur gpsjam.org", them, "#23A58F")]
    bg = "#0D171B"
    fig, ax = plt.subplots(figsize=(11, 8.2), facecolor=bg)
    ax.set_facecolor("#0B161B")
    ax.add_collection(PolyCollection([np.array(r).reshape(-1, 2) for r in _land()], facecolor="#122128", edgecolor="#3A5A66", linewidth=0.5))
    rest = g[~(both | ours | them)]
    poly = lambda d: [np.array([(lo, la) for la, lo in h3.cell_to_boundary(h3.int_to_str(int(c)))]) for c in d["h3"]]  # noqa: E731
    ax.add_collection(PolyCollection(poly(rest), facecolor="#1A3842", edgecolor="none"))
    handles, counts = [], {}
    for name, mask, color in style:
        ax.add_collection(PolyCollection(poly(g[mask]), facecolor=color, edgecolor="#0D171B", linewidth=0.5))
        counts[name] = int(mask.sum())
        handles.append(Patch(facecolor=color, label=f"{name} ({counts[name]} Zellen)"))
    handles.append(Patch(facecolor="#1A3842", label="Verkehr, von keiner Seite markiert"))
    ax.set_xlim(-11, 50)
    ax.set_ylim(27, 66)
    ax.set_aspect(1 / np.cos(np.radians(47)), adjustable="datalim")
    ax.axis("off")
    leg = ax.legend(handles=handles, loc="lower left", frameon=True, fontsize=15, facecolor=bg, edgecolor="#1E3138", labelcolor="#E6EEEE")
    leg.get_frame().set_alpha(0.9)
    fig.tight_layout(pad=0.3)
    C.OUTPUT_DIR.joinpath("charts").mkdir(parents=True, exist_ok=True)
    fig.savefig(C.OUTPUT_DIR / "charts" / "validierung_gpsjam.png", dpi=150, facecolor=bg)
    plt.close(fig)
    return counts


def run() -> dict:
    day = pd.read_parquet(C.GOLD_DIR / "hex_day_scored.parquet")
    day["date"] = pd.to_datetime(day["date"], utc=True).dt.strftime("%Y-%m-%d")
    frames = [load_day(d).assign(date=d) for d in sorted(day["date"].unique())]
    theirs = pd.concat(frames, ignore_index=True)
    m = compare_cells(day, theirs)

    ll = [h3.cell_to_latlng(h3.int_to_str(int(c))) for c in m["h3"]]
    m["lat"], m["lon"] = [x for x, _ in ll], [y for _, y in ll]
    m["region"] = [region_of(a, b) for a, b in ll]
    b = CENTRAL
    m["central"] = m["lat"].between(b["lat0"], b["lat1"]) & m["lon"].between(b["lon0"], b["lon1"])

    area = {c: h3.cell_area(h3.int_to_str(int(c)), "km^2") for c in m["h3"].unique()}
    m["area"] = m["h3"].map(area)
    regions = []
    for name, g in m.groupby("region"):
        ours_a = g.loc[g["jammed"]].groupby("date")["area"].sum()
        them_a = g.loc[g["g_red"]].groupby("date")["area"].sum()
        regions.append(dict(region=name, ours_km2=round(ours_a.sum() / m["date"].nunique()),
                            gpsjam_km2=round(them_a.sum() / m["date"].nunique()), **metrics(g)))
    regions.sort(key=lambda r: -r["ours_km2"])

    # Blick auf den möglichen Restfehler: Was sagt gpsjam zu unseren mitteleuropäischen Zellen?
    cen = m[m["central"]]
    only = m[m["jammed"] & ~m["g_red"]]
    yellow = float((only["g_ratio"] >= C.RATIO_LOW).mean())
    area_ours = m.loc[m["jammed"]].groupby("date")["area"].sum().mean()
    area_them = m.loc[m["g_red"]].groupby("date")["area"].sum().mean()
    daily = pd.DataFrame({"ours": m.loc[m["jammed"]].groupby("date")["area"].sum(), "them": m.loc[m["g_red"]].groupby("date")["area"].sum()})
    ov = metrics(m)
    result = dict(
        source="gpsjam.org", days=int(m["date"].nunique()), overall=ov, neighbour_tolerant=neighbour_tolerant(m),
        only_ours_yellow_at_gpsjam=round(yellow, 4),
        ours_red_or_yellow_at_gpsjam=round((ov["both"] + yellow * ov["only_ours"]) / (ov["both"] + ov["only_ours"]), 4),
        median_ratio_gpsjam_to_ours_in_jammed=round(float(((m["count_bad_aircraft"] / m["g_total"]) / m["ratio"].replace(0, np.nan))[m["jammed"]].median()), 3),
        daily_area_km2=dict(ours=round(float(area_ours)), gpsjam=round(float(area_them)), spearman=round(float(spearmanr(daily["ours"], daily["them"]).statistic), 3)),
        region_rank_spearman=round(float(spearmanr([r["ours_km2"] for r in regions], [r["gpsjam_km2"] for r in regions]).statistic), 3),
        rules=rule_comparison(m), nacp_indicator=nacp_variant(day, theirs), map_cells=agreement_map(m),
        regions=regions, central_europe=dict(cell_days=len(cen), ours=int(cen["jammed"].sum()), gpsjam=int(cen["g_red"].sum()), both=int((cen["jammed"] & cen["g_red"]).sum())),
    )
    (C.GOLD_DIR / "validation_gpsjam.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    m[["h3", "date", "jammed", "g_red", "ratio", "g_ratio", "lat", "lon"]].to_parquet(C.GOLD_DIR / "validation_cells.parquet", index=False)
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
