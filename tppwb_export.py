#!/usr/bin/env python3
"""
TPPWB-Exporter v6
Wie v5, aber mit hartem Zeitbudget: bricht nach 4 Minuten Endpunkt-Suche ab
und schreibt, was bis dahin gefunden wurde.
"""

import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

BASE = "https://tennis.tppwb.be"
OUT = Path("export")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

ZEITBUDGET = 240          # Sekunden für die Endpunkt-Suche
MAX_ANFRAGEN = 120        # Obergrenze an Testanfragen
TIMEOUT = 12              # Sekunden pro Anfrage

ergebnis = {
    "zeit": datetime.now(timezone.utc).isoformat(),
    "version": 6,
    "status": "nicht_gestartet",
}


def schreiben():
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(
        json.dumps(ergebnis, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[GESCHRIEBEN] Status: {ergebnis['status']}")


def einloggen(s, num, pin):
    from bs4 import BeautifulSoup

    r = s.get(f"{BASE}/MyAFT/Home/Login", timeout=45)
    soup = BeautifulSoup(r.text, "html.parser")
    versteckt = {
        i["name"]: i.get("value", "")
        for i in soup.select("input[type=hidden]")
        if i.get("name")
    }
    s.post(
        f"{BASE}/MyAFT/Home/Login",
        data={
            "affiliationNumber": num,
            "pinCode": pin,
            "rememberMe": "false",
            "returnUrl": versteckt.get("returnUrl", ""),
            "sourcePage": versteckt.get("sourcePage", ""),
        },
        timeout=45,
        headers={"Referer": f"{BASE}/MyAFT/Home/Login", "Origin": BASE},
    )
    pruef = s.get(f"{BASE}/MyAFT/", timeout=45)
    return (num in pruef.text), pruef.text


def kandidaten_sammeln(s, html):
    from bs4 import BeautifulSoup

    muster = r"result|tourno|tournam|classem|ranking|match|palmar|histor"
    gefunden = set()
    quellen = []

    # Inline-JS der Seite
    for m in re.finditer(r"""["'](/MyAFT/[A-Za-z0-9_/\-]+)["']""", html):
        if re.search(muster, m.group(1), re.I):
            gefunden.add(m.group(1))

    soup = BeautifulSoup(html, "html.parser")
    skripte = [
        urljoin(BASE, t["src"])
        for t in soup.find_all("script", src=True)
        if "myaft" in t["src"].lower() or "script" in t["src"].lower()
    ][:12]

    for url in skripte:
        if time.time() - START > 90:
            break
        try:
            r = s.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            quellen.append({"datei": url.rsplit("/", 1)[-1][:60], "laenge": len(r.text)})
            for m in re.finditer(r"""["'](/MyAFT/[A-Za-z0-9_/\-]+)["']""", r.text):
                if re.search(muster, m.group(1), re.I):
                    gefunden.add(m.group(1))
        except Exception:
            continue

    return sorted(gefunden)[:20], quellen


def endpunkte_testen(s, pfade, num):
    treffer = {}
    anfragen = 0
    varianten = [{}, {"numFed": num}]

    for pfad in pfade:
        for params in varianten:
            if anfragen >= MAX_ANFRAGEN or time.time() - START > ZEITBUDGET:
                treffer["_abbruch"] = {
                    "grund": "zeitbudget_oder_limit",
                    "anfragen": anfragen,
                }
                return treffer
            anfragen += 1
            try:
                r = s.get(
                    BASE + pfad,
                    params=params,
                    timeout=TIMEOUT,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": f"{BASE}/MyAFT/",
                    },
                )
            except Exception as e:
                continue

            if r.status_code != 200 or len(r.text) < 40:
                continue

            ct = r.headers.get("content-type", "")
            schluessel = f"GET {pfad} {sorted(params) or '[]'}"
            if "json" in ct:
                try:
                    treffer[schluessel] = {"typ": "json", "daten": r.json()}
                except Exception:
                    treffer[schluessel] = {"typ": "json_kaputt", "auszug": r.text[:1200]}
            elif re.search(r"\d{2}/\d{2}/\d{4}", r.text) and "Responsive menu" not in r.text:
                treffer[schluessel] = {
                    "typ": "html_fragment",
                    "laenge": len(r.text),
                    "auszug": r.text[:2500],
                }

    treffer["_anfragen"] = anfragen
    return treffer


def lauf():
    import requests

    num = os.environ.get("TPPWB_NUM", "").strip()
    pin = os.environ.get("TPPWB_PIN", "").strip()
    if not num or not pin:
        ergebnis["status"] = "secrets_fehlen"
        return

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "fr-BE,fr;q=0.9"})

    ok, html = einloggen(s, num, pin)
    ergebnis["login"] = "ok" if ok else "fehlgeschlagen"
    print("Login:", ergebnis["login"])
    if not ok:
        ergebnis["status"] = "login_fehlgeschlagen"
        return

    pfade, quellen = kandidaten_sammeln(s, html)
    ergebnis["js_dateien"] = quellen
    ergebnis["kandidaten"] = pfade
    print(f"Kandidaten ({len(pfade)}):")
    for p in pfade:
        print("   ", p)

    ergebnis["treffer"] = endpunkte_testen(s, pfade, num)
    echte = [k for k in ergebnis["treffer"] if not k.startswith("_")]
    print(f"Treffer mit Daten: {len(echte)}")
    ergebnis["status"] = "ok" if echte else "keine_endpunkte_gefunden"
    ergebnis["dauer_sekunden"] = round(time.time() - START, 1)


if __name__ == "__main__":
    START = time.time()
    try:
        lauf()
    except Exception:
        ergebnis["status"] = "absturz"
        ergebnis["traceback"] = traceback.format_exc()
        print(ergebnis["traceback"])
    finally:
        ergebnis.setdefault("dauer_sekunden", round(time.time() - START, 1))
        schreiben()
