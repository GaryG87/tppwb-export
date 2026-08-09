#!/usr/bin/env python3
"""
TPPWB-Exporter v5
Login funktioniert (POST /MyAFT/Home/Login, Formularfelder).
Diese Version sucht die internen AJAX-Endpunkte, über die die
Turnierergebnisse nachgeladen werden, und probiert sie durch.
"""

import json
import os
import re
import sys
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

ergebnis = {
    "zeit": datetime.now(timezone.utc).isoformat(),
    "version": 5,
    "status": "nicht_gestartet",
}


def schreiben():
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(
        json.dumps(ergebnis, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[GESCHRIEBEN] Status: {ergebnis['status']}")


def einloggen(s, num, pin):
    """Bekannter funktionierender Weg aus v3."""
    from bs4 import BeautifulSoup

    r = s.get(f"{BASE}/MyAFT/Home/Login", timeout=45)
    soup = BeautifulSoup(r.text, "html.parser")
    versteckt = {
        i["name"]: i.get("value", "")
        for i in soup.select("input[type=hidden]")
        if i.get("name")
    }
    payload = {
        "affiliationNumber": num,
        "pinCode": pin,
        "rememberMe": "false",
        "returnUrl": versteckt.get("returnUrl", ""),
        "sourcePage": versteckt.get("sourcePage", ""),
    }
    s.post(
        f"{BASE}/MyAFT/Home/Login",
        data=payload,
        timeout=45,
        headers={"Referer": f"{BASE}/MyAFT/Home/Login", "Origin": BASE},
    )
    pruef = s.get(f"{BASE}/MyAFT/", timeout=45)
    return (num in pruef.text), pruef.text


def js_endpunkte_finden(s, html):
    """Lädt die eingebundenen JS-Dateien und extrahiert MyAFT-URLs daraus."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    skripte = []
    for tag in soup.find_all("script", src=True):
        src = tag["src"]
        if "myaft" in src.lower() or "script" in src.lower():
            skripte.append(urljoin(BASE, src))

    gefunden = {}
    geladen = []
    for url in skripte[:25]:
        try:
            r = s.get(url, timeout=45)
            if r.status_code != 200:
                continue
            geladen.append({"url": url, "laenge": len(r.text)})
            for m in re.finditer(r"""["'](/MyAFT/[A-Za-z0-9_/\-]+)["']""", r.text):
                pfad = m.group(1)
                if re.search(
                    r"result|tourno|tournam|classem|ranking|match|palmar|histor",
                    pfad,
                    re.I,
                ):
                    gefunden.setdefault(pfad, []).append(url.rsplit("/", 1)[-1][:40])
        except Exception:
            continue

    # Auch das Inline-JS der Seite durchsuchen
    for m in re.finditer(r"""["'](/MyAFT/[A-Za-z0-9_/\-]+)["']""", html):
        pfad = m.group(1)
        if re.search(r"result|tourno|tournam|classem|ranking|match|palmar|histor", pfad, re.I):
            gefunden.setdefault(pfad, []).append("inline")

    return gefunden, geladen


def endpunkte_testen(s, pfade, num):
    """Ruft jeden Kandidaten auf und merkt sich, was JSON zurückliefert."""
    treffer = {}
    params_varianten = [
        {},
        {"numFed": num},
        {"affiliationNumber": num},
        {"idPlayer": num},
        {"numFed": num, "periode": "current"},
    ]
    for pfad in sorted(pfade):
        for params in params_varianten:
            for methode in ("get", "post"):
                try:
                    fn = s.get if methode == "get" else s.post
                    kw = {
                        "timeout": 30,
                        "headers": {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/"},
                    }
                    r = fn(BASE + pfad, params=params, **kw) if methode == "get" else fn(BASE + pfad, data=params, **kw)
                    ct = r.headers.get("content-type", "")
                    if r.status_code == 200 and len(r.text) > 40:
                        schluessel = f"{methode.upper()} {pfad} {sorted(params)}"
                        eintrag = {"content_type": ct[:50], "laenge": len(r.text)}
                        if "json" in ct:
                            try:
                                eintrag["json"] = r.json()
                            except Exception:
                                eintrag["auszug"] = r.text[:1500]
                        else:
                            # HTML-Fragmente sind ebenfalls interessant
                            if re.search(r"\d{2}/\d{2}/\d{4}", r.text):
                                eintrag["auszug"] = r.text[:3000]
                                eintrag["hinweis"] = "enthaelt Datumsangaben"
                            else:
                                continue
                        treffer[schluessel] = eintrag
                except Exception:
                    continue
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
    print(f"Login: {ergebnis['login']}")
    if not ok:
        ergebnis["status"] = "login_fehlgeschlagen"
        return

    pfade, geladen = js_endpunkte_finden(s, html)
    ergebnis["js_dateien"] = geladen
    ergebnis["kandidaten"] = {k: sorted(set(v)) for k, v in pfade.items()}
    print(f"Kandidaten gefunden: {len(pfade)}")
    for p in sorted(pfade):
        print("   ", p)

    ergebnis["treffer"] = endpunkte_testen(s, pfade, num)
    print(f"Treffer mit Daten: {len(ergebnis['treffer'])}")

    ergebnis["status"] = "ok" if ergebnis["treffer"] else "keine_endpunkte_gefunden"


if __name__ == "__main__":
    try:
        lauf()
    except Exception:
        ergebnis["status"] = "absturz"
        ergebnis["traceback"] = traceback.format_exc()
        print(ergebnis["traceback"])
    finally:
        schreiben()
