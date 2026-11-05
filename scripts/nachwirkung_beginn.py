"""Wo beginnen lange schlechte NIC-Phasen, und führen sie in die Irre?

Liest die Rohspuren (Bronze), weil Silver nur den Kartenausschnitt enthält und
der Beginn einer Phase weit davor liegen kann. Zwei Prüfungen:

1. Alle Flugzeug-Tage mit Nachwirkung: Beginn jeder Phase von mindestens 1 h,
   sofern weniger als 60 Minuten vorher ein guter Wert vorlag.
2. Fallbeispiel A350 aus dem Vortrag, dazu die Kontrolle über Frankfurt.

Ergebnis: data/gold/nachwirkung_beginn.json
Aufruf: .venv/bin/python scripts/nachwirkung_beginn.py [--workers 8]
"""
import argparse
import glob
import gzip
import json
import math
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gpsjam import config as C  # noqa: E402

CASE_DATE, CASE_ICAO = "2026-10-23", "88510d"
CASE_LANDING = pd.Timestamp(f"{CASE_DATE} 18:30", tz="UTC")  # nach der Landung, vor dem Rückflug
FRA = dict(lat=(49.5, 50.5), lon=(8.0, 9.5))
FRA_START = pd.Timestamp(f"{CASE_DATE} 16:30", tz="UTC")
FRA_END = pd.Timestamp(f"{CASE_DATE} 17:30", tz="UTC")


def read_trace(date: str, icao: str) -> dict | None:
    path = C.RAW_DIR / date / f"trace_full_{icao}.json"
    if not path.exists():
        return None
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def nic_points(trace: dict) -> pd.DataFrame:
    """Punkte mit NIC-Wert aus einer Rohspur."""
    rows = []
    for p in trace["trace"]:
        details = p[8] if len(p) > 8 and isinstance(p[8], dict) else None
        if details and details.get("nic") is not None:
            rows.append((trace["timestamp"] + p[0], p[1], p[2], p[3], details["nic"]))
    df = pd.DataFrame(rows, columns=["t", "lat", "lon", "alt", "nic"])
    df["ts"] = pd.to_datetime(df["t"], unit="s", utc=True)
    return df


def phases(arg: tuple[str, str]) -> list[dict]:
    """Schlechte Phasen einer Spur: Sie beginnen in der Luft und enden beim nächsten guten Wert."""
    date, icao = arg
    trace = read_trace(date, icao)
    if trace is None:
        return []
    out, cur, last_good, last = [], None, None, None
    for p in trace["trace"]:
        details = p[8] if len(p) > 8 and isinstance(p[8], dict) else None
        if not details or details.get("nic") is None:
            continue
        t, lat, lon, ground = trace["timestamp"] + p[0], p[1], p[2], p[3] == "ground"
        if details["nic"] >= C.NIC_BAD_BELOW:
            if cur:
                out.append({**cur, "t1": last[0], "lat1": last[1], "lon1": last[2]})
                cur = None
            last_good = t
        elif not ground:
            if cur is None:
                cur = dict(date=date, icao=icao, t0=t, lat0=lat, lon0=lon,
                           good_ago_min=(t - last_good) / 60 if last_good else None)
            last = (t, lat, lon)
    if cur:
        out.append({**cur, "t1": last[0], "lat1": last[1], "lon1": last[2]})
    return out


def zone(lat: float, lon: float) -> str:
    """Grobe Zuordnung des Phasenbeginns. Die Reihenfolge entscheidet bei Überlappung."""
    if lon > 50 or lon < -25 or lat < 27:
        return "außerhalb Kartenausschnitt"
    if 38 <= lat <= 48 and 27 <= lon <= 50:
        return "Schwarzes Meer und Kaukasus"
    if 52 <= lat <= 66 and 15 <= lon <= 35:
        return "Ostsee und Baltikum"
    if 27 <= lat <= 38 and 25 <= lon <= 50:
        return "Ostmittelmeer, Levante, Golf"
    if 44 <= lat <= 54 and 20 <= lon <= 30:
        return "Ostflanke"
    return "übriges Europa"


