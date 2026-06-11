"""FastAPI-Dependencies: Einstellungen, DB-Verbindung je Request, Login-Pflicht."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Iterator, Optional

from fastapi import Depends, HTTPException, Request

from . import db
from .config import Einstellungen
from .security import token_hashen
from .zeit import jetzt_iso

SESSION_COOKIE = "wm26_session"


def get_einstellungen(request: Request) -> Einstellungen:
    return request.app.state.einstellungen


def get_db(
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> Iterator[sqlite3.Connection]:
    conn = db.verbinden(einstellungen.db_pfad)
    try:
        yield conn
    finally:
        conn.close()


def optionaler_nutzer(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> Optional[sqlite3.Row]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return conn.execute(
        "SELECT n.id, n.anzeigename, n.rolle, n.ki_freigeschaltet, n.tipp_erinnerung_minuten"
        " FROM sitzung s JOIN nutzer n ON n.id = s.nutzer_id"
        " WHERE s.token_hash = ? AND s.ablauf_utc > ?",
        (token_hashen(token), jetzt_iso()),
    ).fetchone()


def aktueller_nutzer(
    nutzer: Annotated[Optional[sqlite3.Row], Depends(optionaler_nutzer)],
) -> sqlite3.Row:
    if nutzer is None:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return nutzer


def admin_nutzer(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
) -> sqlite3.Row:
    if nutzer["rolle"] != "admin":
        raise HTTPException(status_code=403, detail="Nur für Administratoren")
    return nutzer


def ki_sichtbar(nutzer: sqlite3.Row) -> bool:
    """KI-Gate (SPEC 5.2/5.4): KI-Inhalte sehen nur Admins, der KI-Tipper selbst
    und vom Admin freigeschaltete Profile — für alle anderen ist die App KI-frei."""
    return nutzer["rolle"] in ("admin", "ki") or bool(nutzer["ki_freigeschaltet"])
