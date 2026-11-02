#!/usr/bin/env bash
# Komplette Pipeline: Bronze (Download) -> Silver (Parsen) -> Gold (Aggregation) -> Erkennung -> Karte
#   ./run_pipeline.sh 2026-10-15 2026-10-28          # mit Dask
#   SPARK=1 ./run_pipeline.sh 2026-10-15 2026-10-28  # zusätzlich Spark-Job + Abgleich Dask vs. Spark
set -euo pipefail
cd "$(dirname "$0")"
START=${1:?Startdatum YYYY-MM-DD}; END=${2:-$1}
PY=.venv/bin/python
export PYTHONPATH=src

days=$($PY -c "import pandas as pd,sys; print(' '.join(d.strftime('%Y-%m-%d') for d in pd.date_range('$START','$END')))")

echo "== Bronze: adsb.lol-Tagesdumps laden"
$PY -m gpsjam.ingest_adsblol "$START" "$END"
echo "== Silver: Flugspuren parsen (Dask, parallel)"
$PY -m gpsjam.parse_traces $days --workers "${WORKERS:-8}"
echo "== Gold: H3-Aggregation (Dask)"
$PY -m gpsjam.aggregate --workers "${WORKERS:-8}" 2>/dev/null
if [[ "${SPARK:-0}" == "1" ]]; then
  echo "== Gold: H3-Aggregation (Spark) + Abgleich"
  export JAVA_HOME=${JAVA_HOME:-$(ls -d "$PWD"/.jdk/*/Contents/Home 2>/dev/null | head -1)}
  PYSPARK_PYTHON=$PWD/$PY $PY -m gpsjam.spark_aggregate 2>/dev/null
  $PY scripts/compare_engines.py
fi
echo "== Anomalieerkennung + Störzonen"
$PY -m gpsjam.detect
echo "== Karte + Diagramme"
$PY -m gpsjam.build_map
$PY -m gpsjam.charts
