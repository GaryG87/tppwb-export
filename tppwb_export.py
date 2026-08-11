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
    "version": 24,
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



def anmeldungen_und_teams(s):
    """Nächste Spiele, Turnieranmeldungen und Interclub-Mannschaften."""
    daten = {}
    endpunkte = {
        "naechste_spiele": "/MyAFT/MyTournois/MyNextGamesData",
        "meine_anmeldungen": "/MyAFT/MyTournois/MyRegistrations",
        "turniere_folgen": "/MyAFT/MyTournois/MyTournamentsToFollow",
        "interclub_ergebnisse": "/MyAFT/MyInterclubs/MyResultsData",
        "interclub_teams": "/MyAFT/MyInterclubs/MyTeamsData",
        "interclub_teams2": "/MyAFT/MyInterclubs/MyTeamsToFollowData",
        "belgian_circuit": "/MyAFT/MyTournois/BelgianCircuit",
    }
    for name, pfad in endpunkte.items():
        try:
            r = s.get(
                BASE + pfad,
                timeout=30,
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/"},
            )
            roh = entschaerfen(re.sub(r"<[^>]+>", " ", r.text))
            eintrag = {"http": r.status_code, "laenge": len(r.text)}
            if r.status_code == 200 and len(roh) > 10:
                eintrag["text"] = roh[:6000]
                # Turniernamen und Daten herausziehen
                eintrag["turniere"] = sorted(set(re.findall(
                    r"([A-ZÄÖÜÉÈ][A-ZÄÖÜÉÈ\-\. ]{3,25})\s*(?:le\s*)?(\d{2}/\d{2}/\d{4})", roh
                )))[:40]
                eintrag["ids"] = sorted(set(re.findall(r"idTournoi=(\d+)", r.text)))[:20]
            daten[name] = eintrag
        except Exception as e:
            daten[name] = {"fehler": repr(e)}
    return daten



def turnierkalender(s):
    """Sucht den Endpunkt für den Turnierkalender und ruft ihn ab."""
    from bs4 import BeautifulSoup
    daten = {}

    # Seite "Inscriptions tournois" laden und JS-Endpunkte extrahieren
    try:
        r = s.get(f"{BASE}/MyAFT/Competitions/Tournaments", timeout=45)
        html = r.text
        daten["seite"] = {"http": r.status_code, "laenge": len(html)}
        urls = set()
        for m in re.finditer(r"url\s*:\s*[\"']([^\"']+)[\"']", html):
            urls.add(m.group(1))
        for m in re.finditer(r"[\"'](/MyAFT/[A-Za-z0-9_/\-]*(?:Tourna|Calend|Search)[A-Za-z0-9_/\-]*)[\"'?]", html):
            urls.add(m.group(1))
        soup = BeautifulSoup(html, "html.parser")
        for t in soup.find_all("script", src=True)[:10]:
            src = t["src"]
            if not src.startswith("http"):
                src = BASE + src if src.startswith("/") else BASE + "/" + src
            if "myaft" not in src.lower() and "script" not in src.lower():
                continue
            try:
                rj = s.get(src, timeout=30)
                for m in re.finditer(r"[\"'](/MyAFT/[A-Za-z0-9_/\-]*(?:Tourna|Calend)[A-Za-z0-9_/\-]*)[\"'?]", rj.text):
                    urls.add(m.group(1))
            except Exception:
                pass
        daten["gefundene_urls"] = sorted(urls)[:40]
        # Formularfelder der Suche
        daten["formularfelder"] = sorted({
            i.get("name") for i in soup.select("input[name], select[name]") if i.get("name")
        })[:40]
    except Exception as e:
        daten["seite"] = {"fehler": repr(e)}
        daten["gefundene_urls"] = []

    # Kandidaten durchprobieren
    kandidaten = [u for u in daten.get("gefundene_urls", []) if u.startswith("/MyAFT/")]
    kandidaten += [
        "/MyAFT/Competitions/SearchTournaments",
        "/MyAFT/Competitions/TournamentsData",
        "/MyAFT/Competitions/GetTournaments",
        "/MyAFT/Competitions/TournamentsList",
    ]
    daten["treffer"] = {}
    versuche = 0
    for pfad in list(dict.fromkeys(kandidaten))[:18]:
        for params in ({}, {"regionId": 3}, {"idRegion": 3}):
            if versuche >= 40:
                break
            versuche += 1
            for methode in ("get", "post"):
                try:
                    fn = s.get if methode == "get" else s.post
                    kw = {"timeout": 20, "headers": {
                        "X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/Competitions/Tournaments"}}
                    r = fn(BASE + pfad, params=params, **kw) if methode == "get" else fn(BASE + pfad, data=params, **kw)
                    if r.status_code != 200 or len(r.text) < 200:
                        continue
                    ct = r.headers.get("content-type", "")
                    roh = entschaerfen(re.sub(r"<[^>]+>", " ", r.text))
                    orte = sorted(set(re.findall(r"([A-ZÄÖÜÉÈ][A-ZÄÖÜÉÈ\-\. ]{3,22})\s+(?:du|le)?\s*(\d{2}/\d{2}/\d{4})", roh)))
                    if "json" in ct or orte:
                        daten["treffer"][f"{methode.upper()} {pfad} {sorted(params)}"] = {
                            "content_type": ct[:40],
                            "laenge": len(r.text),
                            "turniere": orte[:60],
                            "auszug": roh[:2500],
                        }
                except Exception:
                    continue
    return daten



