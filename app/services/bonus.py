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
    conn: sqlite3.Connection, *, bonusfrage_id: int, aufloesung_refs: list[int], akteur: str
) -> int:
    """Setzt die richtige(n) Antwort(en) und vergibt Punkte; liefert gewertete Tipps.

    Mehrere richtige Antworten (v0.2) decken Fragen wie „Wer erreicht das
    Halbfinale?" ab — jeder Tipp auf eine der Antworten bekommt die vollen
    Punkte. Erneutes Auflösen ersetzt die alte Wertung (Korrekturen). Die
    Legacy-Spalte aufloesung_ref hält weiter den „ist aufgelöst?"-Zustand.
    """
    frage = conn.execute(
        "SELECT * FROM bonusfrage WHERE id = ?", (bonusfrage_id,)
    ).fetchone()
    if frage is None:
        raise BonusFehler("Bonusfrage nicht gefunden")
    refs = sorted(set(aufloesung_refs))
    if not refs:
        raise BonusFehler("Mindestens eine richtige Antwort angeben")
    for ref in refs:
        if _antwort_name(conn, frage["typ"], ref) is None:
            raise BonusFehler(f"Auflösung muss ein vorhandenes {frage['typ']}-Profil sein")
    with db.schreib_transaktion(conn):
        conn.execute(
            "DELETE FROM bonusfrage_aufloesung WHERE bonusfrage_id = ?", (bonusfrage_id,)
        )
        conn.executemany(
            "INSERT INTO bonusfrage_aufloesung (bonusfrage_id, ref) VALUES (?, ?)",
            [(bonusfrage_id, ref) for ref in refs],
        )
        conn.execute(
            "UPDATE bonusfrage SET aufloesung_ref = ? WHERE id = ?",
            (refs[0], bonusfrage_id),
        )
        platzhalter = ",".join("?" for _ in refs)
        gewertet = conn.execute(
            f"UPDATE bonustipp SET punkte = CASE WHEN antwort_ref IN ({platzhalter})"
            " THEN ? ELSE 0 END WHERE bonusfrage_id = ?",
            (*refs, frage["punkte_wert"], bonusfrage_id),
        ).rowcount
        db.change_log_eintrag(
            conn,
            entitaet="bonusfrage",
            entitaet_id=bonusfrage_id,
            feld="aufloesung_ref",
            alt_wert=frage["aufloesung_ref"],
            neu_wert=",".join(str(ref) for ref in refs),
            quelle="admin",
            akteur=akteur,
            zeitpunkt_utc=jetzt_iso(),
        )
    return gewertet


def _aufloesung_refs(conn: sqlite3.Connection, frage: sqlite3.Row) -> list[int]:
    """Alle richtigen Antworten einer Frage (neue Tabelle, Fallback Legacy-Spalte)."""
    zeilen = conn.execute(
        "SELECT ref FROM bonusfrage_aufloesung WHERE bonusfrage_id = ? ORDER BY ref",
        (frage["id"],),
    ).fetchall()
    if zeilen:
        return [zeile["ref"] for zeile in zeilen]
    return [frage["aufloesung_ref"]] if frage["aufloesung_ref"] is not None else []


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
        # Mehrfach-Auflösung (v0.2): alle richtigen Antworten; aufloesung_name
        # bleibt als verbundener Anzeigetext erhalten („Spanien, Frankreich, …").
        namen = [
            name
            for ref in _aufloesung_refs(conn, frage)
            if (name := _antwort_name(conn, frage["typ"], ref)) is not None
        ]
        eintrag: dict[str, Any] = {
            "id": frage["id"],
            "frage": frage["frage"],
            "typ": frage["typ"],
            "punkte_wert": frage["punkte_wert"],
            "einsendeschluss_utc": frage["einsendeschluss_utc"],
            "offen": offen,
            "aufloesung_ref": frage["aufloesung_ref"],
            "aufloesung_namen": namen,
            "aufloesung_name": ", ".join(namen) if namen else None,
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
