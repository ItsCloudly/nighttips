"""Bonusfragen (SPEC 5.4): z. B. Weltmeister, Torschützenkönig.

typ 'team' -> antwort_ref ist eine team.id, typ 'spieler' -> eine spieler.id.
Tipps sind bis zum Einsendeschluss änderbar; fremde Bonustipps werden erst
danach sichtbar (gleiche Fairness-Regel wie bei Spieltipps).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .. import db
from ..zeit import jetzt_iso


class BonusFehler(Exception):
    pass


def frage_anlegen(
    conn: sqlite3.Connection,
    *,
    frage: str,
    typ: str,
    punkte_wert: int,
    einsendeschluss_utc: str,
) -> int:
    with db.schreib_transaktion(conn):
        cursor = conn.execute(
            "INSERT INTO bonusfrage (frage, typ, punkte_wert, einsendeschluss_utc)"
            " VALUES (?, ?, ?, ?)",
            (frage, typ, punkte_wert, einsendeschluss_utc),
        )
        return cursor.lastrowid


def _antwort_name(conn: sqlite3.Connection, typ: str, ref: int | None) -> str | None:
    if ref is None:
        return None
    tabelle = "team" if typ == "team" else "spieler"
    zeile = conn.execute(f"SELECT name FROM {tabelle} WHERE id = ?", (ref,)).fetchone()
    return zeile["name"] if zeile else None


def tipp_abgeben(
    conn: sqlite3.Connection, *, nutzer_id: int, bonusfrage_id: int, antwort_ref: int
) -> dict[str, Any]:
    frage = conn.execute(
        "SELECT * FROM bonusfrage WHERE id = ?", (bonusfrage_id,)
    ).fetchone()
    if frage is None:
        raise BonusFehler("Bonusfrage nicht gefunden")
    jetzt = jetzt_iso()
    if jetzt >= frage["einsendeschluss_utc"] or frage["aufloesung_ref"] is not None:
        raise BonusFehler("Der Einsendeschluss ist vorbei")
    if _antwort_name(conn, frage["typ"], antwort_ref) is None:
        raise BonusFehler(f"Antwort muss ein vorhandenes {frage['typ']}-Profil sein")
    with db.schreib_transaktion(conn):
        conn.execute(
            "INSERT INTO bonustipp (nutzer_id, bonusfrage_id, antwort_ref, abgegeben_utc, punkte)"
            " VALUES (?, ?, ?, ?, NULL)"
            " ON CONFLICT(nutzer_id, bonusfrage_id) DO UPDATE SET"
            " antwort_ref = excluded.antwort_ref, abgegeben_utc = excluded.abgegeben_utc,"
            " punkte = NULL",
            (nutzer_id, bonusfrage_id, antwort_ref, jetzt),
        )
    return {"bonusfrage_id": bonusfrage_id, "antwort_ref": antwort_ref, "abgegeben_utc": jetzt}


def aufloesen(
    conn: sqlite3.Connection, *, bonusfrage_id: int, aufloesung_ref: int, akteur: str
) -> int:
    """Setzt die richtige Antwort und vergibt die Punkte; liefert gewertete Tipps."""
    frage = conn.execute(
        "SELECT * FROM bonusfrage WHERE id = ?", (bonusfrage_id,)
    ).fetchone()
    if frage is None:
        raise BonusFehler("Bonusfrage nicht gefunden")
    if _antwort_name(conn, frage["typ"], aufloesung_ref) is None:
        raise BonusFehler(f"Auflösung muss ein vorhandenes {frage['typ']}-Profil sein")
    with db.schreib_transaktion(conn):
        conn.execute(
            "UPDATE bonusfrage SET aufloesung_ref = ? WHERE id = ?",
            (aufloesung_ref, bonusfrage_id),
        )
        gewertet = conn.execute(
            "UPDATE bonustipp SET punkte = CASE WHEN antwort_ref = ? THEN ? ELSE 0 END"
            " WHERE bonusfrage_id = ?",
            (aufloesung_ref, frage["punkte_wert"], bonusfrage_id),
        ).rowcount
        db.change_log_eintrag(
            conn,
            entitaet="bonusfrage",
            entitaet_id=bonusfrage_id,
            feld="aufloesung_ref",
            alt_wert=frage["aufloesung_ref"],
            neu_wert=aufloesung_ref,
            quelle="admin",
            akteur=akteur,
            zeitpunkt_utc=jetzt_iso(),
        )
    return gewertet


def fragen_fuer_nutzer(
    conn: sqlite3.Connection, nutzer_id: int, *, mit_ki: bool = True
) -> list[dict[str, Any]]:
    """Alle Bonusfragen mit eigenem Tipp; fremde Tipps erst nach Einsendeschluss.

    KI-Gate (SPEC 5.4): mit_ki=False filtert die Antworten des KI-Tippers heraus.
    """
    jetzt = jetzt_iso()
    fragen = conn.execute(
        "SELECT * FROM bonusfrage ORDER BY einsendeschluss_utc, id"
    ).fetchall()
    ergebnis = []
    for frage in fragen:
        offen = jetzt < frage["einsendeschluss_utc"] and frage["aufloesung_ref"] is None
        eintrag: dict[str, Any] = {
            "id": frage["id"],
            "frage": frage["frage"],
            "typ": frage["typ"],
            "punkte_wert": frage["punkte_wert"],
            "einsendeschluss_utc": frage["einsendeschluss_utc"],
            "offen": offen,
            "aufloesung_ref": frage["aufloesung_ref"],
            "aufloesung_name": _antwort_name(conn, frage["typ"], frage["aufloesung_ref"]),
            "mein_tipp": None,
            "tipps": [],
        }
        eigener = conn.execute(
            "SELECT antwort_ref, punkte FROM bonustipp WHERE bonusfrage_id = ? AND nutzer_id = ?",
            (frage["id"], nutzer_id),
        ).fetchone()
        if eigener:
            eintrag["mein_tipp"] = {
                "antwort_ref": eigener["antwort_ref"],
                "antwort_name": _antwort_name(conn, frage["typ"], eigener["antwort_ref"]),
                "punkte": eigener["punkte"],
            }
        if not offen:
            eintrag["tipps"] = [
                {
                    "anzeigename": zeile["anzeigename"],
                    "rolle": zeile["rolle"],
                    "antwort_name": _antwort_name(conn, frage["typ"], zeile["antwort_ref"]),
                    "punkte": zeile["punkte"],
                }
                for zeile in conn.execute(
                    "SELECT n.anzeigename, n.rolle, b.antwort_ref, b.punkte"
                    " FROM bonustipp b JOIN nutzer n ON n.id = b.nutzer_id"
                    " WHERE b.bonusfrage_id = ?"
                    + ("" if mit_ki else " AND n.rolle != 'ki'")
                    + " ORDER BY n.anzeigename COLLATE NOCASE",
                    (frage["id"],),
                ).fetchall()
            ]
        ergebnis.append(eintrag)
    return ergebnis
