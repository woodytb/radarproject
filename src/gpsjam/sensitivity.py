"""Empfindlichkeitstest: Bleiben die Störzonen stabil, wenn man die Schwellen verändert?

Jeder Parameter wird einzeln um den Standardwert variiert, die anderen bleiben fest.
Weil Nachwirkung von der NIC-Schwelle und dem Radius abhängt und der Störbeginn auch
außerhalb Europas liegen kann, werden die Rohspuren einmal neu gelesen und für jede
NIC-Schwelle die Entfernung zum Störbeginn gespeichert (data/sensitivity/).

    python -m gpsjam.sensitivity prepare [--workers 8]
    python -m gpsjam.sensitivity run
"""
from __future__ import annotations

import argparse
import json
import math
import time
from contextlib import contextmanager
from pathlib import Path

import dask.bag as db
import h3
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.stats import spearmanr

from . import config as C
from .detect import background_rate, region_of, score
from .parse_traces import FILES_PER_TASK, _km, _load

OUT = C.ROOT / "data" / "sensitivity"
NIC_VALUES = (5, 6, 7, 8)
BASE = dict(nic=C.NIC_BAD_BELOW, radius=C.AFTERGLOW_MAX_KM, share=C.BAD_SHARE_PER_AIRCRAFT,
            ratio=C.RATIO_HIGH, min_bad=C.MIN_BAD_AIRCRAFT, alpha=C.BINOM_ALPHA)
VARIANTS = [("Standard", {})] + [
    (f"{label} {shown}", {key: value}) for label, key, values in (
        ("NIC-Schwelle <", "nic", (5, 6, 8)),
        ("Nachwirkung ab km", "radius", (300, 800, math.inf)),
        ("Anteil Meldungen je Flugzeug ≥", "share", (0.25, 0.75)),
        ("Anteil Flugzeuge je Wabe ≥", "ratio", (0.05, 0.20)),
        ("Mindestens betroffene Flugzeuge", "min_bad", (1, 3)),
        ("Signifikanz α", "alpha", (1e-3, 1e-6)),
    ) for value in values for shown in [("aus" if value == math.inf else value)]
]


def onset_distances(trace: list, t0: float) -> list[tuple]:
    """Wie parse_traces.quality_points, aber Entfernung zum Störbeginn je NIC-Schwelle."""
    state = {t: dict(onset=None, prev_bad=False) for t in NIC_VALUES}
    prev_t, out = None, []
    for pt in trace:
        det = pt[8]
        if not isinstance(det, dict) or pt[9] != "adsb_icao" or det.get("nic") is None:
            continue
        alt = pt[3]
        if alt == "ground" or alt is None or alt < C.MIN_ALT_FT:
            continue
        t, lat, lon, nic = t0 + pt[0], pt[1], pt[2], det["nic"]
        km = []
        for thr in NIC_VALUES:
            s, bad = state[thr], nic < thr
            if bad and (not s["prev_bad"] or t - prev_t > C.RUN_GAP_S):
                s["onset"] = (lat, lon)
            km.append(_km(s["onset"][0], s["onset"][1], lat, lon) if bad else 0.0)
            s["prev_bad"] = bad
        out.append((lat, lon, nic, det.get("version"), km))
        prev_t = t
    return out


def _prepare_batch(args: tuple[int, list[str], str]) -> int:
    idx, paths, out_dir = args
    b = C.BBOX
    rows: dict[str, list] = {k: [] for k in ("icao", "h3", "nic", "version", *[f"km{t}" for t in NIC_VALUES])}
    for p in paths:
        try:
            d = _load(Path(p))
        except (OSError, ValueError):
            continue
        for lat, lon, nic, ver, km in onset_distances(d.get("trace") or [], d["timestamp"]):
            if not (b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]):
                continue
            rows["icao"].append(d["icao"])
            rows["h3"].append(h3.str_to_int(h3.latlng_to_cell(lat, lon, C.H3_AGG_RES)))
            rows["nic"].append(nic)
            rows["version"].append(ver)
            for thr, v in zip(NIC_VALUES, km):
                rows[f"km{thr}"].append(round(v, 1))
    if rows["icao"]:
        table = pa.table({"icao": pa.array(rows["icao"], pa.string()), "h3": pa.array(rows["h3"], pa.uint64()),
                          "nic": pa.array(rows["nic"], pa.int8()), "version": pa.array(rows["version"], pa.int8()),
                          **{f"km{t}": pa.array(rows[f"km{t}"], pa.float32()) for t in NIC_VALUES}})
        pq.write_table(table, Path(out_dir) / f"part-{idx:05d}.parquet", compression="zstd")
    return len(rows["icao"])


def prepare(workers: int) -> None:
    for pos in sorted((C.SILVER_DIR / "positions").glob("date=*")):
        day = pos.name.split("=")[1]
        out = OUT / f"date={day}"
        if (out / "_SUCCESS").exists():
            continue
        out.mkdir(parents=True, exist_ok=True)
        files = sorted(str(p) for p in (C.RAW_DIR / day).glob("trace_full_*.json"))
        batches = [(i, files[i:i + FILES_PER_TASK], str(out)) for i in range(0, len(files), FILES_PER_TASK)]
        t = time.time()
        n = sum(db.from_sequence(batches, npartitions=len(batches)).map(_prepare_batch).compute(scheduler="processes", num_workers=workers))
        (out / "_SUCCESS").write_text(str(n))
        print(f"{day}: {n:,} Punkte in {time.time() - t:.0f}s", flush=True)


