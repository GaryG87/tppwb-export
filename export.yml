name: TPPWB Export

on:
  schedule:
    # Jede Nacht um 03:17 UTC (05:17 belgischer Sommerzeit)
    - cron: "17 3 * * *"
  workflow_dispatch: {}   # manueller Start über die GitHub-App am Handy

permissions:
  contents: write

jobs:
  export:
    runs-on: ubuntu-latest
    steps:
      - name: Repository auschecken
        uses: actions/checkout@v4

      - name: Python einrichten
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Abhängigkeiten installieren
        run: pip install requests beautifulsoup4

      - name: Export ausführen
        env:
          TPPWB_NUM: ${{ secrets.TPPWB_NUM }}
          TPPWB_PIN: ${{ secrets.TPPWB_PIN }}
        run: python tppwb_export.py
        continue-on-error: true

      - name: Ergebnis committen
        run: |
          git config user.name "tppwb-bot"
          git config user.email "bot@users.noreply.github.com"
          git add export/results.json
          # Debug-Dateien NICHT committen (können Session-Details enthalten)
          git diff --cached --quiet || git commit -m "Export $(date -u +%F)"
          git push
