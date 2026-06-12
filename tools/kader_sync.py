"""Spielerfotos + Zusatzinfos von TheSportsDB in die lokale DB holen (v0.2).

Die Kader selbst kommen weiter aus football-data.org (Stammdaten-Sync);
dieses Skript reichert jeden Spieler OHNE Foto per Einzelabfrage an:
freigestelltes Porträt (Cutout, Fallback Thumb), Verein und Geburtsdatum,
falls in der DB noch leer. Bilder landen als 256-px-WebP in
daten/spielerfotos/, der Dateiname in spieler.foto.

Freier Test-API-Key „3" (öffentlich dokumentiert, nicht geheim), Limit
30 Anfragen/Minute — ein voller Lauf über ~1250 Spieler dauert damit
rund eine Stunde und ist jederzeit abbrechbar: bereits geholte Fotos
werden übersprungen, einfach erneut starten.

    python tools/kader_sync.py [--db daten/wm26.db] [--limit N] [--team GER]

Attribution: Daten/Bilder von thesportsdb.com (Hinweis steht in der App
unter Mehr → App). Nur für den privaten Tippspiel-Kreis gedacht.
"""
from __future__ import annotations

import argparse
import io
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image

# App-Module (laender-Mapping, später db) — Skript läuft aus dem Repo-Wurzelverzeichnis
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.laender import EXTERN_ALIAS_DE, TEAMNAMEN_DE  # noqa: E402

API_BASIS = "https://www.thesportsdb.com/api/v1/json/3"
ANFRAGE_ABSTAND = 2.1  # Sekunden — bleibt unter 30 Anfragen/Minute
KANTE = 256
ZEITLIMIT = 20.0


def _normalisiert(name: str) -> str:
    """Akzente weg, Kleinbuchstaben — für den Namensvergleich."""
    zerlegt = unicodedata.normalize("NFKD", name)
    return "".join(c for c in zerlegt if not unicodedata.combining(c)).casefold().strip()


# Deutsche Teamnamen → akzeptierte englische Schreibweisen (TheSportsDB
# liefert strNationality auf Englisch). Beide Richtungen der App-Mappings
# einsammeln, dazu der deutsche Name selbst (falls identisch, z. B. "Haiti").
_ENGLISCHE_NAMEN: dict[str, set[str]] = {}
for _en, _de in {**TEAMNAMEN_DE, **EXTERN_ALIAS_DE}.items():
    _ENGLISCHE_NAMEN.setdefault(_normalisiert(_de), set()).add(_normalisiert(_en))
for _de in set(TEAMNAMEN_DE.values()) | set(EXTERN_ALIAS_DE.values()):
    _ENGLISCHE_NAMEN.setdefault(_normalisiert(_de), set()).add(_normalisiert(_de))


def _passender_treffer(
    daten: dict, name: str, team_name: str, geburtsdatum_db: str | None
) -> dict | None:
    """Besten Kandidaten wählen — im Zweifel KEINEN (lieber kein Foto als ein
    falsches): bei Allerweltsnamen (Danilo, Rodri …) liefert die Suche
    mehrere Fußballer, daher müssen Nationalität (gegen das laender-Mapping)
    und, falls beidseitig bekannt, das Geburtsdatum zum DB-Spieler passen."""
    kandidaten = daten.get("player") or []
    fussballer = [k for k in kandidaten if (k.get("strSport") or "") == "Soccer"]
    erwartet = _ENGLISCHE_NAMEN.get(_normalisiert(team_name), set())

    def passt(kandidat: dict) -> bool:
        nationalitaet = _normalisiert(kandidat.get("strNationality") or "")
        if erwartet and nationalitaet and nationalitaet not in erwartet:
            return False
        geboren = (kandidat.get("dateBorn") or "").strip()
        if geburtsdatum_db and geboren and geboren != geburtsdatum_db:
            return False
        return True

    ziel = _normalisiert(name)
    exakte = [k for k in fussballer if _normalisiert(k.get("strPlayer") or "") == ziel and passt(k)]
    if len(exakte) == 1:
        return exakte[0]
    if len(exakte) > 1:
        return None  # mehrdeutig trotz Prüfungen — nicht raten
    # Kein exakter Treffer: einzigen passenden Fußballer nehmen
    passende = [k for k in fussballer if passt(k)]
    return passende[0] if len(passende) == 1 else None


def _foto_holen(client: httpx.Client, url: str) -> bytes | None:
    antwort = client.get(url, timeout=ZEITLIMIT)
    if antwort.status_code != 200 or not antwort.content:
        return None
    return antwort.content


def _als_webp(rohdaten: bytes) -> bytes:
    bild = Image.open(io.BytesIO(rohdaten))
    bild.load()
    if bild.mode not in ("RGB", "RGBA"):
        bild = bild.convert("RGBA")
    bild.thumbnail((KANTE, KANTE), Image.LANCZOS)
    ausgabe = io.BytesIO()
    bild.save(ausgabe, "WEBP", quality=88, method=6)
    return ausgabe.getvalue()


