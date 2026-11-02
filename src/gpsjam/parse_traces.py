"""Silver-Stufe: parst die gzip-JSON-Flugspuren parallel mit Dask und schreibt
bereinigte Positionsmeldungen als Parquet.

Jede Datei enthält die Tagesspur eines Flugzeugs (readsb-Format). Ein
Spurpunkt ist eine Liste
    [t_offset, lat, lon, alt_baro, gs, track, flags, vrate, details, source, ...]
`details` (dict) ist nur bei ~25 % der Punkte gesetzt und enthält die
Qualitätsindikatoren nic, nac_p, rc, version. Nur diese Punkte tragen die
Information, die wir brauchen.

Pro Spur wird außerdem die "Nachwirkung" markiert: Punkte einer schlechten
Phase (NIC < 7), die mehr als AFTERGLOW_MAX_KM vom Beginn dieser Phase
entfernt liegen. Das muss hier passieren, solange die Spur noch vollständig
(auch außerhalb Europas) vorliegt – der Störungsbeginn liegt oft im Nahen Osten.

    python -m gpsjam.parse_traces 2026-10-28 [--workers 8]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import time
from pathlib import Path

import dask.bag as db
import h3
import orjson
import pyarrow as pa
import pyarrow.parquet as pq

from . import config as C

SCHEMA = pa.schema([
    ("icao", pa.string()),
    ("ts", pa.timestamp("s", tz="UTC")),
    ("lat", pa.float32()),
    ("lon", pa.float32()),
    ("alt_ft", pa.int32()),
    ("gs_kt", pa.float32()),
    ("nic", pa.int8()),
    ("nac_p", pa.int8()),
    ("rc_m", pa.int32()),
    ("version", pa.int8()),
    ("h3_r5", pa.uint64()),
    ("afterglow", pa.bool_()),
])
FILES_PER_TASK = 400


def _load(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return orjson.loads(raw)


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(a)))


def quality_points(trace: list, t0: float) -> list[tuple]:
    """Luftgestützte ADS-B-Punkte mit NIC, inkl. Nachwirkungs-Flag."""
    pts, onset, prev_t, prev_bad = [], None, None, False
    for pt in trace:
        det = pt[8]
        if not isinstance(det, dict) or pt[9] != "adsb_icao" or det.get("nic") is None:
            continue
        alt = pt[3]
        if alt == "ground" or alt is None or alt < C.MIN_ALT_FT:
            continue
        t, lat, lon, nic = t0 + pt[0], pt[1], pt[2], det["nic"]
        bad = nic < C.NIC_BAD_BELOW
        if bad and (not prev_bad or t - prev_t > C.RUN_GAP_S):
            onset = (lat, lon)
        afterglow = bad and _km(onset[0], onset[1], lat, lon) > C.AFTERGLOW_MAX_KM
        pts.append((int(t), lat, lon, int(alt), pt[4], nic, det.get("nac_p"), det.get("rc"),
                    det.get("version"), afterglow))
        prev_t, prev_bad = t, bad
    return pts


def parse_batch(args: tuple[int, list[str], str]) -> dict:
    """Parst eine Gruppe von Spurdateien und schreibt eine Parquet-Datei."""
    idx, paths, out_dir = args
    b = C.BBOX
    cols = {name: [] for name in SCHEMA.names}
    stats = dict(files=0, points_total=0, points_with_quality=0, points_kept=0, points_afterglow=0)

    for p in paths:
        try:
            d = _load(Path(p))
        except (OSError, ValueError):
            continue
        stats["files"] += 1
        icao, t0 = d["icao"], d["timestamp"]
        trace = d.get("trace") or []
        stats["points_total"] += len(trace)
        for t, lat, lon, alt, gs, nic, nac_p, rc, ver, glow in quality_points(trace, t0):
            stats["points_with_quality"] += 1
            if not (b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]):
                continue
            cell = h3.str_to_int(h3.latlng_to_cell(lat, lon, C.H3_STORE_RES))
            for name, v in zip(SCHEMA.names, (icao, t, lat, lon, alt, gs, nic, nac_p, rc, ver, cell, glow)):
                cols[name].append(v)
            stats["points_afterglow"] += glow

    stats["points_kept"] = len(cols["icao"])
    if cols["icao"]:
        table = pa.table(cols, schema=SCHEMA)
        pq.write_table(table, Path(out_dir) / f"part-{idx:05d}.parquet", compression="zstd")
    return stats


def parse_day(day: str, workers: int) -> dict:
    src = C.RAW_DIR / day
    if not (src / "_SUCCESS").exists():
        raise SystemExit(f"{src} ist nicht vollständig geladen")
    out = C.SILVER_DIR / "positions" / f"date={day}"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.parquet"):
        old.unlink()

    files = sorted(str(p) for p in src.glob("trace_full_*.json"))
    batches = [(i, files[i:i + FILES_PER_TASK], str(out)) for i in range(0, len(files), FILES_PER_TASK)]

    t = time.time()
    results = (db.from_sequence(batches, npartitions=len(batches))
                 .map(parse_batch)
                 .compute(scheduler="processes", num_workers=workers))
    stats = {k: sum(r[k] for r in results) for k in results[0]}
    stats.update(day=day, seconds=round(time.time() - t, 1), raw_bytes=sum(Path(f).stat().st_size for f in files))
    (out / "_stats.json").write_text(json.dumps(stats, indent=2))
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("days", nargs="+", help="YYYY-MM-DD")
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args()
    for day in a.days:
        print(json.dumps(parse_day(day, a.workers)))


if __name__ == "__main__":
    main()
