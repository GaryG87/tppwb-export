#!/usr/bin/env python3
"""
TPPWB-Exporter für Gary
Loggt sich bei tennis.tppwb.be ein und exportiert Turnierergebnisse als JSON.

Zugangsdaten kommen aus Umgebungsvariablen:
  TPPWB_NUM  = Affiliationsnummer (z.B. 4111231)
  TPPWB_PIN  = PIN-Code

Ausgabe: export/results.json (+ Debug-Dateien in export/debug/)
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://tennis.tppwb.be"
OUT = Path("export")
DEBUG = OUT / "debug"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def dump(name: str, content, is_json=False):
    """Debug-Ausgabe speichern, damit wir bei Problemen sehen, was die Seite liefert."""
    DEBUG.mkdir(parents=True, exist_ok=True)
    p = DEBUG / name
    if is_json:
        p.write_text(json.dumps(content, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        p.write_text(str(content)[:200000], encoding="utf-8")


def login(session: requests.Session, num: str, pin: str) -> bool:
    """Login über das MyAFT-Formular. Probiert die bekannten Endpunkt-Varianten durch."""
    # 1. Login-Seite holen (Cookies + eventuelle Hidden-Fields)
    r = session.get(f"{BASE}/MyAFT/Home/Login", timeout=30)
    dump("01_login_page.html", r.text)
    soup = BeautifulSoup(r.text, "html.parser")

    hidden = {
        i.get("name"): i.get("value", "")
        for i in soup.select("input[type=hidden]")
        if i.get("name")
    }

    # Feldnamen aus dem Formular ermitteln (fallback auf übliche Namen)
    kandidaten_felder = [
        {"NumAffiliation": num, "Pin": pin},
        {"numAffiliation": num, "pin": pin},
        {"UserName": num, "Password": pin},
        {"login": num, "password": pin},
    ]

    kandidaten_urls = [
        f"{BASE}/MyAFT/Home/Login",
        f"{BASE}/MyAFT/Home/LogOn",
        f"{BASE}/MyAFT/Account/Login",
    ]

    for url in kandidaten_urls:
        for felder in kandidaten_felder:
            payload = {**hidden, **felder}
            r = session.post(
                url,
                data=payload,
                timeout=30,
                headers={"Referer": f"{BASE}/MyAFT/Home/Login"},
                allow_redirects=True,
            )
            # Erfolgskriterium: wir sind eingeloggt, wenn die MyAFT-Startseite
            # keinen PIN-Dialog mehr verlangt bzw. der Name/Logout auftaucht
            check = session.get(f"{BASE}/MyAFT/", timeout=30)
            if any(
                marker in check.text.lower()
                for marker in ["déconnexion", "logout", "mes inscriptions", "se déconnecter"]
            ) and "code pin" not in check.text.lower():
                dump("02_after_login.html", check.text)
                print(f"[OK] Login erfolgreich über {url} mit Feldern {list(felder)}")
                return True

    dump("02_login_failed_last_response.html", r.text)
    return False


def hole_ergebnisse(session: requests.Session) -> dict:
    """Ergebnisse & Klassierungsdaten von den bekannten MyAFT-Seiten einsammeln."""
    daten = {"abgerufen": datetime.now(timezone.utc).isoformat(), "quellen": {}}

    seiten = {
        "resultate": f"{BASE}/MyAFT/?page=mytournois_myresults",
        "inscriptions": f"{BASE}/MyAFT/",
        "spielerprofil": f"{BASE}/MyAFT/Players/Index",
    }

    for name, url in seiten.items():
        try:
            r = session.get(url, timeout=30)
            dump(f"10_{name}.html", r.text)
            daten["quellen"][name] = {"url": url, "status": r.status_code}
        except Exception as e:
            daten["quellen"][name] = {"url": url, "fehler": str(e)}

    # Die eigentlichen Daten kommen per XHR. Übliche AFTnet-Endpunkte durchprobieren
    # und alles, was JSON zurückgibt, mitnehmen:
    xhr_kandidaten = [
        "/MyAFT/Players/GetMyResults",
        "/MyAFT/Players/MyResults",
        "/MyAFT/Competitions/GetMyResults",
        "/MyAFT/Players/GetPlayerResults",
        "/MyAFT/Players/GetMyTournaments",
        "/MyAFT/Players/GetRankingHistory",
    ]
    daten["xhr"] = {}
    for pfad in xhr_kandidaten:
        try:
            r = session.post(
                BASE + pfad,
                timeout=30,
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            ct = r.headers.get("content-type", "")
            eintrag = {"status": r.status_code, "content_type": ct}
            if "json" in ct and r.status_code == 200:
                try:
                    eintrag["daten"] = r.json()
                    dump(f"20_xhr_{pfad.replace('/', '_')}.json", eintrag["daten"], is_json=True)
                except Exception:
                    eintrag["text"] = r.text[:2000]
            daten["xhr"][pfad] = eintrag
        except Exception as e:
            daten["xhr"][pfad] = {"fehler": str(e)}

    # Fallback: Ergebnisse aus dem HTML der Resultate-Seite parsen
    html = (DEBUG / "10_resultate.html").read_text(encoding="utf-8") if (DEBUG / "10_resultate.html").exists() else ""
    daten["ergebnisse_html_parse"] = parse_resultate_html(html)
    return daten


def parse_resultate_html(html: str) -> list:
    """Turnier-Karten aus dem HTML ziehen (Struktur wie in der mobilen Ansicht)."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    bloecke = []
    # Turnierkarten beginnen mit ORTSNAME TT/MM/JJJJ
    for m in re.finditer(
        r"([A-ZÄÖÜÉÈ][A-ZÄÖÜÉÈ\s\-]+\s\d{2}/\d{2}/\d{4})(.*?)(?=(?:[A-ZÄÖÜÉÈ][A-ZÄÖÜÉÈ\s\-]+\s\d{2}/\d{2}/\d{4})|$)",
        text,
        re.S,
    ):
        kopf, rest = m.group(1).strip(), m.group(2)
        eintrag = {"turnier": kopf}
        kat = re.search(r"(Simples|Doubles)[^\n]+", rest)
        if kat:
            eintrag["kategorie"] = kat.group(0).strip()
        gegner = re.search(r"Contre\s+([^\n]+)", rest)
        if gegner:
            eintrag["gegner"] = gegner.group(1).strip()
        partner = re.search(r"Avec\s+([^\n]+)", rest)
        if partner:
            eintrag["partner"] = partner.group(1).strip()
        score = re.search(r"(\d+/\d+-\d+/\d+-\d+/\d+(?:\s*WO)?)", rest)
        if score:
            eintrag["ergebnis"] = score.group(1).strip()
        bloecke.append(eintrag)
    return bloecke


def main():
    num = os.environ.get("TPPWB_NUM", "").strip()
    pin = os.environ.get("TPPWB_PIN", "").strip()
    if not num or not pin:
        print("[FEHLER] TPPWB_NUM und TPPWB_PIN müssen als Umgebungsvariablen gesetzt sein.")
        sys.exit(1)

    OUT.mkdir(exist_ok=True)
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    if not login(s, num, pin):
        print("[FEHLER] Login fehlgeschlagen. Debug-Dateien liegen in export/debug/.")
        # Trotzdem eine results.json mit Fehlerstatus schreiben, damit der Workflow etwas committet
        (OUT / "results.json").write_text(
            json.dumps(
                {"status": "login_fehlgeschlagen", "zeit": datetime.now(timezone.utc).isoformat()},
                indent=2,
            ),
            encoding="utf-8",
        )
        sys.exit(2)

    daten = hole_ergebnisse(s)
    daten["status"] = "ok"
    (OUT / "results.json").write_text(
        json.dumps(daten, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[OK] Export geschrieben: export/results.json")


if __name__ == "__main__":
    main()