def main() -> int:
    # Windows-Konsole (cp1252) verschluckt Akzente in Spielernamen — lieber
    # ersetzen als mit UnicodeEncodeError aussteigen.
    for strom in (sys.stdout, sys.stderr):
        if hasattr(strom, "reconfigure"):
            strom.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Spielerfotos von TheSportsDB syncen")
    parser.add_argument("--db", default="daten/wm26.db", help="Pfad zur SQLite-DB")
    parser.add_argument("--limit", type=int, default=0, help="höchstens N Spieler (0 = alle)")
    parser.add_argument("--team", default="", help="nur ein Team (FIFA-Code, z. B. GER)")
    parser.add_argument("--trocken", action="store_true", help="nur anzeigen, nichts schreiben")
    argumente = parser.parse_args()

    db_pfad = Path(argumente.db)
    if not db_pfad.is_file():
        print(f"DB nicht gefunden: {db_pfad}", file=sys.stderr)
        return 1
    foto_verzeichnis = db_pfad.parent / "spielerfotos"
    foto_verzeichnis.mkdir(parents=True, exist_ok=True)

    # App-Verbindung samt Migrationen (foto-Spalte) — das Skript läuft damit
    # auch gegen eine DB, die der neue App-Code noch nie geöffnet hat.
    from app import db as app_db

    conn = app_db.verbinden(db_pfad)
    app_db.schema_anlegen(conn)
    sql = (
        "SELECT s.id, s.name, s.verein, s.geburtsdatum, t.name AS team_name, t.fifa_code"
        " FROM spieler s JOIN team t ON t.id = s.team_id"
        " WHERE s.foto IS NULL"
    )
    parameter: list[object] = []
    if argumente.team:
        sql += " AND t.fifa_code = ?"
        parameter.append(argumente.team.upper())
    sql += " ORDER BY t.fifa_code, s.name"
    offene = conn.execute(sql, parameter).fetchall()
    if argumente.limit:
        offene = offene[: argumente.limit]
    print(f"{len(offene)} Spieler ohne Foto — geschätzte Dauer ~{len(offene) * ANFRAGE_ABSTAND / 60:.0f} Min.")

    gefunden = ohne_treffer = fehler = 0
    with httpx.Client(headers={"User-Agent": "wm26-app/1.0 (+kader-sync)"}) as client:
        for index, spieler in enumerate(offene, start=1):
            zeit_start = time.monotonic()
            try:
                antwort = client.get(
                    f"{API_BASIS}/searchplayers.php?p={quote(spieler['name'])}",
                    timeout=ZEITLIMIT,
                )
                if antwort.status_code == 429:
                    print("  Rate-Limit (429) — 60 s Pause"); time.sleep(60)
                    continue
                antwort.raise_for_status()
                treffer = _passender_treffer(
                    antwort.json(), spieler["name"], spieler["team_name"], spieler["geburtsdatum"]
                )
                if treffer is None:
                    ohne_treffer += 1
                    print(f"[{index}/{len(offene)}] {spieler['name']} ({spieler['fifa_code']}): kein Treffer")
                else:
                    bild_url = treffer.get("strCutout") or treffer.get("strThumb")
                    bild_daten = None
                    if bild_url:
                        roh = _foto_holen(client, f"{bild_url}/preview")
                        if roh is None:
                            roh = _foto_holen(client, bild_url)
                        if roh:
                            bild_daten = _als_webp(roh)
                    if argumente.trocken:
                        print(f"[{index}/{len(offene)}] {spieler['name']}: Treffer"
                              f" ({'mit' if bild_daten else 'ohne'} Bild) — trocken, nichts geschrieben")
                    else:
                        dateiname = None
                        if bild_daten:
                            dateiname = f"{spieler['id']}.webp"
                            (foto_verzeichnis / dateiname).write_bytes(bild_daten)
                        # Lücken füllen, vorhandene Werte nie überschreiben
                        conn.execute(
                            "UPDATE spieler SET"
                            " foto = COALESCE(?, foto),"
                            " verein = COALESCE(verein, ?),"
                            " geburtsdatum = COALESCE(geburtsdatum, ?)"
                            " WHERE id = ?",
                            (
                                dateiname,
                                treffer.get("strTeam") or None,
                                treffer.get("dateBorn") or None,
                                spieler["id"],
                            ),
                        )
                        conn.commit()
                        gefunden += 1
                        print(f"[{index}/{len(offene)}] {spieler['name']}:"
                              f" {'Foto gespeichert' if dateiname else 'nur Infos (kein Bild)'}")
            except (httpx.HTTPError, OSError, ValueError) as ausnahme:
                fehler += 1
                print(f"[{index}/{len(offene)}] {spieler['name']}: Fehler — {ausnahme}", file=sys.stderr)
            rest = ANFRAGE_ABSTAND - (time.monotonic() - zeit_start)
            if rest > 0 and index < len(offene):
                time.sleep(rest)
    conn.close()
    print(f"Fertig: {gefunden} aktualisiert, {ohne_treffer} ohne Treffer, {fehler} Fehler.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
