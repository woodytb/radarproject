"""Kreuzvalidierung: liefern Dask (aggregate.py) und Spark (spark_aggregate.py)
identische Gold-Tabellen?"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gpsjam import config as C  # noqa: E402

ok = True
for name, key in (("hex_day", "date"), ("hex_hour", "hour")):
    a = pd.read_parquet(C.GOLD_DIR / f"{name}.parquet")
    b = pd.read_parquet(C.GOLD_DIR / f"{name}_spark")
    a[key] = pd.to_datetime(a[key], utc=True).dt.tz_localize(None)
    b[key] = pd.to_datetime(b[key], utc=True).dt.tz_localize(None)
    b["h3"] = b["h3"].astype("uint64")
    m = a.merge(b, on=["h3", key], how="outer", suffixes=("_dask", "_spark"), indicator=True)
    same = (m["_merge"] == "both") & (m["n_ac_dask"] == m["n_ac_spark"]) & (m["n_bad_dask"] == m["n_bad_spark"])
    print(f"{name:9s} Dask {len(a):>9,}  Spark {len(b):>9,}  identisch: {same.mean():.4%}")
    ok &= bool(same.all())
sys.exit(0 if ok else 1)
