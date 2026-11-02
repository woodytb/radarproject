"""Diagramme für die Präsentation (PNG, für dunkle Folien gestaltet).

    python -m gpsjam.charts
"""
from __future__ import annotations

import json

import h3
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow.dataset as ds  # noqa: E402

from . import config as C  # noqa: E402

BG, INK, INK2, INK3, GRID = "#0d171b", "#e6eeee", "#a9bcbf", "#71898f", "#1e3138"
HOT, COOL = "#ec6a3e", "#5f8f9c"
OUT = C.OUTPUT_DIR / "charts"

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK3, "ytick.color": INK3,
    "text.color": INK, "font.family": ["Helvetica Neue", "Arial", "sans-serif"],
    "font.size": 15, "axes.titlesize": 17, "axes.titleweight": "600", "axes.titlecolor": INK,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.axisbelow": True,
})


def regions_small_multiples(reg: pd.DataFrame, n: int = 6) -> None:
    top = reg.groupby("region")["area_km2"].mean().sort_values(ascending=False).head(n).index
    dates = pd.date_range(reg["date"].min(), reg["date"].max(), freq="D")
    fig, axes = plt.subplots(2, 3, figsize=(16, 7.2), sharex=True, sharey=True)
    for ax, name in zip(axes.flat, top):
        z = reg[reg["region"] == name].set_index("date")["area_km2"].reindex(dates, fill_value=0) / 1000
        ax.bar(z.index, z.values, width=0.75, color=HOT, edgecolor=BG, linewidth=1)
        ax.set_title(name, loc="left", pad=8)
        ax.grid(axis="x", visible=False)
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m."))
    for ax in axes[:, 0]:
        ax.set_ylabel("Tsd. km² gestört")
    fig.tight_layout(h_pad=2.2, w_pad=1.6)
    fig.savefig(OUT / "regionen_verlauf.png", dpi=150)
    plt.close(fig)


def hour_profile(hour: pd.DataFrame, day: pd.DataFrame) -> None:
    """Anteil gestörter Flugzeuge je Tagesstunde – nur in Zellen, die an dem Tag als gestört gelten."""
    jam = day.loc[day["jammed"], ["h3", "date"]]
    h = hour.assign(date=hour["hour"].dt.floor("D")).merge(jam, on=["h3", "date"])
    prof = h.groupby(h["hour"].dt.hour)[["n_bad", "n_ac"]].sum()
    ratio = 100 * prof["n_bad"] / prof["n_ac"]
    fig, ax = plt.subplots(figsize=(10, 5.2))
    ax.fill_between(ratio.index, ratio.values, color=HOT, alpha=0.25, linewidth=0)
    ax.plot(ratio.index, ratio.values, color=HOT, linewidth=2.5)
    i = int(ratio.idxmax())
    ax.scatter([i], [ratio[i]], color=HOT, s=60, zorder=3, edgecolor=BG, linewidth=2)
    ax.annotate(f"{ratio[i]:.0f} % um {i:02d} Uhr", (i, ratio[i]), xytext=(10, 10), textcoords="offset points", color=INK)
    ax.set_xlim(0, 23)
    ax.set_ylim(0, max(ratio.max() * 1.25, 1))
    ax.set_xticks(range(0, 24, 3), [f"{x:02d}" for x in range(0, 24, 3)])
    ax.set_xlabel("Stunde (UTC)")
    ax.set_ylabel("% gestörte Flugzeuge")
    fig.tight_layout()
    fig.savefig(OUT / "tagesgang.png", dpi=150)
    plt.close(fig)
    return ratio


def nic_distribution(day: pd.DataFrame) -> dict:
    """NIC-Verteilung in gestörten vs. ungestörten Zellen (alle Tage, gestreamt)."""
    jam = day.loc[day["jammed"], ["h3", "date"]].assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d"), inside=True)
    counts = {True: np.zeros(12, np.int64), False: np.zeros(12, np.int64)}
    dataset = ds.dataset(C.SILVER_DIR / "positions", format="parquet", partitioning="hive")
    parent: dict[int, int] = {}
    for batch in dataset.to_batches(columns=["nic", "h3_r5", "date", "afterglow"], batch_size=2_000_000):
        b = batch.to_pandas()
        b = b[~b["afterglow"]]
        for c in b["h3_r5"].unique():
            if c not in parent:
                parent[c] = h3.str_to_int(h3.cell_to_parent(h3.int_to_str(int(c)), C.H3_AGG_RES))
        k = pd.DataFrame({"h3": b["h3_r5"].map(parent).astype("uint64").to_numpy(),
                          "date": b["date"].astype(str).to_numpy()})
        inside = k.merge(jam, on=["h3", "date"], how="left")["inside"].fillna(False).to_numpy(bool)
        for flag in (True, False):
            nic = b["nic"].to_numpy()[inside == flag]
            counts[flag] += np.bincount(nic.clip(0, 11), minlength=12)[:12]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5.2), sharey=True)
    for ax, flag, title, color in ((axes[0], False, "Ungestörte Zellen", COOL), (axes[1], True, "Gestörte Zellen", HOT)):
        share = 100 * counts[flag] / counts[flag].sum()
        ax.bar(range(12), share, color=[color if i >= C.NIC_BAD_BELOW else HOT if flag else INK3 for i in range(12)],
               edgecolor=BG, linewidth=1)
        ax.axvline(C.NIC_BAD_BELOW - 0.5, color=INK2, linestyle="--", linewidth=1)
        ax.set_title(title, loc="left")
        ax.set_xticks(range(12))
        ax.set_xlabel("NIC")
        ax.grid(axis="x", visible=False)
        ax.text(C.NIC_BAD_BELOW - 0.7, ax.get_ylim()[1] * 0.5, "schlecht ←", ha="right", color=INK2, fontsize=13)
        ax.text(C.NIC_BAD_BELOW - 0.3, ax.get_ylim()[1] * 0.5, "→ gut", ha="left", color=INK2, fontsize=13)
    axes[0].set_ylabel("% der Meldungen")
    fig.tight_layout(w_pad=2)
    fig.savefig(OUT / "nic_verteilung.png", dpi=150)
    plt.close(fig)
    return {k: v.tolist() for k, v in (("inside", counts[True]), ("outside", counts[False]))}


