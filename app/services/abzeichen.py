"""Abzeichen (v0.2): kleine Erfolge fürs Nutzerprofil, live berechnet.

Bewusst ohne eigene Tabelle: bei einer Tipprunde im Freundeskreis ist die
Berechnung pro Profil-Aufruf billig, und abgeleitete Daten können nie
veralten oder doppelt vergeben werden. Jedes Abzeichen liefert seinen
aktuellen Zählerstand mit — auch unterhalb der Schwelle (Ansporn).
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .tippspiel import PUNKTE_EXAKT

# Tendenz-Quote, ab der ein richtiger Tipp als Außenseiter-Coup zählt.
AUSSENSEITER_QUOTE = 3.5
# Vorlauf für den Frühen Vogel (Tipp lange vor Anpfiff abgegeben).
FRUEH_STUNDEN = 24


def _gewertete_tipps(conn: sqlite3.Connection, nutzer_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT t.punkte, t.abgegeben_utc, t.tipp_heim, t.tipp_gast,"
        " s.anstoss_utc, s.id AS spiel_id"
        " FROM tipp t JOIN spiel s ON s.id = t.spiel_id"
        " WHERE t.nutzer_id = ? AND t.punkte IS NOT NULL"
        " ORDER BY s.anstoss_utc, s.id",
        (nutzer_id,),
    ).fetchall()


def _laengste_serie(punkte: list[int], *, mindest: int) -> int:
    laengste = aktuelle = 0
    for wert in punkte:
        aktuelle = aktuelle + 1 if wert >= mindest else 0
        laengste = max(laengste, aktuelle)
    return laengste


def _tagessiege(conn: sqlite3.Connection, nutzer_id: int) -> int:
    """Tage, an denen der Nutzer die Tageswertung (mit)gewonnen hat."""
    zeilen = conn.execute(
        "SELECT substr(s.anstoss_utc, 1, 10) AS tag, t.nutzer_id,"
        " SUM(t.punkte) AS punkte"
        " FROM tipp t JOIN spiel s ON s.id = t.spiel_id"
        " JOIN nutzer n ON n.id = t.nutzer_id"
        " WHERE t.punkte IS NOT NULL AND n.rangliste_sichtbar = 1"
        " GROUP BY tag, t.nutzer_id",
        (),
    ).fetchall()
    beste: dict[str, int] = {}
    eigene: dict[str, int] = {}
    for zeile in zeilen:
        beste[zeile["tag"]] = max(beste.get(zeile["tag"], 0), zeile["punkte"])
        if zeile["nutzer_id"] == nutzer_id:
            eigene[zeile["tag"]] = zeile["punkte"]
    return sum(
        1 for tag, punkte in eigene.items() if punkte > 0 and punkte == beste[tag]
    )


def _aussenseiter_treffer(conn: sqlite3.Connection, nutzer_id: int) -> int:
    """Richtige Tendenz bei Tipps, deren getippter Ausgang Quote ≥ 3,5 hatte."""
    zeile = conn.execute(
        "SELECT COUNT(*) AS anzahl"
        " FROM tipp t JOIN quote q ON q.spiel_id = t.spiel_id"
        " WHERE t.nutzer_id = ? AND t.punkte >= 2"
        " AND (CASE WHEN t.tipp_heim > t.tipp_gast THEN q.heim"
        "       WHEN t.tipp_heim = t.tipp_gast THEN q.remis"
        "       ELSE q.gast END) >= ?",
        (nutzer_id, AUSSENSEITER_QUOTE),
    ).fetchone()
    return zeile["anzahl"]


def fuer_nutzer(conn: sqlite3.Connection, nutzer_id: int) -> list[dict[str, Any]]:
    tipps = _gewertete_tipps(conn, nutzer_id)
    punkte = [zeile["punkte"] for zeile in tipps]
    exakte = sum(1 for wert in punkte if wert == PUNKTE_EXAKT)
    treffer_serie = _laengste_serie(punkte, mindest=1)
    exakt_serie = _laengste_serie(punkte, mindest=PUNKTE_EXAKT)
    # strftime statt datetime(): das Projekt speichert "…T…Z", datetime()
    # liefert "… …" — der Stringvergleich wäre sonst am Schwellen-Tag
    # systematisch falsch ('T' > ' ').
    fruehe = conn.execute(
        "SELECT COUNT(*) AS anzahl FROM tipp t JOIN spiel s ON s.id = t.spiel_id"
        " WHERE t.nutzer_id = ?"
        " AND t.abgegeben_utc <= strftime('%Y-%m-%dT%H:%M:%SZ', s.anstoss_utc, ?)",
        (nutzer_id, f"-{FRUEH_STUNDEN} hours"),
    ).fetchone()["anzahl"]
    bonus_richtig = conn.execute(
        "SELECT COUNT(*) AS anzahl FROM bonustipp WHERE nutzer_id = ? AND punkte > 0",
        (nutzer_id,),
    ).fetchone()["anzahl"]
    tagessiege = _tagessiege(conn, nutzer_id)
    aussenseiter = _aussenseiter_treffer(conn, nutzer_id)

    katalog = [
        ("tagessieger", "🏆", "Tagessieger", "Beste Tageswertung der Runde", tagessiege, 1),
        ("exakt_profi", "🎯", "Exakt-Profi", "Ergebnisse auf den Punkt getroffen", exakte, 3),
        ("serien_koenig", "🔥", "Serien-König", "Tipps in Folge mit Punkten", treffer_serie, 3),
        ("perfekte_serie", "💜", "Perfekte Serie", "Exakte Treffer in Folge", exakt_serie, 2),
        ("aussenseiter", "🦊", "Außenseiter-Flüsterer", f"Richtig getippt bei Quote ab {AUSSENSEITER_QUOTE:g}", aussenseiter, 1),
        ("frueher_vogel", "🐦", "Früher Vogel", f"Tipps mindestens {FRUEH_STUNDEN} h vor Anpfiff", fruehe, 10),
        ("bonus_orakel", "🔮", "Bonus-Orakel", "Bonusfragen richtig beantwortet", bonus_richtig, 1),
    ]
    return [
        {
            "schluessel": schluessel,
            "emoji": emoji,
            "titel": titel,
            "beschreibung": beschreibung,
            "wert": wert,
            "ziel": ziel,
            "erreicht": wert >= ziel,
        }
        for schluessel, emoji, titel, beschreibung, wert, ziel in katalog
    ]
