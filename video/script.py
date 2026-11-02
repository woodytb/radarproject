"""Sprechtext und Szenenfolge des Erklärvideos.

Jede Szene (Shot) hat ein Bild und eine Liste von Sätzen. Die Sätze werden
einzeln vertont; daraus ergeben sich Szenenlänge und Untertitel-Zeiten.
SPOKEN ersetzt Abkürzungen, die die Sprachausgabe sonst falsch liest – die
Untertitel zeigen den Originaltext.
"""

SHOTS = [
    dict(id="intro", visual="html", lines=[
        "GPS-Störradar Europa.",
        "Ein Big-Data-Projekt zu einer Frage, die gerade sehr aktuell ist: Wo in Europa wird GPS gestört?",
        "Und kann man das mit frei verfügbaren Daten selbst messen?",
    ]),
    dict(id="frage", visual="html", lines=[
        "Störsender legen den GPS-Empfang in ganzen Regionen lahm.",
        "Für Piloten heißt das: Satellitengestützte Anflüge und Warnsysteme fallen aus, die Crew muss auf Ersatzverfahren umsteigen.",
        "Schiffe auf der Ostsee und dem Schwarzen Meer trifft es genauso.",
        "Und es ist sicherheitspolitisch relevant, wo und wann gestört wird. Ein öffentliches, datengestütztes Lagebild dazu gibt es kaum.",
    ]),
    dict(id="idee", visual="html", lines=[
        "Unsere Idee: Wir nutzen Verkehrsflugzeuge als Messfühler.",
        "Jedes Flugzeug sendet per ADS-B etwa jede Sekunde seine Position, unverschlüsselt auf 1090 Megahertz.",
        "Dazu kommt ein Wert namens NIC, die Navigation Integrity Category. Er sagt, wie sicher sich das Flugzeug über seine eigene Position ist.",
        "Freiwillige empfangen diese Signale mit günstigen Antennen. Die Plattform adsb.lol sammelt sie und veröffentlicht jeden Tag ein komplettes Archiv.",
    ]),
    dict(id="nic", visual="html", lines=[
        "So sieht das Signal aus.",
        "Im normalen Luftraum haben 85 Prozent der Meldungen den Wert NIC 8, nur gut ein Prozent NIC 0.",
        "In gestörten Gebieten springt der Anteil von NIC 0 auf über zehn Prozent.",
        "Ein einzelnes Flugzeug mit schlechtem Wert kann einen Defekt haben. Wenn aber viele Flugzeuge am selben Ort gleichzeitig betroffen sind, steht dort sehr wahrscheinlich ein Störsender.",
    ]),
    dict(id="crispdm", visual="html", lines=[
        "Vorgegangen sind wir nach dem Vorgehensmodell CRISP-DM.",
        "Von der Fragestellung über das Verstehen und Aufbereiten der Daten bis zu Modell, Bewertung und Auslieferung.",
        "Wichtig war dabei nicht die Reihenfolge, sondern die Rücksprünge. Sechs Mal hat uns eine Bewertung zurück in eine frühere Phase geschickt.",
    ]),
    dict(id="daten", visual="html", lines=[
        "Damit wird es ein Big-Data-Problem.",
        "Für acht Tage im Oktober 2026 sind das 609.000 Flugspuren mit 860 Millionen Positionsmeldungen.",
        "Komprimiert 23 Gigabyte, entpackt rund 152 Gigabyte JSON. Das passt nicht in den Arbeitsspeicher eines Laptops.",
        "Die eigentliche Leistung ist die Verdichtung: von 860 Millionen Meldungen auf gut 600.000 Zellen-Stunden, mit denen die Statistik dann arbeitet.",
    ]),
    dict(id="pipeline", visual="html", lines=[
        "Die Pipeline hat drei Stufen.",
        "Bronze sind die Rohdaten, so wie adsb.lol sie liefert.",
        "In der Silver-Stufe liest Dask achtzigtausend Dateien pro Tag parallel ein, filtert Bodenmeldungen heraus und ordnet jede Meldung einer Wabe im H3-Gitter zu. Ein Tag dauert so etwa 35 Sekunden.",
        "H3 teilt die Erde in gleich große Sechsecke. Jede Wabe ist hier etwa 1770 Quadratkilometer groß und hat genau sechs Nachbarn.",
        "In der Gold-Stufe wird pro Wabe und Stunde gezählt: wie viele Flugzeuge, und wie viele davon gestört.",
        "Diese Aggregation haben wir zweimal gebaut, mit Dask und mit Spark. Beide liefern Zeile für Zeile dasselbe Ergebnis.",
    ]),
    dict(id="nachwirkung", visual="html", lines=[
        "Die spannendste Entdeckung kam bei der Datenqualität.",
        "Die erste Karte zeigte nachts orange Streifen quer über Mitteleuropa. Das sah aus wie Flugrouten, nicht wie Störsender.",
        "Die Ursache: Langstreckenflieger werden nahe dem Schwarzen Meer oder über der Ostsee gestört, und ihr Empfänger erholt sich nicht sofort.",
        "Eine Maschine meldete danach noch eine Stunde und vierzig Minuten lang NIC 0, bis über die Adria.",
        "Deshalb zählt eine Störung nur bis 500 Kilometer nach ihrem Beginn. Das betrifft weniger als ein halbes Prozent der Meldungen, entfernt nachts aber rund 40 Prozent der roten Zellen.",
    ]),
    dict(id="methode", visual="html", lines=[
        "Wann ist eine Wabe wirklich gestört? Das entscheidet ein Binomialtest.",
        "Auch ohne Störsender melden etwa drei von tausend Flugzeugen schlechte Werte. Das ist das normale Rauschen.",
        "Der Test fragt: Wie wahrscheinlich ist es, durch reinen Zufall so viele gestörte Flugzeuge zu sehen?",
        "Bei sechs von dreißig liegt diese Wahrscheinlichkeit bei etwa drei zu zehn Milliarden. Das ist kein Zufall mehr.",
        "Zusätzlich verlangen wir mindestens zehn Prozent und mindestens zwei betroffene Flugzeuge. Benachbarte gestörte Waben verbinden wir dann zu Störzonen.",
    ]),
    dict(id="karte", visual="map", lines=[
        "Und so sieht das Ergebnis aus: acht Tage, Stunde für Stunde.",
        "Jede Wabe zeigt den Anteil der Flugzeuge, deren GPS-Integrität zusammenbricht.",
        "Die Ostsee rund um Kaliningrad ist fast durchgehend gestört, ebenso das Schwarze Meer.",
        "Dazu kommen die Grenze von Polen und Rumänien zu Belarus und der Ukraine, die Osttürkei, Zypern und der Libanon.",
        "Die Linie im Osten ist eigentlich der Rand größerer Zonen. Dahinter fliegt kaum jemand, deshalb sehen wir dort nichts.",
        "Unten läuft die gestörte Fläche mit. Sie pulsiert, bleibt aber über die ganze Woche hoch.",
    ]),
    dict(id="regionen", visual="html", lines=[
        "Im Schnitt sind jeden Tag 479.000 Quadratkilometer gestört. Das ist mehr als die Fläche Deutschlands.",
        "Die größte Region ist die Ostsee mit dem Baltikum, mit rund 190.000 Quadratkilometern pro Tag, gefolgt vom Schwarzen Meer.",
        "Die Lage ist über alle acht Tage erstaunlich stabil.",
    ]),
    dict(id="tagesgang", visual="html", lines=[
        "Auffällig ist der Tagesgang: Nachts um eins sind in den gestörten Waben 35 Prozent der Flugzeuge betroffen, morgens um sieben nur 13 Prozent.",
        "Dass nachts stärker gestört wird, ist damit aber nicht bewiesen. Nachts fliegen vor allem Langstrecken durch die Störgebiete, der Verkehrsmix ändert sich mit.",
    ]),
    dict(id="grenzen", visual="html", lines=[
        "Die Methode hat klare Grenzen.",
        "Wir sehen nur, wo geflogen wird. NIC unterscheidet nicht zwischen Stören und Fälschen. Und der Mittelpunkt einer Zone ist kein Senderstandort.",
        "Als nächste Schritte bieten sich ein Live-Radar mit Spark Streaming, die Erkennung gefälschter Positionen und längere Zeiträume auf einem Cluster an.",
    ]),
    dict(id="fazit", visual="html", lines=[
        "Unser Fazit: Offene Flugdaten reichen aus, um Störsender über Europa sichtbar zu machen.",
        "Die Rechenarbeit erledigen Dask und Spark. Die eigentliche Arbeit steckt in der Datenqualität.",
        "Danke fürs Zuschauen.",
    ]),
]

# Aussprache-Hilfen für die Sprachausgabe (nur gesprochen, nicht in den Untertiteln)
SPOKEN = {
    "adsb.lol": "A D S B punkt loll",
    "ADS-B": "A D S B",
    "CRISP-DM": "Crisp D M",
    "NIC 0": "Nick null",
    "NIC 8": "Nick acht",
    "NIC": "Nick",
    "H3": "H drei",
    "JSON": "Jäi-son",
    "1090 Megahertz": "zehn-neunzig Megahertz",
    "1770": "siebzehnhundertsiebzig",
}


def spoken(text: str) -> str:
    for k, v in SPOKEN.items():
        text = text.replace(k, v)
    return text
