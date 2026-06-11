"""Admin-Overrides (SPEC 3.5/4.4): manuelle Werte überdauern API-Syncs.

API-Syncs schreiben Rohwerte — für Felder mit aktivem Override behält der
Upsert den manuell gesetzten Wert bei (Priorität admin > api). Ein Override
bleibt aktiv, bis der Admin ihn aufhebt; danach gilt wieder der API-Stand.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..zeit import jetzt_iso


def setzen(
    conn: sqlite3.Connection,
    *,
    entitaet: str,
    entitaet_id: int,
    feld: str,
    wert: Any,
    nutzer_id: int | None,
) -> None:
    """Innerhalb einer offenen Schreib-Transaktion aufrufen."""
    conn.execute(
        "INSERT INTO override (entitaet, entitaet_id, feld, wert, gesetzt_von, gesetzt_utc, aktiv)"
        " VALUES (?, ?, ?, ?, ?, ?, 1)"
        " ON CONFLICT(entitaet, entitaet_id, feld) DO UPDATE SET"
        " wert = excluded.wert, gesetzt_von = excluded.gesetzt_von,"
        " gesetzt_utc = excluded.gesetzt_utc, aktiv = 1",
        (
            entitaet,
            entitaet_id,
            feld,
            None if wert is None else str(wert),
            nutzer_id,
            jetzt_iso(),
        ),
    )


def aufheben(conn: sqlite3.Connection, override_id: int) -> bool:
    """Innerhalb einer offenen Schreib-Transaktion aufrufen."""
    return (
        conn.execute(
            "UPDATE override SET aktiv = 0 WHERE id = ? AND aktiv = 1", (override_id,)
        ).rowcount
        > 0
    )


def aktive_felder(conn: sqlite3.Connection, entitaet: str, entitaet_id: int) -> set[str]:
    zeilen = conn.execute(
        "SELECT feld FROM override WHERE entitaet = ? AND entitaet_id = ? AND aktiv = 1",
        (entitaet, entitaet_id),
    ).fetchall()
    return {zeile["feld"] for zeile in zeilen}


def aktive_liste(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT o.id, o.entitaet, o.entitaet_id, o.feld, o.wert, o.gesetzt_utc,"
        " n.anzeigename AS gesetzt_von_name"
        " FROM override o LEFT JOIN nutzer n ON n.id = o.gesetzt_von"
        " WHERE o.aktiv = 1 ORDER BY o.gesetzt_utc DESC"
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]