def _day_cells(df: pd.DataFrame, nic: int, radius: float, share: float) -> pd.DataFrame:
    df = df[df.groupby("icao")["nic"].transform("max") >= nic]
    df = df[df[f"km{nic}"] <= radius]
    per_ac = (df["nic"] < nic).groupby([df["h3"], df["icao"]], observed=True).mean().reset_index(name="bad_share")
    per_ac["bad_ac"] = per_ac["bad_share"] >= share
    cell = per_ac.groupby("h3").agg(n_ac=("icao", "count"), n_bad=("bad_ac", "sum")).reset_index()
    cell = cell[cell["n_ac"] >= C.MIN_AIRCRAFT_DAY]
    return cell.assign(ratio=cell["n_bad"] / cell["n_ac"])


@contextmanager
def _thresholds(ratio: float, min_bad: int, alpha: float):
    old = C.RATIO_HIGH, C.MIN_BAD_AIRCRAFT, C.BINOM_ALPHA
    C.RATIO_HIGH, C.MIN_BAD_AIRCRAFT, C.BINOM_ALPHA = ratio, min_bad, alpha
    try:
        yield
    finally:
        C.RATIO_HIGH, C.MIN_BAD_AIRCRAFT, C.BINOM_ALPHA = old


def _evaluate(cells: pd.DataFrame, params: dict, meta: pd.DataFrame) -> dict:
    with _thresholds(params["ratio"], params["min_bad"], params["alpha"]):
        scored = score(cells, background_rate(cells))
    j = scored.loc[scored["jammed"], ["date", "h3"]].merge(meta, on="h3")
    return dict(p0=round(float(background_rate(cells)), 5), keys=set(zip(j["date"], j["h3"])),
                area=j.groupby("date")["area"].sum().reindex(sorted(cells["date"].unique()), fill_value=0).mean(),
                regions=j.groupby("region")["area"].sum())


def run() -> dict:
    days = {p.name.split("=")[1]: p for p in sorted(OUT.glob("date=*"))}
    cache: dict[tuple, pd.DataFrame] = {}
    needed = {(p.get("nic", BASE["nic"]), p.get("radius", BASE["radius"]), p.get("share", BASE["share"])) for _, p in VARIANTS}
    for day, path in days.items():
        df = pd.read_parquet(path)
        df = df[df["version"].fillna(0) >= 1].drop(columns="version")
        for key in needed:
            cache.setdefault(key, []).append(_day_cells(df, *key).assign(date=day))
        print(day, flush=True)
    cache = {k: pd.concat(v, ignore_index=True) for k, v in cache.items()}

    cells = pd.concat([c[["h3"]] for c in cache.values()]).drop_duplicates()
    ll = [h3.cell_to_latlng(h3.int_to_str(int(c))) for c in cells["h3"]]
    meta = cells.assign(region=[region_of(a, b) for a, b in ll], area=[h3.cell_area(h3.int_to_str(int(c)), "km^2") for c in cells["h3"]],
                        central=[46 <= a <= 55 and 3 <= b <= 16 for a, b in ll])

    base = None
    rows = []
    for name, change in VARIANTS:
        p = {**BASE, **change}
        cell = cache[(p["nic"], p["radius"], p["share"])]
        ev = _evaluate(cell, p, meta)
        base = base or ev
        common = sorted(set(base["regions"].index) | set(ev["regions"].index))
        rb, re = base["regions"].reindex(common, fill_value=0), ev["regions"].reindex(common, fill_value=0)
        central = meta.set_index("h3")["central"]
        rows.append(dict(
            name=name, change={k: ("aus" if v == math.inf else v) for k, v in change.items()},
            cell_days=len(ev["keys"]), vs_base=round(len(ev["keys"]) / len(base["keys"]), 3),
            area_km2=round(float(ev["area"])), jaccard=round(len(ev["keys"] & base["keys"]) / max(len(ev["keys"] | base["keys"]), 1), 3),
            kept=round(len(ev["keys"] & base["keys"]) / len(base["keys"]), 3),
            region_rank_spearman=round(float(spearmanr(rb, re).statistic), 3),
            top3=list(ev["regions"].sort_values(ascending=False).index[:3]),
            central_cell_days=int(sum(bool(central.loc[h]) for _, h in ev["keys"])), p0=ev["p0"],
        ))
    result = dict(baseline=BASE, days=len(days), variants=rows)
    result["baseline"] = {k: v for k, v in BASE.items()}
    (C.GOLD_DIR / "sensitivity.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("step", choices=["prepare", "run"])
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()
    if a.step == "prepare":
        prepare(a.workers)
    else:
        r = run()
        for v in r["variants"]:
            print(f"{v['name']:38s} {v['cell_days']:>6,} ({v['vs_base']:.2f}×)  {v['area_km2']:>8,} km²/Tag  Jaccard {v['jaccard']:.2f}  behalten {v['kept']:.2f}  Regionen-Rang {v['region_rank_spearman']:.2f}  Mitteleuropa {v['central_cell_days']}")


if __name__ == "__main__":
    main()
