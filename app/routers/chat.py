"""Gruppenchat-Endpunkte (v0.2): lesen, schreiben, reagieren.

Flutschutz über den In-Process-Rate-Limiter (10 Nachrichten/Minute je
Nutzer, Reaktionen großzügiger). Inhalte sind reiner Text — escaped wird
im Frontend, gespeichert wird, was der Nutzer getippt hat.
"""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import ratelimit
from ..abhaengigkeiten import aktueller_nutzer, get_db, get_einstellungen
from ..config import Einstellungen
from ..services import chat, push

router = APIRouter(prefix="/api/chat", tags=["chat"])


class NachrichtEingabe(BaseModel):
    inhalt: str = Field(min_length=1, max_length=chat.INHALT_MAX)


class ReaktionEingabe(BaseModel):
    emoji: str = Field(min_length=1, max_length=8)


@router.get("")
def chat_lesen(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    vor_id: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    daten = chat.nachrichten_liste(conn, vor_id=vor_id, limit=limit)
    daten["emojis"] = list(chat.REAKTIONS_EMOJIS)
    return daten


@router.post("", status_code=201)
def chat_schreiben(
    eingabe: NachrichtEingabe,
    hintergrund: BackgroundTasks,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> dict[str, Any]:
    if not ratelimit.erlaubt(f"chat:{nutzer['id']}", limit=10, fenster_sekunden=60):
        raise HTTPException(
            status_code=429, detail="Kurz durchatmen — höchstens 10 Nachrichten pro Minute."
        )
    try:
        nachricht_id = chat.nachricht_anlegen(
            conn, nutzer_id=nutzer["id"], inhalt=eingabe.inhalt
        )
    except ValueError as fehler:
        raise HTTPException(status_code=422, detail=str(fehler)) from None
    chat.nachricht_publizieren(conn, nachricht_id)
    # Web-Push an alle mit aktiviertem Chat-Push (v0.3) — im Hintergrund nach
    # der Antwort, mit eigener DB-Verbindung; blockiert den POST nicht.
    hintergrund.add_task(
        push.chat_benachrichtigen,
        einstellungen,
        nachricht_id=nachricht_id,
        autor_id=nutzer["id"],
    )
    return chat.nachricht_json(conn, nachricht_id)


@router.put("/{nachricht_id}/reaktion", status_code=200)
def reaktion_setzen(
    nachricht_id: int,
    eingabe: ReaktionEingabe,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    if not ratelimit.erlaubt(f"chatreaktion:{nutzer['id']}", limit=30, fenster_sekunden=60):
        raise HTTPException(status_code=429, detail="Zu viele Reaktionen — kurz warten.")
    try:
        chat.reaktion_setzen(
            conn, nachricht_id=nachricht_id, nutzer_id=nutzer["id"], emoji=eingabe.emoji
        )
    except ValueError as fehler:
        raise HTTPException(status_code=422, detail=str(fehler)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="Nachricht nicht gefunden") from None
    chat.reaktionen_publizieren(conn, nachricht_id)
    return chat.nachricht_json(conn, nachricht_id)


@router.delete("/{nachricht_id}/reaktion", status_code=200)
def reaktion_entfernen(
    nachricht_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    # Gleicher Schlüssel wie das Setzen: PUT und DELETE teilen das 30/Min-Budget
    if not ratelimit.erlaubt(f"chatreaktion:{nutzer['id']}", limit=30, fenster_sekunden=60):
        raise HTTPException(status_code=429, detail="Zu viele Reaktionen — kurz warten.")
    # Erst die 404-Prüfung: unbekannte ids dürfen weder Schreib-Transaktion
    # noch SSE-Broadcast auslösen.
    daten = chat.nachricht_json(conn, nachricht_id)
    if daten is None:
        raise HTTPException(status_code=404, detail="Nachricht nicht gefunden")
    chat.reaktion_entfernen(conn, nachricht_id=nachricht_id, nutzer_id=nutzer["id"])
    chat.reaktionen_publizieren(conn, nachricht_id)
    return chat.nachricht_json(conn, nachricht_id)