def kalender_suche(s, von, bis, plz="4750", region=None, kategorie=None, radius_an=False):
    """Turniersuche. Format dd/mm/yyyy ist bestätigt; Parsen über <dl class='grid-data-item'>."""
    from bs4 import BeautifulSoup

    payload = {
        "periodStartDate": von,
        "periodEndDate": bis,
        "searchmode": "1",
        "searchtext": "",
    }
    if region is not None:
        payload["Regions"] = str(region)
    if kategorie:
        # Zahlenwert der Klassierung, NICHT das Kuerzel. C30.6 = 3
        payload["ddlSingleCategoryValue"] = str(kategorie)
    if radius_an:
        payload["txtZipCode"] = plz
        payload["chkRadius"] = "on"
        payload["Radius"] = "80"
    try:
        r = s.post(
            f"{BASE}/MyAFT/Competitions/TournamentSearchResultData",
            data=payload, timeout=60,
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Referer": f"{BASE}/MyAFT/Competitions/Tournaments"},
        )
        if r.status_code != 200:
            return {"http": r.status_code}

        soup = BeautifulSoup(r.text, "html.parser")
        turniere = []
        for dl in soup.select("dl.grid-data-item"):
            dds = dl.find_all("dd")
            if not dds:
                continue

            kopf = entschaerfen(dds[0].get_text(" "))
            m = re.match(r"(.+?)\s+(\d{2}/\d{2}/\d{4})\s*$", kopf)
            if not m:
                continue
            ort, start_datum = m.group(1).strip(), m.group(2)

            idm = None
            for a in dl.find_all("a", attrs={"data-url": True}):
                mm = re.search(r"Tournament(?:Details|Categories)/(\d+)", a["data-url"])
                if mm:
                    idm = mm.group(1)
                    break

            volltext = entschaerfen(dl.get_text(" "))
            anm = re.search(r"jusqu'au\s+(\d{2}/\d{2}/\d{4})", volltext)

            beschreibung, status = "", ""
            volltext_dl = entschaerfen(dl.get_text(" "))

            # Alle Kategorie-Tokens aus dem gesamten Block einsammeln
            kat_set = set()
            for dd in dds[1:]:
                t = entschaerfen(dd.get_text(" "))
                if t.lstrip().startswith("-"):
                    for tok in re.findall(r"\b([A-Z]{1,4}\d{1,3}[A-Z]*\*?)\b", t):
                        kat_set.add(tok)
                if "CRITERIUM" in t.upper() or "TOURNOI" in t.upper():
                    beschreibung = t[:140]
                if t.strip() in ("En cours", "Terminé", "Ouvert", "Clôturé"):
                    status = t.strip()

            # Anmeldeöffnung je Kategoriegruppe
            oeffnungen = []
            for mm in re.finditer(
                r"-\s*([A-Z0-9,\s\*]{1,60}?)\s*Ouverture des inscriptions le\s*"
                r"(\d{2}/\d{2}/\d{4})\s*à partir de\s*(\d{1,2}:\d{2})",
                volltext_dl,
            ):
                gruppe = [g.strip() for g in mm.group(1).split(",") if g.strip()]
                oeffnungen.append({"kategorien": gruppe, "datum": mm.group(2), "uhrzeit": mm.group(3)})
                for g in gruppe:
                    kat_set.add(g)

            kats = sorted(kat_set)
            turniere.append({
                "id": idm,
                "ort": ort,
                "start": start_datum,
                "anmeldung_bis": anm.group(1) if anm else None,
                "status": status,
                "beschreibung": beschreibung,
                "kategorien": kats,
                "anmeldeoeffnung": oeffnungen,
                "hat_M6": "M6" in kats,
                "hat_M356": "M356" in kats,
            })

        eindeutig = {}
        for t in turniere:
            eindeutig.setdefault(t["id"] or f"{t['ort']}_{t['start']}", t)

        alle = list(eindeutig.values())
        relevant = [t for t in alle if t["hat_M6"] or t["hat_M356"]]
        return {
            "http": 200,
            "anzahl_gesamt": len(alle),
            "anzahl_relevant": len(relevant),
            "turniere": alle[:250],
        }
    except Exception as e:
        return {"fehler": repr(e)}



