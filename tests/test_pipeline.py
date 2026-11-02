"""Tests der Kernlogik: Nachwirkung, Statistik, Zonen, Kartenregeln und Engine-Gleichheit."""
from __future__ import annotations

import os
from pathlib import Path

import h3
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gpsjam import aggregate, config as C, detect
from gpsjam.build_map import NO_DATA, flare_onsets, hourly_frames
from gpsjam.parse_traces import SCHEMA, _km, quality_points

ROOT = Path(__file__).resolve().parents[1]


def point(t: float, lat: float, lon: float, nic: int | None, alt=35000, source="adsb_icao", details=True) -> list:
    det = {"nic": nic, "nac_p": 9, "rc": 186, "version": 2} if details else None
    return [t, lat, lon, alt, 450.0, 90.0, 0, 0, det, source, None, None, None, None]


# ---------- Nachwirkung und Punktauswahl ----------

def test_ignores_ground_mlat_and_points_without_details():
    trace = [
        point(0, 50, 10, 8, alt="ground"),
        point(1, 50, 10, 8, alt=500),
        point(2, 50, 10, 8, source="mlat"),
        point(3, 50, 10, 8, details=False),
        point(4, 50, 10, None),
        point(5, 50, 10, 8),
    ]
    assert [p[0] for p in quality_points(trace, 1000)] == [1005]


def test_afterglow_starts_only_beyond_radius():
    near = 50 + 400 / 111.2  # etwa 400 km nördlich
    far = 50 + 600 / 111.2   # etwa 600 km nördlich
    trace = [point(0, 50, 10, 8), point(60, 50, 10, 0), point(120, near, 10, 0), point(180, far, 10, 0)]
    assert [p[-1] for p in quality_points(trace, 0)] == [False, False, False, True]


def test_good_message_ends_the_phase_and_resets_onset():
    far = 50 + 600 / 111.2
    trace = [point(0, 50, 10, 0), point(60, far, 10, 8), point(120, far, 10, 0)]
    assert [p[-1] for p in quality_points(trace, 0)] == [False, False, False]


def test_phase_survives_reception_gap_up_to_limit():
    far = 50 + 600 / 111.2
    within = [point(0, 50, 10, 0), point(C.RUN_GAP_S - 1, far, 10, 0)]
    beyond = [point(0, 50, 10, 0), point(C.RUN_GAP_S + 1, far, 10, 0)]
    assert quality_points(within, 0)[-1][-1] is True
    assert quality_points(beyond, 0)[-1][-1] is False


def test_haversine_distance():
    assert _km(0, 0, 0, 1) == pytest.approx(111.2, abs=0.2)
    assert _km(50, 10, 50, 10) == 0


# ---------- Statistik ----------

