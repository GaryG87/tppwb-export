#!/usr/bin/env python3
"""
TPPWB-Exporter v3
Login mit ASP.NET-Antiforgery-Token. Protokolliert jeden Versuch samt
Antwort-Auszug in export/results.json, damit Claude direkt mitlesen kann.
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
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def hole_login_kontext(session):
    """Login-Seite laden und Token sowie versteckte Felder einsammeln."""
    r = session.get(f"{BASE}/MyAFT/Home/Login", timeout=30)
    soup = BeautifulSoup(r.text, "html.parser")

    kontext = {"http": r.status_code, "laenge": len(r.text)}

    # Antiforgery-Token kann irgendwo auf der Seite stehen
    token = None
    for inp in soup.select('input[name="__RequestVerificationToken"]'):
        if inp.get("value"):
            token = inp["value"]
            break
    if not token:
        m = re.search(
            r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', r.text
        )
        if m:
            token = m.group(1)
    kontext["token_gefunden"] = bool(token)

    versteckt = {}
    for inp in soup.select("input[type=hidden]"):
        if inp.get("name"):
            versteckt[inp["name"]] = inp.get("value", "")
    kontext["versteckte_felder"] = list(versteckt)
    kontext["cookies"] = list(session.cookies.keys())

    return token, versteckt, kontext


def ist_eingeloggt(session):
    """Prüft anhand mehrerer Merkmale, ob die Session angemeldet ist."""
    r = session.get(f"{BASE}/MyAFT/", timeout=30)
    txt = r.text
    low = txt.lower()
    merkmale = {
        "logout_link": any(
            m in low for m in ["déconnexion", "se déconnecter", "logout", "/home/logout"]
        ),
        "pin_dialog": "pincode" in low or "code pin" in low,
        "mes_inscriptions": "mes inscriptions" in low,
        "mes_resultats": "mytournois_myresults" in low,
        "laenge": len(txt),
    }
    # Eingeloggt, wenn ein Logout-Merkmal auftaucht
    merkmale["bewertung"] = merkmale["logout_link"]
    return merkmale["bewertung"], merkmale, txt


def versuche_login(session, num, pin):
    token, versteckt, kontext = hole_login_kontext(session)
    protokoll = {"kontext": kontext, "versuche": []}

    basis = {
        "affiliationNumber": num,
        "pinCode": pin,
        "rememberMe": "false",
        "returnUrl": versteckt.get("returnUrl", ""),
        "sourcePage": versteckt.get("sourcePage", ""),
    }
    if token:
        basis["__RequestVerificationToken"] = token

    varianten = [
        ("/MyAFT/Home/Login", basis, "form"),
        ("/MyAFT/Home/Login", basis, "json"),
        ("/MyAFT/Home/LoginInfo", basis, "form"),
        ("/MyAFT/Home/LoginInfo", basis, "json"),
        # ohne die Zusatzfelder, nur das Nötigste
        (
            "/MyAFT/Home/Login",
            {k: v for k, v in basis.items() if k in ("affiliationNumber", "pinCode", "__RequestVerificationToken")},
            "form",
        ),
    ]

    for pfad, payload, art in varianten:
        url = BASE + pfad
        eintrag = {"url": pfad, "art": art, "felder": sorted(payload)}
        try:
            kwargs = {
                "timeout": 30,
                "headers": {
                    "Referer": f"{BASE}/MyAFT/Home/Login",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": BASE,
                },
                "allow_redirects": True,
            }
            r = session.post(url, json=payload, **kwargs) if art == "json" else session.post(url, data=payload, **kwargs)
            eintrag["status"] = r.status_code
            eintrag["content_type"] = r.headers.get("content-type", "")
            eintrag["redirects"] = [x.headers.get("location", "") for x in r.history]
            eintrag["antwort_auszug"] = r.text[:800]
            eintrag["cookies_danach"] = list(session.cookies.keys())
        except Exception as e:
            eintrag["fehler"] = str(e)
            protokoll["versuche"].append(eintrag)
            continue

        ok, merkmale, _ = ist_eingeloggt(session)
        eintrag["login_merkmale"] = merkmale
        protokoll["versuche"].append(eintrag)
        print(f"  {'✓' if ok else '✗'} {pfad} [{art}] HTTP {eintrag['status']}")
        if ok:
            protokoll["erfolgreich"] = {"url": pfad, "art": art}
            return True, protokoll

    return False, protokoll


def main():
    num = os.environ.get("TPPWB_NUM", "").strip()
    pin = os.environ.get("TPPWB_PIN", "").strip()
    if not num or not pin:
        print("[FEHLER] TPPWB_NUM und TPPWB_PIN fehlen.")
        sys.exit(1)

    OUT.mkdir(exist_ok=True)
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    print("Login-Versuche laufen ...")
    erfolg, protokoll = versuche_login(s, num, pin)

    ergebnis = {
        "zeit": datetime.now(timezone.utc).isoformat(),
        "status": "ok" if erfolg else "login_fehlgeschlagen",
        "login_protokoll": protokoll,
    }

    if erfolg:
        print("Login OK, hole Ergebnisse ...")
        seiten = {
            "resultate": f"{BASE}/MyAFT/?page=mytournois_myresults",
            "startseite": f"{BASE}/MyAFT/",
        }
        ergebnis["seiten"] = {}
        for name, url in seiten.items():
            r = s.get(url, timeout=30)
            text = BeautifulSoup(r.text, "html.parser").get_text("\n")
            text = re.sub(r"\n{3,}", "\n\n", text)
            ergebnis["seiten"][name] = {
                "http": r.status_code,
                "text": text[:40000],
            }

    (OUT / "results.json").write_text(
        json.dumps(ergebnis, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[FERTIG] Status: {ergebnis['status']}")


if __name__ == "__main__":
    main()