def adressen_ergaenzen(s, turniere, max_abrufe=45):
    """Holt Adresse und PLZ aus dem Turnier-Tooltip für relevante Turniere."""
    import time as _t
    start = _t.time()
    n = 0
    for t in turniere:
        if n >= max_abrufe or _t.time() - start > 150:
            break
        if not (t.get("hat_M6") or t.get("hat_M356")) or not t.get("id"):
            continue
        n += 1
        try:
            r = s.get(
                f"{BASE}/MyAFT/Tooltip/TournamentDetails/{t['id']}",
                timeout=20,
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/MyAFT/"},
            )
            if r.status_code != 200:
                continue
            roh = entschaerfen(re.sub(r"<[^>]+>", " ", r.text))
            zr = re.search(r"Date:\s*Du\s*(\d{2}/\d{2}/\d{4})\s*au\s*(\d{2}/\d{2}/\d{4})", roh)
            if zr:
                t["zeitraum_von"], t["zeitraum_bis"] = zr.group(1), zr.group(2)
            cl = re.search(r"Club:\s*(.+?)\s+Date:", roh)
            if cl:
                t["klub"] = entschaerfen(cl.group(1))[:60]
            ja = re.search(r"Juge-arbitre:\s*\d+\s*-\s*([A-ZÄÖÜÉÈ][^T]{2,40}?)\s*Tél", roh)
            if ja:
                t["schiedsrichter"] = entschaerfen(ja.group(1))[:50]
            tel = re.search(r"GSM:\s*([0-9/ \.]{8,20})", roh)
            if tel:
                t["kontakt_gsm"] = tel.group(1).strip()
        except Exception:
            continue
    return n



def klub_verzeichnis(s):
    """Klubliste holen. Struktur wird mitprotokolliert, damit sie auswertbar ist."""
    from bs4 import BeautifulSoup
    out = {"treffer": {}, "klubs": {}}
    try:
        r = s.get(f"{BASE}/MyAFT/Clubs/Index", timeout=60,
                  headers={"Referer": f"{BASE}/MyAFT/"})
        out["treffer"]["Index"] = {"http": r.status_code, "laenge": len(r.text)}
        if r.status_code != 200:
            return out
        soup = BeautifulSoup(r.text, "html.parser")

        # Alle Links auf Klub-Detailseiten: /MyAFT/Clubs/Detail/4091-BUTGENBACH
        for a in soup.find_all("a", href=re.compile(r"/Clubs/Detail/")):
            href = a.get("href", "")
            m = re.search(r"/Clubs/Detail/(\d+)-(.+)$", href)
            if not m:
                continue
            code, kurz = m.group(1), m.group(2)
            name = entschaerfen(a.get_text(" ")) or kurz
            umfeld = entschaerfen(a.parent.get_text(" ") if a.parent else "")
            plz = re.search(r"\b(\d{4})\b", umfeld)
            out["klubs"][name.upper()[:40]] = {
                "code": code, "kurz": kurz,
                "plz": plz.group(1) if plz else None,
                "umfeld": umfeld[:120],
            }
        out["treffer"]["links_gefunden"] = len(out["klubs"])
        # Strukturprobe fuer die Fehlersuche
        out["probe"] = re.sub(r"\s+", " ", r.text[:2500])
    except Exception as e:
        out["treffer"]["fehler"] = repr(e)
    return out

def klub_codes(s):
    """Klubcodes aus dem Auswahlfeld der Turnierseite. Der Code verraet die
    Provinz: 4xxx = Luettich, 6xxx = Luxemburg, 1xxx = Brabant/Bruessel usw."""
    from bs4 import BeautifulSoup

    try:
        r = s.get(f"{BASE}/MyAFT/Competitions/Tournaments", timeout=45)
        soup = BeautifulSoup(r.text, "html.parser")
        sel = soup.find("select", id="ddlTournamentsSearchClubs")
        if not sel:
            return {}
        karte = {}
        for o in sel.find_all("option"):
            code = (o.get("value") or "").strip()
            text = entschaerfen(o.get_text())
            if not code or not text:
                continue
            name = re.sub(r"^\d+\s*-\s*", "", text)
            sauber = re.sub(r"\(.*?\)", "", name)
            sauber = re.sub(r"\b(?:R\.?T\.?C\.?|T\.?C\.?|K\.?T\.?C\.?|R\.?C\.?S\.?|A\.?S\.?B\.?L\.?)\b", "", sauber)
            sauber = re.sub(r"[^A-Za-zÀ-ÿ0-9 ]", " ", sauber)
            sauber = re.sub(r"\s+", " ", sauber).strip().upper()
            if sauber:
                karte[sauber] = {"code": code, "voll": name.strip()}
        return karte
    except Exception:
        return {}


