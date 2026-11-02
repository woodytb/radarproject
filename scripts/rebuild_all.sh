#!/usr/bin/env bash
# Rechnet alle Ergebnisse aus den vorhandenen Rohdaten neu (Silver bis Validierung).
#   scripts/rebuild_all.sh 2026-10-23 2026-10-27   # diese Tage neu einlesen, dann alles auswerten
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
export PYTHONPATH=src PYSPARK_PYTHON=$PWD/$PY
export JAVA_HOME=${JAVA_HOME:-$(ls -d "$PWD"/.jdk/*/Contents/Home | head -1)}
step() { echo; echo "== $(date '+%H:%M:%S') $1"; }
if [[ $# -ge 1 ]]; then
  step "Silver: neue Tage einlesen"
  days=$($PY -c "import pandas as pd; print(' '.join(d.strftime('%Y-%m-%d') for d in pd.date_range('$1','${2:-$1}')))")
  $PY -m gpsjam.parse_traces $days --workers "${WORKERS:-8}" | cut -c1-200
fi
step "Gold: Dask"
$PY -m gpsjam.aggregate --workers "${WORKERS:-6}" 2>/dev/null | tail -1
step "Gold: Spark und Abgleich"
$PY -m gpsjam.spark_aggregate --master "local[${WORKERS:-8}]" 2>/dev/null | tail -1
$PY scripts/compare_engines.py
step "Erkennung"
$PY -m gpsjam.detect > data/gold/detect_stdout.json
step "Karte"
$PY -m gpsjam.build_map
step "Diagramme"
$PY -m gpsjam.charts 2>&1 | grep -v findfont || true
step "Validierung gpsjam.org"
$PY -m gpsjam.validate_gpsjam 2>&1 | grep -v findfont > data/gold/validate_stdout.json
step "Empfindlichkeitstest"
$PY -m gpsjam.sensitivity prepare --workers "${WORKERS:-8}"
$PY -m gpsjam.sensitivity run
step "Tests"
$PY -m pytest tests -q 2>&1 | tail -1
step "FERTIG"
