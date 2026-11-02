"""Rendert die Störkarte als Bildfolge (1920x1080) für das Video.

Gleiche Daten und Farben wie die interaktive Karte (gpsjam.build_map), mit
Zwischenbildern zwischen den Stunden, damit die Zonen weich pulsieren.
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BG, SEA, LAND, COAST, COV = "#0D171B", "#0B161B", "#122128", "#3A5A66", "#1A3842"
INK, INK2, INK3, GRID = "#E6EEEE", "#A9BCBF", "#71898F", "#1E3138"
HEAT = ["#6E3420", "#B2482A", "#EC6A3E", "#FFB58A"]
STOPS = [2, 10, 30, 60]
VIEW = dict(lon0=-11.5, lon1=45.5, lat0=31.0, lat1=66.5)
SUB = 2  # Bilder pro Stunde
WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

_STATE: dict = {}


def _heat_rgba(v: np.ndarray) -> np.ndarray:
    from matplotlib.colors import to_rgba
    stops = np.array([to_rgba(c) for c in HEAT])
    out = np.empty((len(v), 4))
    for ch in range(4):
        out[:, ch] = np.interp(v, STOPS, stops[:, ch])
    return out


def _load() -> dict:
    import h3
    import pandas as pd

    from gpsjam import config as C
    from gpsjam.build_map import NO_DATA, _land, hourly_frames

    hour = pd.read_parquet(C.GOLD_DIR / "hex_hour.parquet")
    cells, hours, ratio, _ = hourly_frames(hour)
    strs = [h3.int_to_str(int(c)) for c in cells]
    polys = [np.array([(lo, la) for la, lo in h3.cell_to_boundary(s)]) for s in strs]
    area = np.array([h3.cell_area(s, "km^2") for s in strs])
    r = ratio.astype(float)
    r[ratio == NO_DATA] = np.nan
    jam = np.nan_to_num(r) >= 100 * C.RATIO_HIGH
    timeline = (jam * area[:, None]).sum(0) / 1000
    land = [np.array(x).reshape(-1, 2) for x in _land()]
    return dict(polys=polys, ratio=r, hours=hours, timeline=timeline, land=land)


def _init() -> None:
    _STATE.update(_load())


def _value(f: int) -> np.ndarray:
    """Werte für Bild f (mit Überblendung zwischen zwei Stunden, wie in der Web-Karte)."""
    r = _STATE["ratio"]
    h, t = divmod(f, SUB)
    t /= SUB
    a, b = r[:, h], r[:, min(h + 1, r.shape[1] - 1)]
    v = a + (b - a) * t
    v = np.where(np.isnan(a), np.where(t > 0.5, b, np.nan), v)
    v = np.where(np.isnan(b) & ~np.isnan(a), np.where(t < 0.5, a, np.nan), v)
    return v


def render(frames: list[int], out_dir: str) -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    s = _STATE
    n_frames = len(s["hours"]) * SUB
    plt.rcParams.update({"font.family": ["Helvetica Neue", "Arial", "sans-serif"]})
    fig = plt.figure(figsize=(19.2, 10.8), dpi=100, facecolor=BG)
    ax = fig.add_axes([0, 0.13, 1, 0.87], facecolor=SEA)
    ax.set_xlim(VIEW["lon0"], VIEW["lon1"])
    ax.set_ylim(VIEW["lat0"], VIEW["lat1"])
    # Seitenverhältnis halten, aber den Längengrad-Ausschnitt auf die volle Bildbreite erweitern
    ax.set_aspect(1 / np.cos(np.radians(49)), adjustable="datalim")
    ax.set_axis_off()
    ax.add_collection(PolyCollection(s["land"], facecolor=LAND, edgecolor="none", zorder=1))
    borders = PolyCollection(s["land"], facecolor="none", edgecolor=COAST, linewidth=0.7, zorder=6)
    ax.add_collection(borders)

    # Kopfzeile (auf halbtransparentem Grund, damit sie sich von den Waben abhebt)
    fig.patches.append(plt.Rectangle((0.022, 0.775), 0.445, 0.21, transform=fig.transFigure, figure=fig,
                                     facecolor=BG, alpha=0.82, edgecolor=GRID))
    fig.text(0.035, 0.945, "GPS-Störradar Europa", fontsize=34, fontweight="bold", color=INK)
    fig.text(0.035, 0.912, "Anteil der Flugzeuge mit gestörtem GPS (NIC < 7) je H3-Wabe, 3-h-Fenster",
             fontsize=17, color=INK2)
    clock = fig.text(0.035, 0.855, "", fontsize=30, color=INK, family="monospace")
    for i, (c, lab) in enumerate(zip([COV] + HEAT, ["< 2 %", "2 %", "10 %", "30 %", "60 %+"])):
        fig.patches.append(plt.Rectangle((0.035 + i * 0.045, 0.815), 0.042, 0.012, transform=fig.transFigure,
                                         color=c, figure=fig))
        fig.text(0.035 + i * 0.045, 0.795, lab, fontsize=13, color=INK3)

    # Zeitleiste
    tl = fig.add_axes([0.06, 0.035, 0.9, 0.075], facecolor=BG)
    x = np.arange(len(s["timeline"]))
    tl.fill_between(x, s["timeline"], color=HEAT[2], alpha=0.35, linewidth=0)
    tl.plot(x, s["timeline"], color=HEAT[2], linewidth=1.6)
    tl.set_xlim(0, len(x) - 1)
    tl.set_ylim(0, s["timeline"].max() * 1.1)
    for sp in tl.spines.values():
        sp.set_visible(False)
    tl.tick_params(colors=INK3, labelsize=12, length=0)
    days = [i for i, h in enumerate(s["hours"]) if h.hour == 0]
    tl.set_xticks(days, [f"{s['hours'][i]:%d.%m.}" for i in days])
    tl.set_yticks([])
    for d in days:
        tl.axvline(d, color=GRID, linewidth=1, zorder=0)
    tl.text(0, 1.18, "Gestörte Fläche (Waben ≥ 10 %)", transform=tl.transAxes, fontsize=14, color=INK2)
    head = tl.axvline(0, color=INK, linewidth=2)
    dot, = tl.plot([0], [0], "o", color=INK, markersize=6)

    dyn: list = []
    for f in frames:
        for art in dyn:
            art.remove()
        dyn = []
        v = _value(f)
        quiet = np.where(v < 2)[0]
        hot = np.where(v >= 2)[0]
        hot = hot[np.argsort(v[hot])]
        dyn.append(ax.add_collection(PolyCollection([s["polys"][i] for i in quiet], facecolor=COV,
                                                    edgecolor="none", zorder=2)))
        glow = hot[v[hot] >= 10]
        if len(glow):
            rgba = _heat_rgba(v[glow])
            rgba[:, 3] = 0.22
            dyn.append(ax.add_collection(PolyCollection([s["polys"][i] for i in glow], facecolor="none",
                                                        edgecolor=rgba, linewidth=9, zorder=3)))
        if len(hot):
            dyn.append(ax.add_collection(PolyCollection([s["polys"][i] for i in hot],
                                                        facecolor=_heat_rgba(v[hot]), edgecolor="none", zorder=4)))
        h = s["hours"][f // SUB]
        clock.set_text(f"{WD[h.weekday()]}, {h:%d.%m.%Y}  {h:%H}:00 UTC")
        xpos = f / SUB
        head.set_xdata([xpos, xpos])
        dot.set_data([xpos], [np.interp(xpos, x, s["timeline"])])
        fig.savefig(Path(out_dir) / f"{f:05d}.png", facecolor=BG)
    plt.close(fig)
    return len(frames)


def render_all(out_dir: Path, workers: int = 6) -> int:
    """Rendert alle Bilder parallel; gibt die Bildzahl zurück."""
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(_load()["hours"]) * SUB
    chunks = [list(range(i, n, workers)) for i in range(workers)]
    with ProcessPoolExecutor(workers, initializer=_init) as ex:
        list(ex.map(render, chunks, [str(out_dir)] * workers))
    return n


if __name__ == "__main__":
    out = Path(sys.argv[1])
    if len(sys.argv) > 2:  # Probebild: python map_frames.py <dir> <frame>
        _init()
        render([int(sys.argv[2])], str(out))
    else:
        print(render_all(out), "Bilder")