PROVINZ = {"1": "Brabant/Bruessel", "2": "Hainaut", "4": "Luettich",
           "5": "Namur", "6": "Luxemburg", "7": "Hainaut"}


def kalender_zeitraum(s, jahr_von, monat_von, monate=9, plz="4750"):
    """Monatsweise Abfrage, gefiltert auf Garys Klassierung (C30.6 = Wert 3).
    Damit bleibt die Treffermenge unter der 100er-Grenze des Servers."""
    import calendar

    alle = {}
    protokoll = []
    j, mo = jahr_von, monat_von
    for _ in range(monate):
        letzter = calendar.monthrange(j, mo)[1]
        for kat in (3, None):   # 3 = C30.6; None = ungefiltert als Kontrolle
            res = kalender_suche(s, f"01/{mo:02d}/{j}", f"{letzter:02d}/{mo:02d}/{j}",
                                 plz, kategorie=kat)
            anz = res.get("anzahl_gesamt", 0)
            protokoll.append({"monat": f"{mo:02d}/{j}",
                              "filter": "C30.6" if kat else "alle",
                              "treffer": anz, "abgeschnitten": anz >= 100})
            for t in res.get("turniere", []):
                schl = t["id"] or f"{t['ort']}_{t['start']}"
                if schl not in alle:
                    t["fuer_c306"] = bool(kat)
                    alle[schl] = t
                elif kat:
                    alle[schl]["fuer_c306"] = True
            if kat and anz and anz < 100:
                break   # gefilterte Abfrage reicht, ungefilterte sparen
        mo += 1
        if mo > 12:
            mo, j = 1, j + 1

    liste = sorted(alle.values(), key=lambda t: t["start"].split("/")[::-1])
    geholt = adressen_ergaenzen(s, liste, max_abrufe=60)
    print(f"Tooltips geholt: {geholt}")

    # Provinz ueber den Klubcode zuordnen
    karte = klub_codes(s)
    zugeordnet = 0
    for t in liste:
        name = (t.get("klub") or t.get("ort") or "")
        name = re.sub(r"\(.*?\)", "", name)
        name = re.sub(r"[^A-Za-zÀ-ÿ0-9 ]", " ", name)
        name = re.sub(r"\s+", " ", name).strip().upper()
        for kn, info in karte.items():
            if kn and (kn == name or kn in name or name in kn):
                t["klubcode"] = info["code"]
                t["klub_voll"] = info["voll"]
                t["provinz"] = PROVINZ.get(info["code"][:1], "?")
                zugeordnet += 1
                break
    protokoll.append({"klubliste": len(karte), "provinz_zugeordnet": zugeordnet})

    return {"protokoll": protokoll, "anzahl": len(liste), "turniere": liste[:600]}

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

    print("Hole Anmeldungen und Interclub-Daten ...")
    ergebnis["anmeldungen"] = anmeldungen_und_teams(s)

    print("Suche Turnierkalender ...")
    ergebnis["kalender"] = turnierkalender(s)

    print("Turniersuche Zeitraum ...")
    ergebnis["kalender_suche"] = kalender_zeitraum(s, 2026, 8, monate=9)
    print("Klubverzeichnis uebersprungen (Seite ist JS-Huelle ohne Daten)")
    try:
        kv = {"treffer": {"hinweis": "uebersprungen"}, "klubs": {}}
        ergebnis["klubverzeichnis"] = {"treffer": kv["treffer"], "anzahl": len(kv["klubs"]),
                                       "probe": kv.get("probe", "")[:2500],
                                       "klubs": kv["klubs"]}
        for t in ergebnis["kalender_suche"].get("turniere", []):
            schl = (t.get("klub") or t.get("ort") or "").upper()[:40]
            for name, info in kv["klubs"].items():
                if schl and (schl in name or name in schl):
                    t["plz"] = info["plz"]
                    t["gemeinde"] = info["gemeinde"]
                    break
    except Exception as e:
        ergebnis["klubverzeichnis"] = {"fehler": repr(e)}

    print("Hole classement previsionnel ...")
    try:
        ergebnis["prevision"] = mct_prevision(num)
    except Exception as e:
        ergebnis["prevision"] = {"fehler": repr(e)}

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
