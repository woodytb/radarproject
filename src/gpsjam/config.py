"""Zentrale Parameter der Pipeline (eine Stelle zum Drehen)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"          # Bronze: Original-Flugspuren (gzip-JSON)
SILVER_DIR = Path(os.environ.get("GPSJAM_SILVER_DIR", ROOT / "data" / "silver"))  # Silver: bereinigte Positionen (Parquet)
GOLD_DIR = Path(os.environ.get("GPSJAM_GOLD_DIR", ROOT / "data" / "gold"))  # Gold: H3-Aggregate, Anomalien, Zonen
OUTPUT_DIR = ROOT / "output"

# Untersuchungsgebiet: Europa inkl. Ostsee, Schwarzes Meer, östliches Mittelmeer
BBOX = dict(lat_min=27.0, lat_max=72.0, lon_min=-25.0, lon_max=50.0)

# Nur Messungen in der Luft (am Boden ist NIC durch Abschattung oft schlecht)
MIN_ALT_FT = 1000

# H3-Auflösungen: r5 wird pro Punkt gespeichert (~250 km² Fläche),
# aggregiert wird auf r4 (~1.770 km²), ähnlich zu gpsjam.org
H3_STORE_RES = 5
H3_AGG_RES = 4

# NIC (Navigation Integrity Category) < 7 heißt: Containment-Radius >= 0,2 NM.
# Ein intaktes GPS liefert bei Verkehrsflugzeugen NIC 7-11.
NIC_BAD_BELOW = 7
# Nachwirkung: Viele Flugzeuge melden nach einer Störung noch stundenlang NIC 0,
# auch weit außerhalb der Zone (Empfänger/FMS erholt sich nicht sofort). Eine
# schlechte Phase zählt deshalb nur bis zu dieser Entfernung vom Störungsbeginn –
# grob der Radiohorizont eines Bodenstörsenders gegenüber Reiseflughöhe.
AFTERGLOW_MAX_KM = 500
# Eine schlechte Phase überdauert Empfangslücken (z. B. über Iran/Irak, wo
# adsb.lol kaum Empfänger hat); neu beginnt sie erst nach einer guten Meldung
# oder nach > 3 h Pause (neuer Flugabschnitt)
RUN_GAP_S = 3 * 3600
# Ein Flugzeug gilt in einer Zelle/Stunde als gestört, wenn >= 50 % seiner Meldungen schlecht sind
BAD_SHARE_PER_AIRCRAFT = 0.5
# Mindestverkehr, damit eine Zelle bewertet wird
MIN_AIRCRAFT_HOUR = 3
MIN_AIRCRAFT_DAY = 5
# Ein Störsender trifft alle Flugzeuge in der Zone, Avionik-Fehler oder
# Nachwirkung nur eines -> mindestens 2 betroffene Flugzeuge für "gestört"
MIN_BAD_AIRCRAFT = 2

# Anomalie-Schwellen (Ampel wie bei gpsjam.org: <2 % grün, 2-10 % gelb, >10 % rot)
RATIO_LOW = 0.02
RATIO_HIGH = 0.10
BINOM_ALPHA = 1e-4

# Karte: Ring, wenn eine Wabe nach mind. 12 h ohne Störung (davon mind. 6 h mit
# Daten) die 10 % überschreitet und in den 6 Stunden danach mind. 3-mal gestört bleibt
FLARE_QUIET_H = 12
FLARE_MIN_VALID_H = 6
FLARE_AFTER_H = 6
FLARE_AFTER_MIN = 3

# Karte: Detailansicht mit einzelnen Flugzeugen (Export aus Silver)
DETAIL_NAME = "Ostsee"
DETAIL_HOURS = 2
DETAIL_DAY_HOURS = (6, 20)  # Tagesverkehr, UTC
DETAIL_DATA_BOX = dict(lat0=50.0, lat1=64.0, lon0=5.0, lon1=36.0)
DETAIL_VIEW_BOX = dict(lat0=52.0, lat1=62.0, lon0=8.0, lon1=32.0)
DETAIL_MIN_STEP_S = 20    # Punkte ausdünnen, Zustandswechsel bleiben erhalten
DETAIL_MAX_GAP_S = 300    # größere Lücken trennen die Spur
DETAIL_MAX_SPEED_KMH = 1400  # unplausible Sprünge trennen die Spur
