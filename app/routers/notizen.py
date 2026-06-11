"""Private Spiel-Notizen (v0.1.1): eigene Gedanken je Spiel, nur für einen selbst."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..abhaengigkeiten import aktueller_nutzer, get_db
from ..modelle import NotizEingabe
from ..zeit import jetzt_iso

router = APIRouter(prefix="/api", tags=["notizen"])


def _spiel_pruefen(conn: sqlite3.Connection, spiel_id: int) -> None:
    if not conn.execute("SELECT 1 FROM spiel WHERE id = ?", (spiel_id,)).fetchone():
        raise HTTPException(status_code=404, detail="Spiel nicht gefunden")


@router.get("/notizen/{spiel_id}")
def notiz_lesen(
    spiel_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    _spiel_pruefen(conn, spiel_id)
    zeile = conn.execute(
        "SELECT text, erstellt_utc, geaendert_utc FROM notiz"
        " WHERE nutzer_id = ? AND spiel_id = ?",
        (nutzer["id"], spiel_id),
    ).fetchone()
    return {"spiel_id": spiel_id, "notiz": dict(zeile) if zeile else None}


@router.put("/notizen/{spiel_id}")
def notiz_speichern(
    spiel_id: int,
    daten: NotizEingabe,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    _spiel_pruefen(conn, spiel_id)
    jetzt = jetzt_iso()
    with db.schreib_transaktion(conn):
        conn.execute(
            "INSERT INTO notiz (nutzer_id, spiel_id, text, erstellt_utc, geaendert_utc)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(nutzer_id, spiel_id) DO UPDATE SET"
            " text = excluded.text, geaendert_utc = excluded.geaendert_utc",
            (nutzer["id"], spiel_id, daten.text, jetzt, jetzt),
        )
    return {"spiel_id": spiel_id, "text": daten.text, "geaendert_utc": jetzt}


@router.delete("/notizen/{spiel_id}", status_code=204)
def notiz_loeschen(
    spiel_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    _spiel_pruefen(conn, spiel_id)
    with db.schreib_transaktion(conn):
        conn.execute(
            "DELETE FROM notiz WHERE nutzer_id = ? AND spiel_id = ?",
            (nutzer["id"], spiel_id),
        )
