#!/usr/bin/env python3
"""
TPPWB-Exporter v2 – mit Diagnose-Modus
Gibt die Struktur des Login-Formulars direkt ins Actions-Log aus,
damit der richtige Endpunkt und die Feldnamen ermittelt werden können.
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


def trenner(titel):
    print("\n" + "=" * 70)
    print(f"  {titel}")
    print("=" * 70)


def diagnose(session):
    """Untersucht die Login-Seite und gibt alles Relevante ins Log aus."""
    trenner("DIAGNOSE: Login-Seiten abklopfen")

    kandidaten = [
        "/MyAFT/Home/Login",
        "/MyAFT/",
        "/MyAFT/Players/Search",
    ]

    befunde = {}

    for pfad in kandidaten:
        url = BASE + pfad
        try:
            r = session.get(url, timeout=30)
        except Exception as e:
            print(f"\n--- {pfad}: FEHLER {e}")
            continue

        print(f"\n--- {pfad}: HTTP {r.status_code}, {len(r.text)} Zeichen")
        soup = BeautifulSoup(r.text, "html.parser")

        formulare = soup.find_all("form")
        print(f"    Formulare gefunden: {len(formulare)}")

        for i, f in enumerate(formulare):
            action = f.get("action", "(kein action)")
            method = f.get("method", "get")
            fid = f.get("id", "")
            inputs = f.find_all(["input", "select", "button"])
            # Nur Formulare mit Passwort-/PIN-Feld sind interessant
            hat_pw = any(
                (inp.get("type") == "password")
                or ("pin" in (inp.get("name") or "").lower())
                or ("pin" in (inp.get("id") or "").lower())
                for inp in inputs
            )
            markierung = "  <<< LOGIN-KANDIDAT" if hat_pw else ""
            print(f"    [Form {i}] id={fid!r} action={action!r} method={method}{markierung}")
            for inp in inputs:
                nm = inp.get("name")
                if not nm and not hat_pw:
                    continue
                print(
                    f"        - tag={inp.name} type={inp.get('type')!r} "
                    f"name={nm!r} id={inp.get('id')!r} value={(inp.get('value') or '')[:40]!r}"
                )
            if hat_pw:
                befunde[pfad] = {
                    "action": action,
                    "method": method,
                    "felder": [
                        {"name": inp.get("name"), "type": inp.get("type"), "id": inp.get("id")}
                        for inp in inputs
                        if inp.get("name")
                    ],
                }

        # JS-Hinweise auf AJAX-Login-Endpunkte
        treffer = set()
        for m in re.finditer(r"""url\s*:\s*["']([^"']*[Ll]og[^"']*)["']""", r.text):
            treffer.add(m.group(1))
        for m in re.finditer(r"""["'](/MyAFT/[^"']*(?:Login|LogOn|Authenticate)[^"']*)["']""", r.text):
            treffer.add(m.group(1))
        if treffer:
            print(f"    JS-Hinweise auf Login-URLs: {sorted(treffer)}")
            befunde.setdefault(pfad, {})["js_urls"] = sorted(treffer)

    trenner("DIAGNOSE-ZUSAMMENFASSUNG (dieses Stück an Claude schicken)")
    print(json.dumps(befunde, indent=2, ensure_ascii=False))
    return befunde


def login_versuchen(session, num, pin, befunde):
    """Login mit den in der Diagnose gefundenen Feldnamen versuchen."""
    trenner("LOGIN-VERSUCHE")

    versuche = []

    # 1. Aus der Diagnose gewonnene echte Formulare
    for pfad, info in befunde.items():
        action = info.get("action") or pfad
        if action.startswith("/"):
            action = BASE + action
        elif not action.startswith("http"):
            action = BASE + pfad
        feldnamen = [f["name"] for f in info.get("felder", []) if f.get("name")]
        # Nummer-Feld und PIN-Feld heuristisch zuordnen
        num_feld = next(
            (n for n in feldnamen if re.search(r"num|affil|user|login", n, re.I)), None
        )
        pin_feld = next(
            (n for n in feldnamen if re.search(r"pin|pass|pwd", n, re.I)), None
        )
        if num_feld and pin_feld:
            versuche.append((action, {num_feld: num, pin_feld: pin}))
        for js in info.get("js_urls", []):
            u = BASE + js if js.startswith("/") else js
            if num_feld and pin_feld:
                versuche.append((u, {num_feld: num, pin_feld: pin}))

    # 2. Bekannte Standardvarianten als Fallback
    for u in [
        f"{BASE}/MyAFT/Home/Login",
        f"{BASE}/MyAFT/Home/LogOn",
        f"{BASE}/MyAFT/Home/Authenticate",
    ]:
        for felder in [
            {"NumAffiliation": num, "Pin": pin},
            {"numAffiliation": num, "codePin": pin},
            {"Login": num, "Pin": pin},
            {"UserName": num, "Password": pin},
        ]:
            versuche.append((u, felder))

    for url, payload in versuche:
        for as_json in (False, True):
            try:
                kwargs = {
                    "timeout": 30,
                    "headers": {
                        "Referer": f"{BASE}/MyAFT/Home/Login",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                }
                if as_json:
                    r = session.post(url, json=payload, **kwargs)
                else:
                    r = session.post(url, data=payload, **kwargs)
            except Exception as e:
                continue

            check = session.get(f"{BASE}/MyAFT/", timeout=30)
            low = check.text.lower()
            eingeloggt = (
                any(m in low for m in ["déconnexion", "se déconnecter", "logout"])
                and "code pin" not in low
            )
            kennung = f"{url} | {list(payload)} | {'json' if as_json else 'form'}"
            print(f"  {'✓ ERFOLG' if eingeloggt else '✗'} {kennung} (HTTP {r.status_code})")
            if eingeloggt:
                return True
    return False


def main():
    num = os.environ.get("TPPWB_NUM", "").strip()
    pin = os.environ.get("TPPWB_PIN", "").strip()
    if not num or not pin:
        print("[FEHLER] TPPWB_NUM und TPPWB_PIN fehlen.")
        sys.exit(1)

    OUT.mkdir(exist_ok=True)
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    befunde = diagnose(s)
    erfolg = login_versuchen(s, num, pin, befunde)

    ergebnis = {
        "zeit": datetime.now(timezone.utc).isoformat(),
        "status": "ok" if erfolg else "login_fehlgeschlagen",
        "diagnose": befunde,
    }

    if erfolg:
        trenner("DATEN ABHOLEN")
        r = s.get(f"{BASE}/MyAFT/?page=mytournois_myresults", timeout=30)
        print(f"  Resultate-Seite: HTTP {r.status_code}, {len(r.text)} Zeichen")
        ergebnis["rohtext"] = BeautifulSoup(r.text, "html.parser").get_text("\n")[:60000]

    (OUT / "results.json").write_text(
        json.dumps(ergebnis, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n[FERTIG] Status: {ergebnis['status']}")


if __name__ == "__main__":
    main()
