"""Feedback und Fehlermeldungen (v0.1.1): Nutzer melden aus der App heraus,
der Admin sichtet den Posteingang (Endpunkte dafür in routers/admin.py)."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from .. import db, ratelimit
from ..abhaengigkeiten import aktueller_nutzer, get_db
from ..modelle import FeedbackEingabe
from ..zeit import jetzt_iso

router = APIRouter(prefix="/api", tags=["feedback"])


@router.post("/feedback", status_code=201)
def feedback_senden(
    daten: FeedbackEingabe,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    # Spamschutz pro Konto — 5 Meldungen je Stunde reichen auch eifrigen Testern.
    if not ratelimit.erlaubt(f"feedback:{nutzer['id']}", limit=5, fenster_sekunden=3600):
        raise HTTPException(
            status_code=429, detail="Zu viele Meldungen — bitte versuch es später noch einmal."
        )
    jetzt = jetzt_iso()
    with db.schreib_transaktion(conn):
        cursor = conn.execute(
            "INSERT INTO feedback (nutzer_id, kategorie, nachricht, erstellt_utc)"
            " VALUES (?, ?, ?, ?)",
            (nutzer["id"], daten.kategorie, daten.nachricht, jetzt),
        )
        feedback_id = cursor.lastrowid
    return {"id": feedback_id, "kategorie": daten.kategorie, "erstellt_utc": jetzt}
