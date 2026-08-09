# TPPWB-Export

Holt jede Nacht automatisch die Turnierergebnisse von tennis.tppwb.be
und legt sie als `export/results.json` in dieses Repository.
Claude liest diese Datei dann direkt – keine Screenshots mehr nötig.

## Einrichtung (einmalig, am Laptop, ca. 15 Minuten)

### 1. Repository anlegen
1. Auf github.com einloggen → oben rechts **+** → **New repository**
2. Name: `tppwb-export`
3. Sichtbarkeit: **Public** (nötig, damit Claude die JSON lesen kann –
   deine Ergebnisse sind auf der TPPWB-Seite ohnehin öffentlich, der PIN
   landet hier NIE im Code)
4. Haken bei **Add a README file** → **Create repository**

### 2. Die drei Dateien hochladen
1. Im neuen Repository: **Add file** → **Upload files**
2. Diese Dateien hineinziehen:
   - `tppwb_export.py`
   - `README.md` (diese Datei, ersetzt die automatisch erstellte)
3. **Commit changes**
4. Für den Workflow: **Add file** → **Create new file**
   - Als Dateiname eingeben: `.github/workflows/export.yml`
     (die Schrägstriche erzeugen automatisch die Ordner)
   - Inhalt aus `export.yml` hineinkopieren → **Commit changes**

### 3. Zugangsdaten als Secrets hinterlegen
1. Im Repository: **Settings** → **Secrets and variables** → **Actions**
2. **New repository secret**:
   - Name: `TPPWB_NUM` → Wert: deine Affiliationsnummer
3. Nochmal **New repository secret**:
   - Name: `TPPWB_PIN` → Wert: dein PIN
4. Fertig. Die Secrets sind verschlüsselt und für niemanden einsehbar,
   auch nicht für Claude.

### 4. Ersten Lauf starten
1. Reiter **Actions** → links **TPPWB Export** → **Run workflow**
2. Nach 1–2 Minuten erscheint ein grüner Haken (oder ein rotes X –
   dann Claude Bescheid geben, siehe unten)
3. Prüfen: im Repository sollte jetzt `export/results.json` liegen

## Nutzung vom Handy

- **Automatisch:** läuft jede Nacht um ca. 5:17 Uhr.
- **Manuell:** GitHub-App öffnen → Repository → Actions →
  TPPWB Export → Run workflow. Dauert 2 Minuten.
- **In Claude:** einfach fragen „Wie steht's mit meinen Tennispunkten?" –
  Claude holt sich die Datei von
  `https://raw.githubusercontent.com/DEIN-BENUTZERNAME/tppwb-export/main/export/results.json`

## Wenn etwas schiefgeht

Der erste Lauf wird vermutlich nicht sofort klappen – die
TPPWB-Login-Endpunkte sind nirgends dokumentiert, das Skript probiert
mehrere Varianten durch. In dem Fall:

1. Im Actions-Log nachsehen, was `[FEHLER]` meldet
2. Das Log (oder einen Screenshot davon) an Claude schicken
3. Claude passt das Skript an → neue Version hochladen → nochmal laufen lassen

Erfahrungsgemäß braucht es 1–2 solcher Runden (wie beim Yazio-Exporter).

## Sicherheit

- PIN und Nummer liegen NUR in den GitHub Secrets, nie im Code, nie im Chat
- Die Debug-Dateien in `export/debug/` werden bewusst NICHT committet
- Öffentlich sichtbar ist nur `results.json` – also dieselben
  Turnierergebnisse, die auf tennis.tppwb.be sowieso jeder einsehen kann
