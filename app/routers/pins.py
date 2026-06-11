"""Pins (Spiele/Teams markieren) und Web-Push-Abos (SPEC 5.5)."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..abhaengigkeiten import aktueller_nutzer, get_db, get_einstellungen
from ..config import Einstellungen
from ..services import push
from ..zeit import jetzt_iso

router = APIRouter(prefix="/api", tags=["pins"])

_PIN_TABELLEN = {"spiel": "spiel", "team": "team"}


class PushSchluessel(BaseModel):
    p256dh: str = Field(min_length=1, max_length=300)
    auth: str = Field(min_length=1, max_length=100)


class PushAbo(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: PushSchluessel


class PushAbmeldung(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)


@router.get("/pins")
def pins_liste(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT typ, ref_id FROM pin WHERE nutzer_id = ? ORDER BY erstellt_utc",
        (nutzer["id"],),
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]


@router.put("/pins/{typ}/{ref_id}", status_code=204)
def pin_setzen(
    typ: str,
    ref_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    tabelle = _PIN_TABELLEN.get(typ)
    if tabelle is None:
        raise HTTPException(status_code=404, detail="Pin-Typ unbekannt")
    existiert = conn.execute(f"SELECT 1 FROM {tabelle} WHERE id = ?", (ref_id,)).fetchone()
    if existiert is None:
        raise HTTPException(status_code=404, detail=f"{typ} nicht gefunden")
    with db.schreib_transaktion(conn):
        conn.execute(
            "INSERT OR IGNORE INTO pin (nutzer_id, typ, ref_id, erstellt_utc) VALUES (?, ?, ?, ?)",
            (nutzer["id"], typ, ref_id, jetzt_iso()),
        )


@router.delete("/pins/{typ}/{ref_id}", status_code=204)
def pin_entfernen(
    typ: str,
    ref_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    if typ not in _PIN_TABELLEN:
        raise HTTPException(status_code=404, detail="Pin-Typ unbekannt")
    with db.schreib_transaktion(conn):
        conn.execute(
            "DELETE FROM pin WHERE nutzer_id = ? AND typ = ? AND ref_id = ?",
            (nutzer["id"], typ, ref_id),
        )


@router.get("/push/vapid-key")
def vapid_key(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> dict[str, Any]:
    """Öffentlicher VAPID-Schlüssel fürs Frontend; leer = Push deaktiviert."""
    return {"public_key": einstellungen.vapid_public_key, "aktiv": push.aktiv(einstellungen)}


@router.post("/push/subscribe", status_code=204)
def push_anmelden(
    abo: PushAbo,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> None:
    if not push.aktiv(einstellungen):
        raise HTTPException(status_code=503, detail="Web Push ist nicht konfiguriert")
    push.subscription_speichern(
        conn,
        nutzer_id=nutzer["id"],
        endpoint=abo.endpoint,
        p256dh=abo.keys.p256dh,
        auth=abo.keys.auth,
    )


@router.post("/push/unsubscribe", status_code=204)
def push_abmelden(
    abmeldung: PushAbmeldung,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    push.subscription_loeschen(conn, nutzer_id=nutzer["id"], endpoint=abmeldung.endpoint)
