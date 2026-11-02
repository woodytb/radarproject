"""Leistungsvergleich: Einlesen (Dask) und Aggregation (Dask, Spark) mit 1, 2, 4 und 8 Kernen.

Jede Messung ist ein eigener Prozess mit Wiederholungen; gemeldet wird der Median.
Ergebnisse landen in Temp-Verzeichnissen, die Projektergebnisse bleiben unberührt.

    .venv/bin/python scripts/benchmark.py [--parse-day 2026-10-20] [--reps 3]
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "bin" / "python")
JDK = sorted((ROOT / ".jdk").glob("*/Contents/Home"))


def timed(cmd: list[str], env: dict) -> float:
    t = time.perf_counter()
    subprocess.run(cmd, cwd=ROOT, env={**os.environ, "PYTHONPATH": "src", **env}, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - t


def measure(label: str, cores: int, reps: int, cmd: list[str], env: dict, rows: list, out: Path, only: set | None) -> None:
    if only is not None and f"{label}:{cores}" not in only:
        return
    rows[:] = [r for r in rows if (r["stage"], r["cores"]) != (label, cores)]
    times = []
    for _ in range(reps):
        try:
            times.append(round(timed(cmd, env), 1))
        except subprocess.CalledProcessError as e:  # z. B. Speicher zu knapp: festhalten, nicht abbrechen
            print(f"{label:16s} {cores} Kerne: FEHLGESCHLAGEN ({e.returncode})", flush=True)
            continue
        print(f"{label:16s} {cores} Kerne: {times[-1]} s", flush=True)
    rows.append(dict(stage=label, cores=cores, seconds=statistics.median(times) if times else None, runs=times))
    out.write_text(json.dumps(rows, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--parse-day", default="2026-10-20")
    p.add_argument("--reps", type=int, default=2)
    p.add_argument("--cores", type=int, nargs="+", default=[1, 2, 4, 8])
    p.add_argument("--only", nargs="+", metavar="STUFE:KERNE", help="nur diese Messungen wiederholen, z. B. \"Dask Gold:4\"; übrige bleiben erhalten")
    a = p.parse_args()
    out = ROOT / "data" / "gold" / "benchmark.json"
    only = set(a.only) if a.only else None
    rows: list = json.loads(out.read_text()) if only and out.exists() else []
    with tempfile.TemporaryDirectory(dir=ROOT / "data") as tmp:
        gold, silver = Path(tmp) / "gold", Path(tmp) / "silver"
        gold.mkdir()
        spark_env = {"GPSJAM_GOLD_DIR": str(gold), "PYSPARK_PYTHON": PY, "JAVA_HOME": os.environ.get("JAVA_HOME") or str(JDK[0])}
        for n in a.cores:
            measure("Spark Gold", n, a.reps, [PY, "-m", "gpsjam.spark_aggregate", "--master", f"local[{n}]"], spark_env, rows, out, only)
        for n in a.cores:
            measure("Dask Gold", n, a.reps, [PY, "-m", "gpsjam.aggregate", "--workers", str(n)], {"GPSJAM_GOLD_DIR": str(gold)}, rows, out, only)
        for n in a.cores:
            measure("Dask Silver", n, a.reps, [PY, "-m", "gpsjam.parse_traces", a.parse_day, "--workers", str(n)], {"GPSJAM_SILVER_DIR": str(silver)}, rows, out, only)
        shutil.rmtree(silver, ignore_errors=True)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    sys.exit(main())
