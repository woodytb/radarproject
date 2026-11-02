"""Gold-Stufe als Spark-Job – dieselbe Logik wie aggregate.py (Dask),
gedacht für den Cluster-Betrieb (z. B. Monate an Daten auf HDFS/S3).

Lokal:   python -m gpsjam.spark_aggregate
Cluster: spark-submit --py-files gpsjam.zip -m gpsjam.spark_aggregate --master yarn

Unterschied zu Dask: Die H3-Elternzelle wird mit Bit-Arithmetik direkt in
Spark-SQL berechnet (kein Python-UDF -> läuft komplett in der JVM).
"""
from __future__ import annotations

import argparse
import time

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

from . import config as C


def h3_parent(col: F.Column, child_res: int, parent_res: int) -> F.Column:
    """H3-Index (64 bit) auf gröbere Auflösung: Auflösungsfeld (Bits 52-55)
    setzen und die nicht mehr genutzten 3-Bit-Ziffern auf 7 (=ungenutzt) füllen."""
    x = col.cast("long")
    x = (x.bitwiseAND(F.lit(~(0xF << 52)))).bitwiseOR(F.lit(parent_res << 52))
    for r in range(parent_res + 1, child_res + 1):
        x = x.bitwiseOR(F.lit(0x7 << ((15 - r) * 3)))
    return x


def run(spark: SparkSession, out_suffix: str) -> None:
    t = time.time()
    df = (spark.read.parquet(str(C.SILVER_DIR / "positions"))
          .where((F.coalesce("version", F.lit(0)) >= 1) & ~F.col("afterglow"))
          .select("icao",
                  F.date_trunc("hour", "ts").alias("hour"),
                  F.to_date(F.col("ts")).alias("date"),
                  h3_parent(F.col("h3_r5"), C.H3_STORE_RES, C.H3_AGG_RES).alias("h3"),
                  (F.col("nic") < C.NIC_BAD_BELOW).cast("int").alias("bad"),
                  "nic"))

    # Transponder-Filter: Flugzeug muss am Tag mindestens einmal NIC >= 7 melden
    w = Window.partitionBy("icao", "date")
    df = df.withColumn("max_nic", F.max("nic").over(w)).where(F.col("max_nic") >= C.NIC_BAD_BELOW).cache()

    for key, min_ac, name in (("hour", C.MIN_AIRCRAFT_HOUR, "hex_hour"), ("date", C.MIN_AIRCRAFT_DAY, "hex_day")):
        per_ac = df.groupBy("h3", key, "icao").agg(F.avg("bad").alias("bad_share"), F.count("*").alias("n_msg"))
        cell = (per_ac.groupBy("h3", key)
                .agg(F.count("*").alias("n_ac"),
                     F.sum((F.col("bad_share") >= C.BAD_SHARE_PER_AIRCRAFT).cast("int")).alias("n_bad"),
                     F.sum("n_msg").alias("n_msg"))
                .where(F.col("n_ac") >= min_ac)
                .withColumn("ratio", F.col("n_bad") / F.col("n_ac")))
        cell.orderBy(key, "h3").write.mode("overwrite").parquet(str(C.GOLD_DIR / f"{name}{out_suffix}"))
    print(f"{df.count():,} Meldungen aggregiert in {time.time() - t:.0f}s")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--suffix", default="_spark", help="Ausgabe neben den Dask-Ergebnissen ablegen")
    p.add_argument("--master", default="local[*]", help="z. B. local[4] oder yarn")
    a = p.parse_args()
    spark = (SparkSession.builder.appName("gps-jamming-radar").master(a.master)
             .config("spark.sql.session.timeZone", "UTC")
             .config("spark.driver.memory", "6g")
             .getOrCreate())
    try:
        run(spark, a.suffix)
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
