"""Gold-Stufe (Dask): verdichtet Milliarden Einzelmeldungen auf H3-Zellen.

Ablauf
  1. Silver-Parquet lesen (alle Tage, spaltenweise, partitioniert)
  2. Nachwirkungs-Punkte verwerfen (siehe parse_traces.py)
     Transponder-Filter: Flugzeuge, die an einem Tag nie NIC >= 7 melden,
     haben vermutlich schlechte Avionik statt gestörtes GPS -> raus
  3. Meldung "schlecht", wenn NIC < 7
  4. pro (Zelle, Stunde, Flugzeug): Anteil schlechter Meldungen;
     >= 50 % -> dieses Flugzeug war in dieser Zelle gestört
  5. pro (Zelle, Stunde) bzw. (Zelle, Tag): #Flugzeuge, #gestörte, Anteil

    python -m gpsjam.aggregate [--workers 8]
"""
from __future__ import annotations

import argparse
import time

import dask
import dask.dataframe as dd
import h3
import pandas as pd
from dask.distributed import Client, LocalCluster

from . import config as C

COLUMNS = ["icao", "ts", "nic", "version", "h3_r5", "afterglow"]


def _to_agg_res(cells: pd.Series) -> pd.Series:
    """H3 r5 -> r4 (Elternzelle). Nur einmal pro eindeutiger Zelle rechnen."""
    uniq = cells.unique()
    parent = {c: h3.str_to_int(h3.cell_to_parent(h3.int_to_str(int(c)), C.H3_AGG_RES)) for c in uniq}
    return cells.map(parent).astype("uint64")


def _prepare(part: pd.DataFrame) -> pd.DataFrame:
    part = part[(part["version"].fillna(0) >= 1) & ~part["afterglow"]]
    out = pd.DataFrame({
        "icao": part["icao"],
        "hour": part["ts"].dt.floor("h"),
        "h3": _to_agg_res(part["h3_r5"]),
        "bad": (part["nic"] < C.NIC_BAD_BELOW).astype("int8"),
        "nic": part["nic"],
    })
    out["date"] = out["hour"].dt.floor("D")
    return out


def _cell_counts(per_ac: dd.DataFrame, key: str, min_ac: int) -> dd.DataFrame:
    per_ac = per_ac.assign(bad_ac=(per_ac["bad_share"] >= C.BAD_SHARE_PER_AIRCRAFT).astype("int32"))
    cell = per_ac.groupby(["h3", key]).agg(n_ac=("icao", "count"), n_bad=("bad_ac", "sum"),
                                           n_msg=("n_msg", "sum"))
    cell = cell.reset_index()
    cell = cell[cell["n_ac"] >= min_ac]
    return cell.assign(ratio=cell["n_bad"] / cell["n_ac"])


def run(workers: int) -> None:
    t = time.time()
    df = dd.read_parquet(C.SILVER_DIR / "positions", columns=COLUMNS)
    meta = pd.DataFrame({"icao": pd.Series(dtype="string"), "hour": pd.Series(dtype="datetime64[s, UTC]"),
                         "h3": pd.Series(dtype="uint64"), "bad": pd.Series(dtype="int8"),
                         "nic": pd.Series(dtype="int8"), "date": pd.Series(dtype="datetime64[s, UTC]")})
    df = df.map_partitions(_prepare, meta=meta)

    # (2) Transponder-Filter auf Tagesebene
    healthy = df.groupby(["icao", "date"])["nic"].max().reset_index()
    healthy = healthy[healthy["nic"] >= C.NIC_BAD_BELOW][["icao", "date"]]
    df = df.merge(healthy, on=["icao", "date"], how="inner")
    df = df.persist()

    # (4) pro Flugzeug in Zelle x Zeitfenster
    outputs = {}
    for key, min_ac in (("hour", C.MIN_AIRCRAFT_HOUR), ("date", C.MIN_AIRCRAFT_DAY)):
        per_ac = (df.groupby(["h3", key, "icao"])["bad"]
                    .agg(["mean", "count"]).reset_index()
                    .rename(columns={"mean": "bad_share", "count": "n_msg"}))
        outputs[key] = _cell_counts(per_ac, key, min_ac)

    C.GOLD_DIR.mkdir(parents=True, exist_ok=True)
    hour, day = dd.compute(outputs["hour"], outputs["date"])
    hour.sort_values(["hour", "h3"]).to_parquet(C.GOLD_DIR / "hex_hour.parquet", index=False)
    day.sort_values(["date", "h3"]).to_parquet(C.GOLD_DIR / "hex_day.parquet", index=False)

    n_msgs = int(df.shape[0].compute())
    print(f"{n_msgs:,} Meldungen -> {len(hour):,} Zellen-Stunden, {len(day):,} Zellen-Tage "
          f"in {time.time() - t:.0f}s")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--scheduler", choices=["distributed", "threads"], default="distributed",
                   help="threads: ohne Worker-Prozesse (robust, z. B. wenn der Laptop schlafen geht)")
    p.add_argument("--memory-gb", type=float, default=9.0, help="Arbeitsspeicher aller Worker zusammen")
    a = p.parse_args()
    if a.scheduler == "threads":
        with dask.config.set(scheduler="threads", num_workers=a.workers):
            run(a.workers)
        return
    with LocalCluster(n_workers=a.workers, threads_per_worker=1, memory_limit=f"{a.memory_gb / a.workers:.2f}GiB") as cluster, Client(cluster):
        print(f"Dask-Dashboard: {cluster.dashboard_link}")
        run(a.workers)


if __name__ == "__main__":
    main()