def cells(rows: list[tuple[int, int]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["n_ac", "n_bad"])
    return df.assign(ratio=df["n_bad"] / df["n_ac"])


def test_binomial_decision_uses_all_three_conditions():
    # p0 = 0,29 %: 6/30 und 2/5 sind gestört, 1/5 scheitert an Test und 2-Flugzeug-Regel,
    # 3/100 am Anteil, 1/2 an der 2-Flugzeug-Regel
    out = detect.score(cells([(30, 6), (5, 2), (5, 1), (100, 3), (2, 1)]), 0.0029)
    assert out["jammed"].tolist() == [True, True, False, False, False]
    assert out["p_value"].iloc[0] == pytest.approx(3.33e-10, rel=0.01)
    assert out["p_value"].iloc[1] == pytest.approx(8.36e-5, rel=0.01)


def test_two_aircraft_rule_is_independent_of_the_test():
    # Mit sehr kleinem p0 wäre 1 von 2 hochsignifikant, zählt aber trotzdem nicht
    out = detect.score(cells([(2, 1)]), 1e-6)
    assert out["p_value"].iloc[0] < C.BINOM_ALPHA
    assert not out["jammed"].iloc[0]


def test_background_rate_ignores_loudest_cells_and_has_floor():
    quiet = [(100, 1)] * 9
    assert detect.background_rate(cells(quiet + [(100, 90)])) == pytest.approx(0.01)
    assert detect.background_rate(cells([(100, 0)] * 10)) == 1e-3


def test_robust_z_flags_only_jammed_outlier_days():
    ratios = [0.1, 0.11, 0.09, 0.1, 0.12, 0.1, 0.09, 0.9]
    day = pd.DataFrame({"h3": 1, "date": range(8), "ratio": ratios, "jammed": [True] * 8})
    out = detect.temporal_anomalies(day)
    assert out["flare_up"].tolist() == [False] * 7 + [True]
    med, mad = np.median(ratios), np.median(np.abs(np.array(ratios) - np.median(ratios)))
    assert out["robust_z"].iloc[7] == pytest.approx((0.9 - med) / (1.4826 * mad + 0.01))


def test_robust_z_requires_history():
    day = pd.DataFrame({"h3": 1, "date": range(3), "ratio": [0.1, 0.1, 0.9], "jammed": [True] * 3})
    assert not detect.temporal_anomalies(day)["flare_up"].any()


# ---------- Zonen und Regionen ----------

def test_zones_join_neighbours_and_drop_isolated_cells():
    a = h3.latlng_to_cell(55.0, 20.0, C.H3_AGG_RES)
    b = next(iter(h3.grid_ring(a, 1)))
    lonely = h3.latlng_to_cell(40.0, 0.0, C.H3_AGG_RES)
    day = pd.DataFrame({
        "date": pd.Timestamp("2026-10-15"), "h3": [h3.str_to_int(x) for x in (a, b, lonely)],
        "n_ac": [10, 10, 10], "n_bad": [4, 2, 5], "jammed": True,
    })
    z = detect.zones(day)
    assert len(z) == 1
    assert z.iloc[0]["n_cells"] == 2 and z.iloc[0]["n_bad"] == 6
    assert z.iloc[0]["ratio"] == pytest.approx(0.3)


@pytest.mark.parametrize("lat,lon,name", [
    (54.7, 20.5, "Ostsee & Baltikum"), (44.6, 33.5, "Schwarzes Meer"), (33.5, 35.5, "Ostmittelmeer & Levante"),
    (28.0, 49.0, "Persischer Golf"), (48.0, 2.0, "Sonstige"),
])
def test_regions(lat, lon, name):
    assert detect.region_of(lat, lon) == name


# ---------- Kartenregeln ----------

def test_hourly_frames_window_and_minimum_aircraft():
    t = pd.date_range("2026-10-15", periods=5, freq="h", tz="UTC")
    hour = pd.DataFrame({"h3": 7, "hour": t, "n_ac": [10, 10, 10, 1, 0], "n_bad": [0, 5, 0, 1, 0]}).astype({"h3": "uint64"})
    _, _, ratio, w_ac = hourly_frames(hour)
    # 3-h-Fenster: 5 von 30 = 17 %; ein einzelnes betroffenes Flugzeug zählt als ungestört
    assert ratio[0, 1] == 17
    assert w_ac[0, 1] == 30
    assert ratio[0, 3] == pytest.approx(0)  # Fenster enthält nur 1 gestörtes Flugzeug
    assert ratio[0, 4] == NO_DATA           # zu wenig Verkehr


def series(values: list[int]) -> np.ndarray:
    return np.array([values], dtype=float)


def test_flare_needs_quiet_history_data_and_persistence():
    quiet, hot = [0] * C.FLARE_QUIET_H, [20] * C.FLARE_AFTER_H
    assert flare_onsets(series(quiet + [20] + hot)) == [[C.FLARE_QUIET_H, 0]]
    # kurzes Aufblitzen ohne Fortsetzung
    assert flare_onsets(series(quiet + [20] + [0] * C.FLARE_AFTER_H)) == []
    # keine Daten davor beweisen keine Ruhe
    assert flare_onsets(series([NO_DATA] * C.FLARE_QUIET_H + [20] + hot)) == []
    # NO_DATA (255) darf selbst nicht als Störung zählen
    assert flare_onsets(series(quiet + [NO_DATA] * (C.FLARE_AFTER_H + 1))) == []


# ---------- Ende-zu-Ende: Aggregation und Engine-Gleichheit ----------

@pytest.fixture
def silver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cell_a = h3.str_to_int(h3.latlng_to_cell(55.0, 20.0, C.H3_STORE_RES))
    ts = pd.Timestamp("2026-10-15 12:00", tz="UTC")
    rows = []

    def add(icao, nic, cell=cell_a, version=2, glow=False, n=4):
        for i in range(n):
            rows.append(dict(icao=icao, ts=ts + pd.Timedelta(minutes=i), lat=55.0, lon=20.0, alt_ft=35000, gs_kt=450.0,
                             nic=nic, nac_p=9, rc_m=186, version=version, h3_r5=cell, afterglow=glow))

    for i in range(4):
        add(f"good{i}", 8)
    add("jam", 0)
    add("jam", 8, n=1)                 # sonst NIC >= 7 irgendwann: Transponder-Filter besteht
    add("jam2", 0)
    add("jam2", 8, n=1)
    add("chronic", 3)                  # nie gut: fällt durch den Transponder-Filter
    add("old", 0, version=0)           # ADS-B Version 0: fällt raus
    add("glow", 0, glow=True)          # nur Nachwirkung: fällt raus
    add("glow", 8, n=1)
    out = tmp_path / "silver" / "positions" / "date=2026-10-15"
    out.mkdir(parents=True)
    df = pd.DataFrame(rows)
    df["ts"] = df["ts"].dt.tz_convert("UTC").astype("datetime64[s, UTC]")
    pq.write_table(pa.Table.from_pandas(df, schema=SCHEMA, preserve_index=False), out / "part-0.parquet")
    monkeypatch.setattr(C, "SILVER_DIR", tmp_path / "silver")
    monkeypatch.setattr(C, "GOLD_DIR", tmp_path / "gold")
    return tmp_path


EXPECTED_PARENT = h3.str_to_int(h3.cell_to_parent(h3.latlng_to_cell(55.0, 20.0, C.H3_STORE_RES), C.H3_AGG_RES))


def check_gold(hour: pd.DataFrame, day: pd.DataFrame) -> None:
    # 4 gute + jam + jam2 + glow (dessen eine gute Meldung bleibt) = 7 Flugzeuge, davon 2 gestört
    for table in (hour, day):
        assert len(table) == 1
        row = table.iloc[0]
        assert int(row["h3"]) == EXPECTED_PARENT
        assert row["n_ac"] == 7 and row["n_bad"] == 2


def test_dask_aggregation_applies_all_filters(silver: Path):
    import dask
    with dask.config.set(scheduler="threads", num_workers=2):
        aggregate.run(2)
    check_gold(pd.read_parquet(C.GOLD_DIR / "hex_hour.parquet"), pd.read_parquet(C.GOLD_DIR / "hex_day.parquet"))


JDK = sorted((ROOT / ".jdk").glob("*/Contents/Home"))


@pytest.mark.skipif(not (os.environ.get("JAVA_HOME") or JDK), reason="kein JDK")
def test_spark_aggregation_matches_expected_and_h3_parent(silver: Path, monkeypatch: pytest.MonkeyPatch):
    if not os.environ.get("JAVA_HOME"):
        monkeypatch.setenv("JAVA_HOME", str(JDK[0]))
    from pyspark.sql import SparkSession

    from gpsjam.spark_aggregate import run
    spark = (SparkSession.builder.master("local[2]").config("spark.sql.session.timeZone", "UTC")
             .config("spark.ui.enabled", "false").getOrCreate())
    try:
        run(spark, "_spark")
    finally:
        spark.stop()
    check_gold(pd.read_parquet(C.GOLD_DIR / "hex_hour_spark"), pd.read_parquet(C.GOLD_DIR / "hex_day_spark"))


# ---------- Empfindlichkeitstest und Validierung ----------

def test_sensitivity_distances_match_silver_flag():
    from gpsjam.sensitivity import NIC_VALUES, onset_distances
    far = 50 + 600 / 111.2
    trace = [point(0, 50, 10, 8), point(60, 50, 10, 0), point(120, far, 10, 0), point(180, far, 10, 8), point(240, far, 10, 0)]
    flags = [p[-1] for p in quality_points(trace, 0)]
    km = [x[4][NIC_VALUES.index(C.NIC_BAD_BELOW)] for x in onset_distances(trace, 0)]
    assert [k > C.AFTERGLOW_MAX_KM for k in km] == flags


def test_gpsjam_formula_and_agreement_metrics():
    from gpsjam.validate_gpsjam import compare_cells, kappa, metrics
    ours = pd.DataFrame({"h3": [1, 2, 3], "date": "d", "n_ac": [50, 50, 50], "n_bad": [20, 0, 1],
                         "ratio": [0.4, 0.0, 0.02], "jammed": [True, False, False]})
    theirs = pd.DataFrame({"h3": [1, 2, 3], "date": "d", "count_good_aircraft": [30, 50, 2],
                           "count_bad_aircraft": [20, 0, 1]})
    m = compare_cells(ours, theirs)
    # Zelle 3 hat bei gpsjam nur 3 Flugzeuge -> nicht vergleichbar; (20 - 1) / 50 = 38 %
    assert m["h3"].tolist() == [1, 2]
    assert m["g_ratio"].iloc[0] == pytest.approx(0.38)
    assert m["g_ratio"].iloc[1] == 0
    r = metrics(m)
    assert r["both"] == 1 and r["neither"] == 1 and r["share_ours_confirmed"] == 1.0
    assert kappa(np.array([True, False]), np.array([True, False])) == pytest.approx(1.0)
