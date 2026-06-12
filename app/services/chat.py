"""Gruppenchat der Tipprunde (v0.2): eine Unterhaltung, live über SSE.

Bewusst schlank gehalten: Textnachrichten + genau eine Emoji-Reaktion je
Nutzer und Nachricht (neues Emoji ersetzt das alte). Kein Löschen, kein
Bearbeiten — der Chat ist ein Stammtisch, kein Forum. Der Versand an die
offenen Clients läuft über den vorhandenen SSE-Broker (Event „chat" bzw.
„chat_reaktion"); wer nicht verbunden ist, lädt beim Öffnen einfach nach.

Reaktionen tragen die nutzer_ids je Emoji — „von mir?" rechnet der Client,
damit derselbe SSE-Payload für alle Empfänger stimmt (Freundeskreis, wer
reagiert hat ist ohnehin sichtbar).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db
from ..zeit import jetzt_iso
from .live import broker

INHALT_MAX = 500

# Feste Reaktions-Palette — hält die Anzeige ruhig und die Daten sauber.
REAKTIONS_EMOJIS = ("👍", "❤️", "😂", "😮", "⚽", "🍺")


def nachricht_anlegen(conn: sqlite3.Connection, *, nutzer_id: int, inhalt: str) -> int:
    """Legt eine Nachricht an und liefert ihre id; validiert die Länge."""
    text = inhalt.strip()
    if not text:
        raise ValueError("Die Nachricht ist leer.")
    if len(text) > INHALT_MAX:
        raise ValueError(f"Höchstens {INHALT_MAX} Zeichen pro Nachricht.")
    with db.schreib_transaktion(conn):
        cursor = conn.execute(
            "INSERT INTO nachricht (nutzer_id, inhalt, erstellt_utc) VALUES (?, ?, ?)",
            (nutzer_id, text, jetzt_iso()),
        )
        return cursor.lastrowid


def _reaktionen_je_nachricht(
    conn: sqlite3.Connection, nachricht_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    """Aggregierte Reaktionen: je Nachricht eine Liste {emoji, anzahl, nutzer_ids}."""
    if not nachricht_ids:
        return {}
    platzhalter = ",".join("?" for _ in nachricht_ids)
    zeilen = conn.execute(
        f"SELECT nachricht_id, emoji, COUNT(*) AS anzahl,"
        f" GROUP_CONCAT(nutzer_id) AS nutzer"
        f" FROM nachricht_reaktion WHERE nachricht_id IN ({platzhalter})"
        f" GROUP BY nachricht_id, emoji ORDER BY MIN(erstellt_utc)",
        nachricht_ids,
    ).fetchall()
    ergebnis: dict[int, list[dict[str, Any]]] = {}
    for zeile in zeilen:
        ergebnis.setdefault(zeile["nachricht_id"], []).append(
            {
                "emoji": zeile["emoji"],
                "anzahl": zeile["anzahl"],
                "nutzer_ids": [int(teil) for teil in str(zeile["nutzer"]).split(",")],
            }
        )
    return ergebnis


def nachrichten_liste(
    conn: sqlite3.Connection, *, vor_id: int | None = None, limit: int = 50
) -> dict[str, Any]:
    """Die jüngsten Nachrichten (chronologisch aufsteigend) + Pagination.

    `vor_id` lädt den nächstälteren Block (unendliches Hochscrollen).
    """
    limit = max(1, min(limit, 100))
    parameter: list[Any] = []
    bedingung = ""
    if vor_id is not None:
        bedingung = "WHERE n.id < ?"
        parameter.append(vor_id)
    zeilen = conn.execute(
        "SELECT n.id, n.nutzer_id, n.inhalt, n.erstellt_utc,"
        " nu.anzeigename, nu.rolle, nu.profilbild"
        f" FROM nachricht n JOIN nutzer nu ON nu.id = n.nutzer_id {bedingung}"
        " ORDER BY n.id DESC LIMIT ?",
        [*parameter, limit + 1],
    ).fetchall()
    aeltere_vorhanden = len(zeilen) > limit
    block = list(reversed(zeilen[:limit]))
    reaktionen = _reaktionen_je_nachricht(conn, [zeile["id"] for zeile in block])
    return {
        "nachrichten": [
            {**dict(zeile), "reaktionen": reaktionen.get(zeile["id"], [])} for zeile in block
        ],
        "aeltere_vorhanden": aeltere_vorhanden,
    }


def nachricht_json(conn: sqlite3.Connection, nachricht_id: int) -> dict[str, Any] | None:
    zeile = conn.execute(
        "SELECT n.id, n.nutzer_id, n.inhalt, n.erstellt_utc,"
        " nu.anzeigename, nu.rolle, nu.profilbild"
        " FROM nachricht n JOIN nutzer nu ON nu.id = n.nutzer_id WHERE n.id = ?",
        (nachricht_id,),
    ).fetchone()
    if zeile is None:
        return None
    reaktionen = _reaktionen_je_nachricht(conn, [nachricht_id])
    return {**dict(zeile), "reaktionen": reaktionen.get(nachricht_id, [])}


def reaktion_setzen(
    conn: sqlite3.Connection, *, nachricht_id: int, nutzer_id: int, emoji: str
) -> None:
    if emoji not in REAKTIONS_EMOJIS:
        raise ValueError("Unbekanntes Reaktions-Emoji.")
    with db.schreib_transaktion(conn):
        existiert = conn.execute(
            "SELECT 1 FROM nachricht WHERE id = ?", (nachricht_id,)
        ).fetchone()
        if existiert is None:
            raise LookupError("Nachricht nicht gefunden")
        conn.execute(
            "INSERT INTO nachricht_reaktion (nachricht_id, nutzer_id, emoji, erstellt_utc)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(nachricht_id, nutzer_id) DO UPDATE SET"
            " emoji = excluded.emoji, erstellt_utc = excluded.erstellt_utc",
            (nachricht_id, nutzer_id, emoji, jetzt_iso()),
        )


def reaktion_entfernen(
    conn: sqlite3.Connection, *, nachricht_id: int, nutzer_id: int
) -> None:
    with db.schreib_transaktion(conn):
        conn.execute(
            "DELETE FROM nachricht_reaktion WHERE nachricht_id = ? AND nutzer_id = ?",
            (nachricht_id, nutzer_id),
        )


def nachricht_publizieren(conn: sqlite3.Connection, nachricht_id: int) -> None:
    """Neue Nachricht an alle SSE-Clients."""
    daten = nachricht_json(conn, nachricht_id)
    if daten:
        broker.publish("chat", daten)


def reaktionen_publizieren(conn: sqlite3.Connection, nachricht_id: int) -> None:
    """Aktualisierte Reaktions-Chips einer Nachricht an alle SSE-Clients."""
    reaktionen = _reaktionen_je_nachricht(conn, [nachricht_id])
    broker.publish(
        "chat_reaktion",
        {"nachricht_id": nachricht_id, "reaktionen": reaktionen.get(nachricht_id, [])},
    )
