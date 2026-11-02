# GPS-Störradar Europa

Big-Data-Projekt: Wo in Europa wird GPS gestört? Verkehrsflugzeuge melden per
ADS-B laufend ihre Position und mit dem **NIC** (Navigation Integrity Category)
auch, wie verlässlich ihr GPS gerade ist. Melden in einer Region gleichzeitig
viele Flugzeuge schlechte Werte, ist dort sehr wahrscheinlich ein Störsender aktiv.

Die Pipeline verarbeitet die öffentlichen Tagesdumps von [adsb.lol](https://adsb.lol)
(Open Database License) und erzeugt eine animierte Karte, auf der die Störzonen
stundenweise „pulsieren“.

Das Vorgehen nach CRISP-DM, mit allen Phasen und Iterationen, ist in
[docs/CRISP-DM.md](docs/CRISP-DM.md) dokumentiert.

## Datengrundlage (15.–28.10.2026)

| | |
|---|---|
| Zeitraum | 14 Tage, lückenlos |
| Rohdaten (Bronze) | 1.055.171 Flugspuren, 40,5 GB gzip ≈ 263 GB JSON |
| Positionsmeldungen gesamt | **1,49 Mrd.** (weltweit) |
| davon mit Qualitätsblock, in der Luft | 274,6 Mio. |
| davon im Untersuchungsgebiet (Silver) | 71,1 Mio. → 1,2 GB Parquet |
| als Nachwirkung markiert | 319.911 |

## Ergebnisse

- Gestört: 4.417 von 86.537 Zellen-Tagen (5,1 %), im Schnitt rund **520.000 km² pro Tag** nach unserer Definition. Mit dem Kriterium von gpsjam.org sind es auf gemeinsamen Zellen rund 317.000 km² (Deutschland: 357.600 km²). Je nach Schwelle reicht unser Wert von 300.000 bis 720.000 km², die Fläche gilt also nur zusammen mit der Definition.
- Größte Regionen (Ø pro Tag): Ostsee & Baltikum 199 Tsd. km², Schwarzes Meer 124, Ostflanke Polen–Rumänien 59, Ostmittelmeer & Levante 51, Kaukasus & Ostanatolien 37, Persischer Golf 19.
- Hintergrundrate schlechter NIC-Werte in ruhigen Zellen: 0,27 %.
- Tagesgang: in gestörten Zellen 34 % betroffene Flugzeuge um 01 Uhr UTC, 14 % um 07 Uhr (Ursache nicht belegt, der Verkehrsmix ändert sich mit).
- Wirkung der Korrekturen auf rote Zellen nachts (14 Nächte): Nachwirkungs-Filter allein −18 %, 2-Flugzeug-Regel allein −34 %, beide −46 %.
- Dask und Spark liefern identische Gold-Tabellen (86.537 Zellen-Tage, 1.056.505 Zellen-Stunden).

## Belastbarkeit

- **Gegenprobe mit [gpsjam.org](https://gpsjam.org/faq)** (`python -m gpsjam.validate_gpsjam`): Regionen-Rang 0,99; 79 % der dortigen Rotzellen finden wir, 47 % unserer Zellen sind dort rot, 83 % rot oder gelb; Cohens κ = 0,58. Die Regionen stimmen, unsere Zonen sind breiter, die Ursache ist offen. Die gpsjam-Dateien werden nur lokal zwischengespeichert (`data/ref/gpsjam/`, nicht im Repository), weil dort keine Lizenz angegeben ist.
- **Empfindlichkeitstest** (`python -m gpsjam.sensitivity prepare`, dann `run`): Rangfolge der Regionen stabil, absolute Fläche stark schwellenabhängig. Details in [docs/CRISP-DM.md](docs/CRISP-DM.md).
- **Tests:** `PYTHONPATH=src .venv/bin/python -m pytest tests`
- **Leistungsvergleich** (`.venv/bin/python scripts/benchmark.py`, Median aus 2 Läufen):

  | Stufe | 1 Kern | 2 Kerne | 4 Kerne | 8 Kerne |
  |---|---:|---:|---:|---:|
  | Einlesen eines Tages (Dask) | 128 s | 64 s | 34 s | 22 s |
  | Aggregation 14 Tage (Spark) | 165 s | 95 s | 57 s | 39 s |
  | Aggregation 14 Tage (Dask) | 1.431 s | 882 s | 533 s | 699 s |


## Architektur

```
adsb.lol (GitHub Releases, ~3 GB/Tag)
   │  ingest_adsblol.py   fortsetzbarer Download, Tar-Stream, nur traces/
   ▼
Bronze  data/raw/<tag>/trace_full_*.json       (gzip-JSON, 1 Datei je Flugzeug & Tag)
   │  parse_traces.py     Dask (Prozess-Pool, 400 Dateien je Task)
   │                      NIC/NACp extrahieren, Bodenmeldungen raus, Europa-BBox,
   │                      H3 r5 je Punkt, Nachwirkungs-Flag je Spur
   ▼
Silver  data/silver/positions/date=<tag>/*.parquet
   │  aggregate.py        Dask LocalCluster  ─┐  identische Logik,
   │  spark_aggregate.py  PySpark (Cluster)  ─┘  Ergebnis wird abgeglichen
   ▼
Gold    data/gold/hex_hour.parquet, hex_day.parquet
   │  detect.py           Binomialtest, robuster z-Score, Zonen (H3-Zusammenhang), Regionen
   ▼
        data/gold/*_scored.parquet, zones_day.parquet, regions_day.parquet, summary.json
   │  build_map.py / charts.py
   ▼
output/stoerkarte.html (eigenständig, 8,8 MB) und output/charts/*.png
```

## Methode

1. **Qualitätssignal**: NIC < 7 heißt Containment-Radius ≥ 0,2 NM. Intaktes GPS liefert bei Verkehrsflugzeugen NIC 7–11.
2. **Transponder-Filter**: Flugzeuge, die an einem Tag nie NIC ≥ 7 melden, haben eher schlechte Avionik als gestörtes GPS und fallen raus. Nur ADS-B Version ≥ 1.
3. **Nachwirkungs-Filter**: Viele Flugzeuge melden nach einer Störung noch stundenlang NIC 0, quer über Europa (Beispiel: eine Maschine aus Bangkok, gestört über dem Schwarzen Meer, 1 h 40 min NIC 0 bis nach Italien). Eine schlechte Phase zählt daher nur bis 500 km nach ihrem Beginn, etwa dem Radiohorizont eines Bodenstörsenders gegenüber Reiseflughöhe. Die Phase überdauert Empfangslücken (z. B. über Iran/Irak), deshalb wird das Flag pro vollständiger Spur in der Silver-Stufe berechnet.
4. **Geo-Binning**: H3-Hexagone, Auflösung 4 (~1.770 km²). Pro Zelle und Stunde bzw. Tag: Zahl der Flugzeuge, Zahl der gestörten Flugzeuge (≥ 50 % ihrer Meldungen in der Zelle schlecht), Anteil.
5. **Räumlicher Detektor**: Binomialtest gegen die Hintergrundrate p0 (gepoolter Anteil der ruhigsten 90 % der Zellen, ~0,3 %). Gestört = Anteil ≥ 10 %, ≥ 2 betroffene Flugzeuge, p < 10⁻⁴.
6. **Zeitlicher Detektor**: robuster z-Score (Median/MAD) des Tagesanteils gegen den Verlauf derselben Zelle. Findet aufflammende Störungen.
7. **Zonen**: benachbarte gestörte Zellen eines Tages werden zu Störzonen verbunden (Zusammenhangskomponenten im H3-Gitter) und nach dem nächstgelegenen Ort benannt. Für Zeitreihen gibt es zusätzlich feste Großregionen.

## Ausführen

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run_pipeline.sh 2026-10-15 2026-10-28            # Dask
SPARK=1 ./run_pipeline.sh 2026-10-15 2026-10-28    # zusätzlich Spark + Abgleich
open output/stoerkarte.html
```

Für Spark wird ein JDK gebraucht. Ohne systemweites Java legt
`.venv/bin/python -c "import jdk; jdk.install('17', path='.jdk')"` eines im Projekt ab.
Ein Tag braucht ~3 GB Download und 22 s Einlesen auf 8 Kernen.

Einzelne Stufen: `PYTHONPATH=src .venv/bin/python -m gpsjam.<modul>` mit
`ingest_adsblol`, `parse_traces`, `aggregate`, `spark_aggregate`, `detect`,
`build_map`, `charts`. Alle Schwellen stehen in [src/gpsjam/config.py](src/gpsjam/config.py).

## Karte

- **Gesamtkarte:** Anteil gestörter Flugzeuge je Wabe, Stunde für Stunde über 14 Tage. Ein Ring zeigt eine Wabe, die nach mindestens 12 h ohne Störung die 10 % überschreitet und in den 6 Stunden danach mindestens 3-mal gestört bleibt (774 Ereignisse).
- **Detailansicht „Flugzeuge“** (Knopf in der Zeitleiste oder Direktlink `stoerkarte.html#ostsee`): echte Flugspuren über der Ostsee, 1 Stunde läuft in 30 Sekunden. Blau = normal, orange = gestört, hohl mit gestrichelter Spur = Nachwirkung. Gezeigt wird automatisch das Zwei-Stunden-Fenster am Tag mit den meisten betroffenen Flugzeugen (16.10., 13–15 Uhr UTC, 197 Flugzeuge), also bewusst kein Durchschnitt. Die Flugzeugkennungen werden nicht exportiert. Zwischen zwei Meldungen wird die Position linear interpoliert.

## Erklärvideo

**Veraltet:** Das Video zeigt den früheren 8-Tage-Stand ohne die späteren Korrekturen, insbesondere die falsche Aussage, der Nachwirkungs-Filter entferne allein rund 40 % der roten Zellen. Für die Präsentation nicht verwenden.

[output/video/gps-stoerradar.mp4](output/video/gps-stoerradar.mp4) (6:46 min, 1080p, deutsche
Sprachausgabe, Untertitel als Spur und als [.srt](output/video/gps-stoerradar.srt)). Neu bauen:

```bash
.venv/bin/python video/build_video.py            # Stimme "Anna", 158 Wörter/min
.venv/bin/python video/build_video.py --rate 150 # langsamer gesprochen
```

Sprechtext und Szenenfolge stehen in [video/script.py](video/script.py), die Szenenbilder in
[video/scenes.py](video/scenes.py), die Kartenanimation in [video/map_frames.py](video/map_frames.py).
Benötigt macOS (`say`) und Google Chrome; ffmpeg kommt über `imageio-ffmpeg` aus der venv.

## Grenzen

- Sichtbar ist nur, wo geflogen wird und Empfänger stehen. Über Russland, Belarus und der Ukraine gibt es kaum Verkehr und keine Empfänger. Deshalb erscheint die Ostflanke als „Linie“ entlang der Grenze.
- NIC unterscheidet nicht zwischen Jamming (Störung) und Spoofing (gefälschte Position). Spoofing zeigt sich zusätzlich an unplausiblen Positionssprüngen; das ist ein Ausbau-Schritt.
- Der Zonenschwerpunkt ist kein Senderstandort: er ist der Schwerpunkt der betroffenen Flugzeuge.
- In der Stundenkarte bleiben vereinzelt Nachtstreifen über Mitteleuropa Die 8 mitteleuropäischen Zellen-Tage des Tagesdetektors bestätigt gpsjam.org nicht, sie sind wahrscheinlich Fehlalarme. Die Ursache ist nicht belegt, vermutlich Nachwirkung mit unbekanntem Störbeginn.
- Der Tagesgang ist vom Verkehrsmix überlagert (nachts mehr Langstrecke über Schwarzes Meer und Ostsee).
