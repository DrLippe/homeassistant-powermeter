# Stromzähler für Home Assistant

Eine HACS-Integration, die aus einer vorhandenen Smart-Meter-Entität einen virtuellen Stromzähler für Netzbezug und Einspeisung erzeugt und Tages-, Monats- und Abrechnungsjahreswerte bereitstellt.

## Funktionen

- Gerät **Stromzähler** in Home Assistant
- Eingabe des aktuellen physischen Zählerstands für Netzbezug
- optionaler Startwert für das Einspeiseregister (2.8.0)
- Auswahl einer vorhandenen Smart-Meter-Sensorentität
- Unterstützung von `W`, `kW`, `Wh` und `kWh`
- bei vorzeichenbehafteter Leistung:
  - positive Leistung = Netzbezug
  - negative Leistung = Einspeisung
- persistente virtuelle Zählerstände für Netzbezug und Einspeisung
- absolute Werte für Tag, Monat und Abrechnungsjahr
- Mittelwerte:
  - Tag: kWh pro Stunde
  - Monat: kWh pro Tag
  - Abrechnungsjahr: kWh pro Monat
- frei wählbarer Beginn des Abrechnungsjahres, Standard `01.01.`
- deutsche und englische Oberfläche
- Provider-Schnittstelle als Grundlage für die spätere automatische Zählerstandsmeldung an Netzbetreiber und/oder Stromlieferanten

## Voraussetzungen

Die ausgewählte Smart-Meter-Entität muss ein Sensor mit einer der Einheiten `W`, `kW`, `Wh` oder `kWh` sein.

### Leistungsquelle (`W` / `kW`)

Eine vorzeichenbehaftete Leistung ist die vollständigste Quelle. Die Integration integriert die Leistung über die Zeit und trennt Netzbezug und Einspeisung anhand des Vorzeichens.

### Energiequelle (`Wh` / `kWh`)

Bei einem monoton steigenden Energiezähler wird der positive Zuwachs als Netzbezug erfasst. Aus einem reinen kumulativen Verbrauchszähler kann keine Einspeisung abgeleitet werden; der Einspeisungswert bleibt daher unverändert. Ein Zurückspringen des Quellzählers wird als Reset behandelt.

## Entitäten

Für Netzbezug und Einspeisung werden jeweils folgende Entitäten erzeugt:

- virtueller Zählerstand
- heute
- Tagesmittel je Stunde
- aktueller Monat
- Monatsmittel je Tag
- aktuelles Abrechnungsjahr
- Jahresmittel je Monat

Damit entstehen insgesamt 14 Sensoren am Gerät **Stromzähler**.

Die absoluten Energiesensoren verwenden `kWh` und geeignete Home-Assistant-State-Classes für Langzeitstatistiken.

## Einrichtung

1. Repository in HACS als benutzerdefiniertes Repository vom Typ **Integration** hinzufügen.
2. **Stromzähler** installieren.
3. Home Assistant neu starten.
4. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Stromzähler** öffnen.
5. Smart-Meter-Entität auswählen.
6. Aktuellen physischen Zählerstand für Netzbezug eintragen.
7. Optional den aktuellen Einspeisezählerstand eintragen.
8. Beginn des Abrechnungsjahres wählen; Standard ist der 1. Januar.

Der Smart-Meter-Sensor und der physische Zählerstand sollten möglichst zum selben Zeitpunkt abgelesen werden.

## Abrechnungsjahr

Der Beginn des Abrechnungsjahres lässt sich später über die Optionen ändern. Beispiel: Bei Start `01.07.` läuft das Abrechnungsjahr vom 1. Juli bis zum 30. Juni des Folgejahres.

## Persistenz

Die Integration speichert Zähler- und Periodenstände im Home-Assistant-Storage. Ein Neustart setzt die Werte daher nicht zurück. Tages-, Monats- und Jahreszähler werden beim Wechsel in die nächste Periode automatisch auf null gesetzt.

## Geplante Provider-API

Die Struktur `custom_components/stromzaehler/providers/` definiert bereits eine providerunabhängige Schnittstelle für Zählerstandsmeldungen. Konkrete Netzbetreiber- oder Stromlieferanten-Adapter werden in einer späteren Ausbaustufe ergänzt.

## Lizenz

MIT