def afterglow_comparison(day: str = "2026-10-22", hours: tuple[int, int] = (2, 6)) -> dict:
    """Karte vorher/nachher: naive Auswertung vs. Nachwirkungs-Filter + 2-Flugzeug-Regel."""
    from matplotlib.collections import PolyCollection
    from matplotlib.colors import LinearSegmentedColormap

    from .build_map import _land

    df = pd.read_parquet(C.SILVER_DIR / "positions" / f"date={day}",
                         columns=["icao", "ts", "nic", "version", "h3_r5", "afterglow"])
    df = df[(df["version"].fillna(0) >= 1) & df["ts"].dt.hour.between(hours[0], hours[1] - 1)]
    healthy = df.groupby("icao")["nic"].transform("max") >= C.NIC_BAD_BELOW
    df = df[healthy]
    parent = {c: h3.str_to_int(h3.cell_to_parent(h3.int_to_str(int(c)), C.H3_AGG_RES)) for c in df["h3_r5"].unique()}
    df = df.assign(h3=df["h3_r5"].map(parent), bad=df["nic"] < C.NIC_BAD_BELOW)

    def cells(d: pd.DataFrame, min_bad: int) -> pd.DataFrame:
        a = d.groupby(["h3", "icao"])["bad"].mean().reset_index()
        a["bad_ac"] = a["bad"] >= C.BAD_SHARE_PER_AIRCRAFT
        g = a.groupby("h3").agg(n_ac=("icao", "count"), n_bad=("bad_ac", "sum")).reset_index()
        g = g[g["n_ac"] >= C.MIN_AIRCRAFT_HOUR]
        g["ratio"] = np.where(g["n_bad"] >= min_bad, g["n_bad"] / g["n_ac"], 0.0)
        return g

    naive, clean = cells(df, 1), cells(df[~df["afterglow"]], C.MIN_BAD_AIRCRAFT)
    cmap = LinearSegmentedColormap.from_list("heat", ["#6e3420", "#b2482a", "#ec6a3e", "#ffb58a"])
    land = [np.array(r).reshape(-1, 2) for r in _land()]

    fig, axes = plt.subplots(1, 2, figsize=(16, 8.2))
    for ax, g, title in ((axes[0], naive, "Naiv: jede schlechte Meldung zählt"),
                         (axes[1], clean, "Bereinigt: ohne Nachwirkung, ≥ 2 Flugzeuge")):
        ax.add_collection(PolyCollection(land, facecolor="#122128", edgecolor="#3a5a66", linewidth=0.5))
        polys = [np.array([(lo, la) for la, lo in h3.cell_to_boundary(h3.int_to_str(int(c)))]) for c in g["h3"]]
        r = g["ratio"].to_numpy()
        colors = [("#1a3842" if v < 0.02 else cmap(min(1, (v - 0.02) / 0.58))) for v in r]
        ax.add_collection(PolyCollection(polys, facecolor=colors, edgecolor="none"))
        ax.set_xlim(-11, 42)
        ax.set_ylim(34, 64)
        ax.set_aspect(1 / np.cos(np.radians(50)))
        ax.set_title(title, loc="left")
        ax.set_facecolor("#0b161b")
        ax.grid(False)
        ax.set_xticks([])
        ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
    fig.suptitle(f"{day}, {hours[0]:02d}–{hours[1]:02d} Uhr UTC", x=0.01, ha="left", color=INK2, fontsize=14)
    fig.tight_layout()
    fig.savefig(OUT / "nachwirkung_vorher_nachher.png", dpi=150)
    plt.close(fig)
    share = lambda g: float((g["ratio"] >= C.RATIO_HIGH).sum())  # noqa: E731
    return dict(cells_red_naive=share(naive), cells_red_clean=share(clean))