def all_phases(workers: int) -> dict:
    pairs = set()
    for day in sorted((C.SILVER_DIR / "positions").glob("date=*")):
        t = pd.read_parquet(day, columns=["icao", "afterglow"])
        pairs |= {(day.name.split("=")[1], i) for i in t.loc[t["afterglow"], "icao"].unique()}
    with ProcessPoolExecutor(workers) as ex:
        rows = [r for rs in ex.map(phases, sorted(pairs), chunksize=64) for r in rs]
    p = pd.DataFrame(rows)
    p["dur_h"] = (p["t1"] - p["t0"]) / 3600
    long_ = p[(p["dur_h"] >= 1) & (p["good_ago_min"] < 60)]
    zones = pd.Series([zone(a, b) for a, b in zip(long_["lat0"], long_["lon0"])]).value_counts()
    core = ["Schwarzes Meer und Kaukasus", "Ostsee und Baltikum", "Ostflanke"]
    return dict(aircraft_days=len(pairs), long_phases=len(long_), aircraft=int(long_["icao"].nunique()),
                duration_median_h=round(float(long_["dur_h"].median()), 2),
                onset_zone=zones.to_dict(), share_core_zones_pct=round(float(zones[core].sum() / len(long_) * 100), 1))


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return 6371 * math.acos(math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl))


def case() -> dict:
    trace = read_trace(CASE_DATE, CASE_ICAO)
    n = nic_points(trace)
    bad = n[n["nic"] < C.NIC_BAD_BELOW]
    first = bad.iloc[0]
    inbound = bad[bad["ts"] < CASE_LANDING]
    last = inbound.iloc[-1]
    before = n[(n["ts"] < first["ts"])]
    after = n[n["ts"] > last["ts"]]
    back = after[after["nic"] < C.NIC_BAD_BELOW].iloc[0]

    t = pd.read_parquet(C.SILVER_DIR / "positions" / f"date={CASE_DATE}", columns=["icao", "ts", "lat", "lon", "nic", "afterglow"])
    t["ts"] = pd.to_datetime(t["ts"], utc=True)
    w = t[t["lat"].between(*FRA["lat"]) & t["lon"].between(*FRA["lon"]) & (t["ts"] >= FRA_START) & (t["ts"] < FRA_END)]
    a = w.groupby("icao").agg(bad=("nic", lambda s: (s < C.NIC_BAD_BELOW).mean()), glow=("afterglow", "any"))
    hit = a[a["bad"] >= C.BAD_SHARE_PER_AIRCRAFT]

    fmt = lambda ts: ts.strftime("%H:%M:%S")
    minutes = (last["t"] - first["t"]) / 60
    return dict(
        date=CASE_DATE, type=trace.get("t"),
        last_good=dict(time_utc=fmt(before.iloc[-1]["ts"]), lat=round(before.iloc[-1]["lat"], 2), lon=round(before.iloc[-1]["lon"], 2), nic=int(before.iloc[-1]["nic"])),
        first_bad=dict(time_utc=fmt(first["ts"]), lat=round(first["lat"], 2), lon=round(first["lon"], 2), nic=int(first["nic"])),
        last_bad_inbound=dict(time_utc=fmt(last["ts"]), lat=round(last["lat"], 2), lon=round(last["lon"], 2), alt_ft=last["alt"], nic=int(last["nic"])),
        inbound_duration=f"{int(minutes // 60)} h {int(minutes % 60)} min", inbound_nic_values=inbound["nic"].value_counts().to_dict(),
        distance_km=round(km(first["lat"], first["lon"], last["lat"], last["lon"])),
        next_value_after_landing=dict(time_utc=fmt(after.iloc[0]["ts"]), nic=int(after.iloc[0]["nic"])),
        return_flight_first_bad=dict(time_utc=fmt(back["ts"]), lat=round(back["lat"], 2), lon=round(back["lon"], 2), nic=int(back["nic"])),
        frankfurt_control=dict(window_utc=f"{FRA_START:%H:%M}-{FRA_END:%H:%M}", aircraft=len(a), disturbed=len(hit), disturbed_with_afterglow=int(hit["glow"].sum())),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    result = dict(case_a350=case(), all_phases=all_phases(args.workers))
    out = C.GOLD_DIR / "nachwirkung_beginn.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf8")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
