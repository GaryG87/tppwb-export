#!/usr/bin/env python3
"""
TPPWB-Exporter v7 – Produktionsversion

Endpunkte (aus HAR-Analyse):
  Login      POST /MyAFT/Home/Login          (affiliationNumber, pinCode, ...)
  Ergebnisse GET  /MyAFT/MyAFT/MyResultsPage
  Tableau    GET  /MyAFT/Competitions/TournamentDraw?idTournoi=&idCategory=&drawType=F
  Kategorien GET  /MyAFT/Tooltip/TournamentCategories/{id}?displaySubscriptions=True
"""

import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://tennis.tppwb.be"
OUT = Path("export")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

ergebnis = {
    "zeit": datetime.now(timezone.utc).isoformat(),
    "version": 9,
    "status": "nicht_gestartet",
}


def schreiben():
    OUT.mkdir(exist_ok=True)
    (OUT / "results.json").write_text(
        json.dumps(ergebnis, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[GESCHRIEBEN] Status: {ergebnis['status']}")


def entschaerfen(s):
    """HTML-Entities auflösen und Whitespace normalisieren."""
    import html as htmlmod

    return re.sub(r"\s+", " ", htmlmod.unescape(s or "")).strip()


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
    return num in pruef.text


def abschnitt_fuer(pos, marker):
    """Ordnet eine Fundstelle dem passenden Ergebnisblock zu."""
    passend = None
    for name, p in marker:
        if p <= pos:
            passend = name
        else:
            break
    return passend or "unbekannt"


def ergebnisse_parsen(txt):
    """Parst die Turnierkarten aus MyResultsPage."""
    # Container-Marker in Reihenfolge ihres Auftretens
    marker = []
    for m in re.finditer(r'id="divMyResults(\w+)"', txt):
        marker.append((m.group(1), m.start()))
    marker.sort(key=lambda x: x[1])

    eintraege = []
    for m in re.finditer(r'<dl class="grid-data-item">(.*?)</dl>', txt, re.S):
        blk, pos = m.group(1), m.start()
        kontext = abschnitt_fuer(pos, marker)

        t = re.search(r"Tournoi ([A-ZÄÖÜÉÈ\-\.\' ]+) le (\d{2}/\d{2}/\d{4})", blk)
        kat = re.search(r"<dd>\s*((?:Simples|Doubles)[^<\r\n]*)", blk)
        score = re.search(r"<dd>\s*(\d+/\d+-\d+/\d+-\d+/\d+[^<]*)</dd>", blk)
        draw = re.search(r"TournamentDraw\?([^\"']+)", blk)

        # Gegner: alle Spielerlinks mit Punktangabe
        gegner = [
            entschaerfen(g)
            for g in re.findall(r'/MyAFT/Players/Detail/\d+"[^>]*title="[^"]*?sur ([^"]+?)"', blk)
        ]
        # Partner steht im Text nach "Avec"
        partner = re.search(r"Avec\s*<a[^>]*>([^<]+)</a>", blk)

        e = {
            "kontext": kontext,
            "turnier": entschaerfen(t.group(1)) if t else None,
            "datum": t.group(2) if t else None,
            "kategorie": entschaerfen(kat.group(1)) if kat else None,
            "score": entschaerfen(score.group(1)) if score else None,
            "gegner": gegner,
            "partner": entschaerfen(partner.group(1)) if partner else None,
        }
        if draw:
            params = dict(
                p.split("=", 1)
                for p in draw.group(1).replace("&amp;", "&").split("&")
                if "=" in p
            )
            e["tableau"] = params
        eintraege.append(e)
    return eintraege


def tableau_groesse(s, idTournoi, idCategory):
    """Ruft das Tableau ab und zählt die Startplätze."""
    try:
        r = s.get(
            f"{BASE}/MyAFT/Competitions/TournamentDraw",
            params={
                "idTournoi": idTournoi,
                "idCategory": idCategory,
                "drawType": "F",
            },
            timeout=30,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/"},
        )
        if r.status_code != 200:
            return {"http": r.status_code}
        txt = r.text
        # Spielernamen im Tableau zählen
        urls = sorted(set(re.findall(r"[\"'](/MyAFT/[A-Za-z0-9_/\-]*[Dd]raw[A-Za-z0-9_/\-]*)[\"'?]", txt)))
        ajax = sorted(set(re.findall(r"url\s*:\s*[\"']([^\"']+)[\"']", txt)))
        spieler = re.findall(r"/MyAFT/Players/Detail/(\d+)", txt)
        return {
            "http": 200,
            "laenge": len(txt),
            "spieler_links": len(set(spieler)),
            "draw_urls": urls,
            "ajax_urls": ajax[:20],
            "auszug": re.sub(r"\s+", " ", txt)[:6000],
        }
    except Exception as e:
        return {"fehler": repr(e)}


def kategorie_infos(s, idTournoi):
    """Tooltip mit Kategorien und Einschreibungszahlen."""
    try:
        r = s.get(
            f"{BASE}/MyAFT/Tooltip/TournamentCategories/{idTournoi}",
            params={"displaySubscriptions": "True"},
            timeout=30,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/"},
        )
        if r.status_code != 200:
            return {"http": r.status_code}
        roh = entschaerfen(re.sub(r"<[^>]+>", " ", r.text))
        kats = []
        for m in re.finditer(
            r"((?:Simples|Doubles)[A-Za-zÀ-ÿ ]*?\d+[^/]{0,40}?)\s*/?\s*(\d+)\s+inscrits", roh
        ):
            kats.append({"kategorie": m.group(1).strip(), "inscrits": int(m.group(2))})
        return {"http": 200, "kategorien": kats, "text": roh[:12000]}
    except Exception as e:
        return {"fehler": repr(e)}



def alles_sammeln(s, num):
    """Holt alle Perioden, Typen und Zusatzseiten."""
    daten = {}

    # --- Ergebnisse je Periode (ordinal 1..9) und je Typ ---
    for ordinal in range(1, 10):
        for typ in ("", "Tournaments", "Interclubs"):
            schluessel = f"ordinal{ordinal}_{typ or 'alle'}"
            try:
                r = s.get(
                    f"{BASE}/MyAFT/MyResults/Results",
                    params={"ordinal": ordinal, "type": typ},
                    timeout=30,
                    headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/"},
                )
                if r.status_code != 200 or len(r.text) < 100:
                    continue
                txt = r.text
                kl = []
                for m in re.finditer(
                    r"(SIMPLES|DOUBLES).{0,220}?Classement (\d{4}):\s*([A-Z0-9.]+).*?>([\d.,]+)\s*pts",
                    txt, re.S,
                ):
                    kl.append({
                        "sparte": m.group(1), "jahr": m.group(2),
                        "klassierung": m.group(3), "punkte": m.group(4),
                    })
                eintraege = ergebnisse_parsen(txt)
                if kl or eintraege:
                    daten[schluessel] = {
                        "periode_label": periode_label(txt),
                        "klassierung": kl,
                        "anzahl": len(eintraege),
                        "ergebnisse": eintraege,
                    }
            except Exception as e:
                daten[schluessel] = {"fehler": repr(e)}

    # --- Weitere Bereiche ---
    seiten = {
        "interclubs": "/MyAFT/MyAFT/MyInterclubsPage",
        "meine_turniere": "/MyAFT/MyAFT/MyTournoiPage",
        "profil": "/MyAFT/MyAFT/MyProfilePage",
    }
    daten["_seiten"] = {}
    for name, pfad in seiten.items():
        try:
            r = s.get(BASE + pfad, timeout=30,
                      headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/"})
            roh = entschaerfen(re.sub(r"<[^>]+>", " ", r.text))
            daten["_seiten"][name] = {"http": r.status_code, "text": roh[:8000]}
        except Exception as e:
            daten["_seiten"][name] = {"fehler": repr(e)}

    # --- Turnierhistorie je Periode ---
    daten["_turnierhistorie"] = {}
    for ordinal in range(1, 6):
        try:
            r = s.get(f"{BASE}/MyAFT/MyTournois/MyTournamentResults",
                      params={"ordinal": ordinal}, timeout=30,
                      headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/"})
            if r.status_code == 200 and len(r.text) > 100:
                daten["_turnierhistorie"][f"ordinal{ordinal}"] = entschaerfen(
                    re.sub(r"<[^>]+>", " ", r.text))[:6000]
        except Exception:
            continue

    return daten


def periode_label(txt):
    m = re.search(r'option value="\d+"\s+selected[^>]*>([^<]+)<', txt)
    if m:
        return entschaerfen(m.group(1))
    m = re.search(r"<option[^>]*>([^<]*Classement[^<]*)</option>", txt)
    return entschaerfen(m.group(1)) if m else None


def lauf():
    import requests

    num = os.environ.get("TPPWB_NUM", "").strip()
    pin = os.environ.get("TPPWB_PIN", "").strip()
    if not num or not pin:
        ergebnis["status"] = "secrets_fehlen"
        return

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "fr-BE,fr;q=0.9"})

    if not einloggen(s, num, pin):
        ergebnis["status"] = "login_fehlgeschlagen"
        return
    print("Login OK")

    r = s.get(
        f"{BASE}/MyAFT/MyAFT/MyResultsPage",
        params={"_": int(time.time() * 1000)},
        timeout=45,
        headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/"},
    )
    txt = r.text
    print(f"Ergebnisseite: HTTP {r.status_code}, {len(txt)} Zeichen")

    # Klassierung und offizieller Punktestand je Sparte
    klassierungen = []
    for m in re.finditer(
        r"(SIMPLES|DOUBLES).{0,200}?Classement (\d{4}):\s*([A-Z0-9.]+).*?>([\d.,]+)\s*pts",
        txt,
        re.S,
    ):
        klassierungen.append(
            {
                "sparte": m.group(1),
                "jahr": m.group(2),
                "klassierung": m.group(3),
                "punkte": m.group(4),
            }
        )
    ergebnis["klassierung"] = klassierungen
    print("Klassierung:", klassierungen)

    eintraege = ergebnisse_parsen(txt)
    ergebnis["ergebnisse"] = eintraege
    print(f"Ergebnisse geparst: {len(eintraege)}")

    # Tableaus und Kategorien je Turnier abrufen
    gesehen = set()
    ergebnis["tableaus"] = {}
    ergebnis["turnier_infos"] = {}
    for e in eintraege:
        tb = e.get("tableau") or {}
        it, ic = tb.get("idTournoi"), tb.get("idCategory")
        if it and ic and (it, ic) not in gesehen:
            gesehen.add((it, ic))
            ergebnis["tableaus"][f"{it}_{ic}"] = tableau_groesse(s, it, ic)
        if it and it not in ergebnis["turnier_infos"]:
            ergebnis["turnier_infos"][it] = kategorie_infos(s, it)

    print("Sammle alle Perioden und Typen ...")
    ergebnis["alle_perioden"] = alles_sammeln(s, num)
    print("Perioden mit Daten:", [k for k in ergebnis["alle_perioden"] if not k.startswith("_")])

    ergebnis["status"] = "ok"


if __name__ == "__main__":
    try:
        lauf()
    except Exception:
        ergebnis["status"] = "absturz"
        ergebnis["traceback"] = traceback.format_exc()
        print(ergebnis["traceback"])
    finally:
        schreiben()
