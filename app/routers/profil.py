"""Eigenes Profil (v0.1.1): Profilbild hochladen/entfernen, Namen ändern.

Das Bild kommt als roher Request-Body (kein Multipart — spart die
python-multipart-Abhängigkeit); ausgeliefert wird nur an angemeldete
Mitspieler, nie öffentlich.
"""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from .. import db, ratelimit, security
from ..abhaengigkeiten import aktueller_nutzer, get_db, get_einstellungen, ki_sichtbar
from ..config import Einstellungen
from ..modelle import NameAenderung, PinWechsel
from ..services import abzeichen as abzeichen_service
from ..services import nutzer as nutzer_service
from ..services import profilbilder, tippspiel
from ..zeit import jetzt_iso

router = APIRouter(prefix="/api", tags=["profil"])


@router.put("/me/profilbild")
async def profilbild_setzen(
    request: Request,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> dict[str, Any]:
    # Früh ablehnen, bevor der Body in den Speicher gelesen wird
    laenge = request.headers.get("content-length")
    if laenge and laenge.isdigit() and int(laenge) > profilbilder.MAX_BYTES:
        raise HTTPException(status_code=413, detail="Bild ist zu groß (max. 2 MB).")
    rohdaten = await request.body()
    if len(rohdaten) > profilbilder.MAX_BYTES:
        raise HTTPException(status_code=413, detail="Bild ist zu groß (max. 2 MB).")
    if not rohdaten:
        raise HTTPException(status_code=422, detail="Keine Bilddaten empfangen.")
    try:
        name = profilbilder.speichern(
            einstellungen, nutzer["id"], rohdaten, alt=nutzer["profilbild"]
        )
    except profilbilder.BildFehler as fehler:
        raise HTTPException(status_code=422, detail=str(fehler)) from None
    with db.schreib_transaktion(conn):
        conn.execute("UPDATE nutzer SET profilbild = ? WHERE id = ?", (name, nutzer["id"]))
    return {"profilbild": name}


@router.delete("/me/profilbild", status_code=204)
def profilbild_entfernen(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> None:
    with db.schreib_transaktion(conn):
        conn.execute("UPDATE nutzer SET profilbild = NULL WHERE id = ?", (nutzer["id"],))
    # Datei erst nach dem erfolgreichen Commit löschen (nie umgekehrt)
    if nutzer["profilbild"]:
        profilbilder.loeschen(einstellungen, nutzer["profilbild"])


@router.get("/profilbilder/{nutzer_id}")
def profilbild_holen(
    nutzer_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> FileResponse:
    zeile = conn.execute(
        "SELECT profilbild FROM nutzer WHERE id = ?", (nutzer_id,)
    ).fetchone()
    pfad = profilbilder.pfad(einstellungen, zeile["profilbild"] if zeile else None)
    if pfad is None:
        raise HTTPException(status_code=404, detail="Kein Profilbild")
    # Die URL trägt den Dateinamen als Version (?v=) — darf lange gecacht werden,
    # bleibt aber privat (Avatare gehören nicht in geteilte Caches).
    return FileResponse(
        pfad,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=604800"},
    )


@router.post("/me/name")
def name_aendern(
    daten: NameAenderung,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    neu = daten.anzeigename.strip()
    if not neu:
        raise HTTPException(status_code=422, detail="Der Name darf nicht leer sein.")
    if neu == nutzer["anzeigename"]:
        return {"anzeigename": neu}
    with db.schreib_transaktion(conn):
        kollision = conn.execute(
            "SELECT 1 FROM nutzer WHERE anzeigename = ? COLLATE NOCASE AND id != ?",
            (neu, nutzer["id"]),
        ).fetchone()
        if kollision:
            raise HTTPException(status_code=409, detail="Der Name ist schon vergeben.")
        conn.execute("UPDATE nutzer SET anzeigename = ? WHERE id = ?", (neu, nutzer["id"]))
        # quelle='admin' = manuelle Änderung (das CHECK-Set der Bestands-DBs
        # kennt keine eigene Nutzer-Quelle); der Akteur ist der Nutzer selbst.
        db.change_log_eintrag(
            conn,
            entitaet="nutzer",
            entitaet_id=nutzer["id"],
            feld="anzeigename",
            alt_wert=nutzer["anzeigename"],
            neu_wert=neu,
            quelle="admin",
            akteur=nutzer["anzeigename"],
            zeitpunkt_utc=jetzt_iso(),
        )
    return {"anzeigename": neu}


@router.post("/me/pin")
def pin_wechseln(
    daten: PinWechsel,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, str]:
    """Eigene PIN ändern (v0.1.2). Beendet alle Sitzungen — auch die aktuelle.

    Rate-Limit vor der scrypt-Prüfung: schützt die CPU und bremst Versuche,
    die aktuelle PIN einer offenen Sitzung zu erraten.
    """
    if not ratelimit.erlaubt(f"pinwechsel:{nutzer['id']}", limit=5, fenster_sekunden=600):
        raise HTTPException(status_code=429, detail="Zu viele Versuche — bitte kurz warten.")
    zeile = conn.execute(
        "SELECT pin_hash FROM nutzer WHERE id = ?", (nutzer["id"],)
    ).fetchone()
    if zeile is None or not security.pin_pruefen(daten.alte_pin, zeile["pin_hash"]):
        raise HTTPException(status_code=403, detail="Die aktuelle PIN ist falsch.")
    try:
        nutzer_service.pin_aendern(
            conn, nutzer_id=nutzer["id"], neue_pin=daten.neue_pin, akteur=nutzer["anzeigename"]
        )
    except ValueError as fehler:
        raise HTTPException(status_code=422, detail=str(fehler)) from fehler
    return {"status": "ok", "hinweis": "PIN geändert — bitte neu anmelden."}


@router.get("/profil/{nutzer_id}")
def profil_ansehen(
    nutzer_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    """Oeffentliches Profil eines Mitspielers (v0.2): Kopf, Statistik,
    Abzeichen und die Tipp-Historie.

    Tipp-Geheimhaltung: Es erscheinen nur Tipps zu Spielen, deren Anpfiff
    vorbei ist - dieselbe Fairness-Regel wie in der Spiel-Lupe (auch ein
    vorab abgesagtes Spiel verraet die Tipps nicht vor der Anstosszeit).
    KI-Gate: Das KI-Profil sehen nur Freigeschaltete (404, um die Existenz
    nicht zu verraten); der Platz zaehlt wie in der Rangliste des
    Betrachters. Konten ausserhalb der Rangliste (rangliste_sichtbar = 0)
    liefern keinen Platz, sind aber ansehbar.
    """
    person = conn.execute(
        "SELECT id, anzeigename, rolle, profilbild, erstellt_utc FROM nutzer WHERE id = ?",
        (nutzer_id,),
    ).fetchone()
    if person is None:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden")
    if person["rolle"] == "ki" and not ki_sichtbar(nutzer):
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden")
    eintrag = next(
        (
            zeile
            for zeile in tippspiel.rangliste(conn, mit_ki=ki_sichtbar(nutzer))
            if zeile["nutzer_id"] == nutzer_id
        ),
        None,
    )
    tipps = conn.execute(
        "SELECT t.spiel_id, t.tipp_heim, t.tipp_gast, t.punkte,"
        " s.anstoss_utc, s.runde, s.status, s.tore_heim, s.tore_gast,"
        " th.fifa_code AS heim_code, tg.fifa_code AS gast_code,"
        " th.name AS heim_name, tg.name AS gast_name"
        " FROM tipp t JOIN spiel s ON s.id = t.spiel_id"
        " LEFT JOIN team th ON th.id = s.heim_team_id"
        " LEFT JOIN team tg ON tg.id = s.gast_team_id"
        " WHERE t.nutzer_id = ? AND s.status != 'geplant' AND s.anstoss_utc <= ?"
        " ORDER BY s.anstoss_utc DESC, s.id DESC LIMIT 120",
        (nutzer_id, jetzt_iso()),
    ).fetchall()
    return {
        "nutzer": {
            "id": person["id"],
            "anzeigename": person["anzeigename"],
            "rolle": person["rolle"],
            "profilbild": person["profilbild"],
            "dabei_seit": person["erstellt_utc"],
        },
        "rangliste": dict(eintrag) if eintrag else None,
        "abzeichen": abzeichen_service.fuer_nutzer(conn, nutzer_id),
        "tipps": [dict(zeile) for zeile in tipps],
    }