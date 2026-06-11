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

from .. import db
from ..abhaengigkeiten import aktueller_nutzer, get_db, get_einstellungen
from ..config import Einstellungen
from ..modelle import NameAenderung
from ..services import profilbilder
from ..zeit import jetzt_iso

router = APIRouter(prefix="/api", tags=["profil"])


@router.put("/me/profilbild")
async def profilbild_setzen(
    request: Request,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> dict[str, Any]:
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
    profilbilder.loeschen(einstellungen, nutzer["profilbild"])
    with db.schreib_transaktion(conn):
        conn.execute("UPDATE nutzer SET profilbild = NULL WHERE id = ?", (nutzer["id"],))


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