def filter_effect(hours: tuple[int, int] = (2, 6)) -> dict:
    """Rote Zellen je Nacht: naiv, nur Nachwirkungs-Filter, nur 2-Flugzeug-Regel, beide."""
    parent: dict[int, int] = {}

    def red(d: pd.DataFrame, min_bad: int) -> int:
        a = d.groupby(["h3", "icao"])["bad"].mean().reset_index()
        a["bad_ac"] = a["bad"] >= C.BAD_SHARE_PER_AIRCRAFT
        g = a.groupby("h3").agg(n=("icao", "count"), k=("bad_ac", "sum"))
        g = g[g["n"] >= C.MIN_AIRCRAFT_HOUR]
        return int(((g["k"] / g["n"] >= C.RATIO_HIGH) & (g["k"] >= min_bad)).sum())

    totals = dict(naiv=0, nur_nachwirkung=0, nur_zwei_flugzeuge=0, beide=0)
    days = sorted((C.SILVER_DIR / "positions").glob("date=*"))
    for path in days:
        df = pd.read_parquet(path, columns=["icao", "ts", "nic", "version", "h3_r5", "afterglow"])
        df = df[(df["version"].fillna(0) >= 1) & df["ts"].dt.hour.between(hours[0], hours[1] - 1)]
        df = df[df.groupby("icao")["nic"].transform("max") >= C.NIC_BAD_BELOW]
        for c in df["h3_r5"].unique():
            parent.setdefault(int(c), h3.str_to_int(h3.cell_to_parent(h3.int_to_str(int(c)), C.H3_AGG_RES)))
        df = df.assign(h3=df["h3_r5"].map(lambda c: parent[int(c)]), bad=df["nic"] < C.NIC_BAD_BELOW)
        clean = df[~df["afterglow"]]
        totals["naiv"] += red(df, 1)
        totals["nur_nachwirkung"] += red(clean, 1)
        totals["nur_zwei_flugzeuge"] += red(df, C.MIN_BAD_AIRCRAFT)
        totals["beide"] += red(clean, C.MIN_BAD_AIRCRAFT)
    return dict(nights=len(days), hours=list(hours), red_cells=totals,
                reduction={k: round(1 - v / totals["naiv"], 4) for k, v in totals.items() if k != "naiv"})


def benchmark_chart() -> None:
    """Beschleunigung gegenüber 1 Kern (gemeinsame Basis, daher nur eine Achse)."""
    path = C.GOLD_DIR / "benchmark.json"
    if not path.exists():
        return
    rows = pd.DataFrame(json.loads(path.read_text()))
    # Farben mit dem Palette-Validator geprüft (dunkler Hintergrund, Farbfehlsichtigkeit)
    series = [("Dask Silver", "Einlesen (Dask)", "#D9582B", "o"), ("Spark Gold", "Aggregation (Spark)", "#8880E0", "s"),
              ("Dask Gold", "Aggregation (Dask)", "#23A58F", "^")]
    fig, ax = plt.subplots(figsize=(10, 6.2))
    cores = sorted(rows["cores"].unique())
    ax.plot(cores, cores, color=INK3, linewidth=1.5, linestyle="--")
    ax.text(cores[-1] - 0.1, cores[-1] - 0.1, "ideal", color=INK2, ha="right", va="bottom", fontsize=15)
    for stage, label, color, marker in series:
        d = rows[rows["stage"] == stage].sort_values("cores")
        speedup = d["seconds"].iloc[0] / d["seconds"]
        ax.plot(d["cores"], speedup, color=color, linewidth=2.5, marker=marker, markersize=9,
                markeredgecolor=BG, markeredgewidth=2, label=label)
        ax.annotate(f"{speedup.iloc[-1]:.1f}×".replace(".", ","), (d["cores"].iloc[-1], speedup.iloc[-1]), xytext=(10, -5),
                    textcoords="offset points", color=INK, fontsize=15)
    ax.set_xticks(cores)
    ax.set_xlim(0.7, cores[-1] + 0.9)
    ax.set_ylim(0, cores[-1] + 0.6)
    ax.set_xlabel("Kerne")
    ax.set_ylabel("Beschleunigung gegenüber 1 Kern")
    leg = ax.legend(loc="upper left", frameon=False, fontsize=15)
    for t in leg.get_texts():
        t.set_color(INK)
    fig.tight_layout()
    fig.savefig(OUT / "skalierung.png", dpi=150)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    hour = pd.read_parquet(C.GOLD_DIR / "hex_hour_scored.parquet")
    day = pd.read_parquet(C.GOLD_DIR / "hex_day_scored.parquet")
    reg = pd.read_parquet(C.GOLD_DIR / "regions_day.parquet")
    regions_small_multiples(reg)
    prof = hour_profile(hour, day)
    nic = nic_distribution(day)
    glow = afterglow_comparison()
    facts = dict(hour_profile=prof.round(2).to_dict(), nic_counts=nic, afterglow=glow, filter_effect=filter_effect())
    (C.GOLD_DIR / "chart_facts.json").write_text(json.dumps(facts, indent=2))
    benchmark_chart()
    print(f"Diagramme -> {OUT}")


if __name__ == "__main__":
    main()
