"""Tippspiel-Logik: Abgabe mit Anpfiff-Sperre, Punktevergabe, Rangliste (SPEC 5.4).

Punkteschema: exaktes Ergebnis 4, richtige Tordifferenz 3, richtige Tendenz 2, sonst 0.
K.o.-Spiele: je nach Konfiguration zählt das Ergebnis nach 120 Minuten (Standard)
oder nach 90 Minuten; ein Elfmeterschießen zählt als Unentschieden-Tendenz.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .. import db
from ..config import Einstellungen
from ..zeit import jetzt_iso

PUNKTE_EXAKT = 4
PUNKTE_DIFFERENZ = 3
PUNKTE_TENDENZ = 2


class TippGesperrt(Exception):
    """Tipp-Abgabe nach Anpfiff (oder für nicht tippbare Spiele)."""


class SpielNichtGefunden(Exception):
    pass


def ist_ko_runde(runde: str) -> bool:
    return not runde.startswith("Gruppe")


def berechne_punkte(tipp_heim: int, tipp_gast: int, tore_heim: int, tore_gast: int) -> int:
    if tipp_heim == tore_heim and tipp_gast == tore_gast:
        return PUNKTE_EXAKT
    if tipp_heim - tipp_gast == tore_heim - tore_gast:
        return PUNKTE_DIFFERENZ
    tendenz_tipp = (tipp_heim > tipp_gast) - (tipp_heim < tipp_gast)
    tendenz_spiel = (tore_heim > tore_gast) - (tore_heim < tore_gast)
    if tendenz_tipp == tendenz_spiel:
        return PUNKTE_TENDENZ
    return 0


@dataclass(frozen=True)
class Wertung:
    tore_heim: int
    tore_gast: int
    # True: nur die Unentschieden-Tendenz ist bekannt (exakter Wertungsstand unbekannt),
    # Remis-Tipps erhalten die Differenz-Punkte, alle anderen 0.
    nur_remis_bekannt: bool = False


def wertung_ermitteln(spiel: sqlite3.Row, einstellungen: Einstellungen) -> Wertung | None:
    """Bestimmt das für die Punktevergabe maßgebliche Ergebnis, None falls offen."""
    if spiel["status"] != "beendet" or spiel["tore_heim"] is None or spiel["tore_gast"] is None:
        return None
    if not ist_ko_runde(spiel["runde"]):
        return Wertung(spiel["tore_heim"], spiel["tore_gast"])
    ergebnis_nach = spiel["ergebnis_nach"] or "90"
    if einstellungen.ko_wertung_nach_120:
        if ergebnis_nach == "elfmeterschiessen":
            # Nach 120 Minuten stand es unentschieden. Defensive Ausnahme: liefert
            # die API hier ungleiche Tore, werten wir nur die Remis-Tendenz.
            if spiel["tore_heim"] != spiel["tore_gast"]:
                return Wertung(spiel["tore_heim"], spiel["tore_gast"], nur_remis_bekannt=True)
        return Wertung(spiel["tore_heim"], spiel["tore_gast"])
    # 90-Minuten-Regel: ging das Spiel in die Verlängerung, stand es nach 90 Minuten
    # unentschieden; der exakte Stand nach 90 Minuten ist aus der API nicht ableitbar.
    if ergebnis_nach in ("120", "elfmeterschiessen"):
        return Wertung(spiel["tore_heim"], spiel["tore_gast"], nur_remis_bekannt=True)
    return Wertung(spiel["tore_heim"], spiel["tore_gast"])


def punkte_fuer_tipp(
    spiel: sqlite3.Row, tipp_heim: int, tipp_gast: int, einstellungen: Einstellungen
) -> int | None:
    wertung = wertung_ermitteln(spiel, einstellungen)
    if wertung is None:
        return None
    if wertung.nur_remis_bekannt:
        return PUNKTE_DIFFERENZ if tipp_heim == tipp_gast else 0
    return berechne_punkte(tipp_heim, tipp_gast, wertung.tore_heim, wertung.tore_gast)


def tipp_abgeben(
    conn: sqlite3.Connection, *, nutzer_id: int, spiel_id: int, tipp_heim: int, tipp_gast: int
) -> dict:
    spiel = conn.execute("SELECT * FROM spiel WHERE id = ?", (spiel_id,)).fetchone()
    if spiel is None:
        raise SpielNichtGefunden(f"Spiel {spiel_id} existiert nicht")
    jetzt = jetzt_iso()
    if jetzt >= spiel["anstoss_utc"] or spiel["status"] != "geplant":
        raise TippGesperrt("Die Tippabgabe ist seit Anpfiff gesperrt")
    with db.schreib_transaktion(conn):
        conn.execute(
            "INSERT INTO tipp (nutzer_id, spiel_id, tipp_heim, tipp_gast, abgegeben_utc, punkte)"
            " VALUES (?, ?, ?, ?, ?, NULL)"
            " ON CONFLICT(nutzer_id, spiel_id) DO UPDATE SET"
            " tipp_heim = excluded.tipp_heim, tipp_gast = excluded.tipp_gast,"
            " abgegeben_utc = excluded.abgegeben_utc, punkte = NULL",
            (nutzer_id, spiel_id, tipp_heim, tipp_gast, jetzt),
        )
    return {
        "spiel_id": spiel_id,
        "tipp_heim": tipp_heim,
        "tipp_gast": tipp_gast,
        "abgegeben_utc": jetzt,
    }


def spiel_auswerten(
    conn: sqlite3.Connection, spiel_id: int, einstellungen: Einstellungen, *, akteur: str
) -> int:
    """Vergibt Punkte für alle Tipps eines beendeten Spiels (idempotent)."""
    spiel = conn.execute("SELECT * FROM spiel WHERE id = ?", (spiel_id,)).fetchone()
    if spiel is None:
        raise SpielNichtGefunden(f"Spiel {spiel_id} existiert nicht")
    wertung = wertung_ermitteln(spiel, einstellungen)
    if wertung is None:
        return 0
    tipps = conn.execute("SELECT * FROM tipp WHERE spiel_id = ?", (spiel_id,)).fetchall()
    if not tipps:
        return 0
    with db.schreib_transaktion(conn):
        for tipp in tipps:
            punkte = punkte_fuer_tipp(
                spiel, tipp["tipp_heim"], tipp["tipp_gast"], einstellungen
            )
            conn.execute("UPDATE tipp SET punkte = ? WHERE id = ?", (punkte, tipp["id"]))
        db.change_log_eintrag(
            conn,
            entitaet="spiel",
            entitaet_id=spiel_id,
            feld="tipp_auswertung",
            alt_wert=None,
            neu_wert=f"{len(tipps)} Tipps gewertet",
            quelle="api" if akteur == "sync" else "admin",
            akteur=akteur,
            zeitpunkt_utc=jetzt_iso(),
        )
    return len(tipps)


def spiele_mit_offener_auswertung(conn: sqlite3.Connection) -> list[int]:
    zeilen = conn.execute(
        "SELECT DISTINCT s.id FROM spiel s JOIN tipp t ON t.spiel_id = s.id"
        " WHERE s.status = 'beendet' AND s.tore_heim IS NOT NULL AND t.punkte IS NULL"
    ).fetchall()
    return [zeile["id"] for zeile in zeilen]


def _live_prognose(
    conn: sqlite3.Connection, *, datum: str | None = None, runde: str | None = None
) -> dict[int, int]:
    """Hypothetische Punkte je Nutzer, würden die laufenden Spiele jetzt so enden.

    Nur Orientierung für die Rangliste (separat ausgewiesen) — gewertet wird
    weiterhin ausschließlich nach Abpfiff über spiel_auswerten.
    """
    bedingungen = [
        "s.status IN ('live', 'halbzeit')",
        "s.tore_heim IS NOT NULL",
        "s.tore_gast IS NOT NULL",
    ]
    parameter: list[str] = []
    if datum is not None:
        bedingungen.append("substr(s.anstoss_utc, 1, 10) = ?")
        parameter.append(datum)
    if runde is not None:
        bedingungen.append("s.runde = ?")
        parameter.append(runde)
    zeilen = conn.execute(
        "SELECT t.nutzer_id, t.tipp_heim, t.tipp_gast, s.tore_heim, s.tore_gast"
        " FROM tipp t JOIN spiel s ON s.id = t.spiel_id"
        f" WHERE {' AND '.join(bedingungen)}",
        parameter,
    ).fetchall()
    prognose: dict[int, int] = {}
    for zeile in zeilen:
        punkte = berechne_punkte(
            zeile["tipp_heim"], zeile["tipp_gast"], zeile["tore_heim"], zeile["tore_gast"]
        )
        prognose[zeile["nutzer_id"]] = prognose.get(zeile["nutzer_id"], 0) + punkte
    return prognose


def rangliste(
    conn: sqlite3.Connection,
    *,
    datum: str | None = None,
    runde: str | None = None,
    mit_ki: bool = True,
) -> list[dict]:
    """Rangliste gesamt, pro Tag (datum=YYYY-MM-DD) oder pro Runde (SPEC 5.4).

    Es zählen nur gewertete Tipps; Nutzer ohne gewertete Tipps im gewählten
    Zeitraum erscheinen mit 0 Punkten, damit alle Mitspieler sichtbar bleiben.
    KI-Gate: mit_ki=False blendet den KI-Tipper komplett aus — die Plätze
    werden dann ohne ihn durchgezählt. Konten mit rangliste_sichtbar=0
    (z. B. Test-Konten) fehlen für alle, ebenfalls mit aufgerückten Plätzen.
    """
    bedingungen = ["t.punkte IS NOT NULL"]
    parameter: list[str] = []
    if datum is not None:
        bedingungen.append("substr(s.anstoss_utc, 1, 10) = ?")
        parameter.append(datum)
    if runde is not None:
        bedingungen.append("s.runde = ?")
        parameter.append(runde)
    # Bonuspunkte zählen nur in der Gesamtwertung (sie gehören zu keinem Spieltag).
    mit_bonus = datum is None and runde is None
    ki_filter = " WHERE n.rangliste_sichtbar = 1" + ("" if mit_ki else " AND n.rolle != 'ki'")
    zeilen = conn.execute(
        f"""
        SELECT n.id, n.anzeigename, n.rolle, n.profilbild,
               COALESCE(SUM(t.punkte), 0) AS tipp_punkte,
               COUNT(t.id) AS tipps_gewertet,
               SUM(CASE WHEN t.punkte = ? THEN 1 ELSE 0 END) AS exakt,
               SUM(CASE WHEN t.punkte = ? THEN 1 ELSE 0 END) AS differenz,
               SUM(CASE WHEN t.punkte = ? THEN 1 ELSE 0 END) AS tendenz,
               COALESCE(b.punkte, 0) AS bonus_punkte
        FROM nutzer n
        LEFT JOIN (
            SELECT t.id, t.nutzer_id, t.punkte
            FROM tipp t
            JOIN spiel s ON s.id = t.spiel_id
            WHERE {" AND ".join(bedingungen)}
        ) t ON t.nutzer_id = n.id
        LEFT JOIN (
            SELECT nutzer_id, SUM(punkte) AS punkte FROM bonustipp
            WHERE punkte IS NOT NULL GROUP BY nutzer_id
        ) b ON b.nutzer_id = n.id
        {ki_filter}
        GROUP BY n.id
        """,
        (PUNKTE_EXAKT, PUNKTE_DIFFERENZ, PUNKTE_TENDENZ, *parameter),
    ).fetchall()
    sortiert = sorted(
        zeilen,
        key=lambda zeile: (
            -(zeile["tipp_punkte"] + (zeile["bonus_punkte"] if mit_bonus else 0)),
            -(zeile["exakt"] or 0),
            -(zeile["differenz"] or 0),
            zeile["anzeigename"].casefold(),
        ),
    )
    formketten = _tipp_formketten(conn)
    prognose = _live_prognose(conn, datum=datum, runde=runde)
    ergebnis = []
    platz = 0
    vorherige_punkte: int | None = None
    for index, zeile in enumerate(sortiert, start=1):
        punkte = zeile["tipp_punkte"] + (zeile["bonus_punkte"] if mit_bonus else 0)
        if punkte != vorherige_punkte:
            platz = index
            vorherige_punkte = punkte
        ergebnis.append(
            {
                "platz": platz,
                "nutzer_id": zeile["id"],
                "anzeigename": zeile["anzeigename"],
                "rolle": zeile["rolle"],
                "profilbild": zeile["profilbild"],
                "punkte": punkte,
                "punkte_live": prognose.get(zeile["id"], 0),
                "bonus_punkte": zeile["bonus_punkte"] if mit_bonus else 0,
                "tipps_gewertet": zeile["tipps_gewertet"],
                "exakt": zeile["exakt"] or 0,
                "differenz": zeile["differenz"] or 0,
                "tendenz": zeile["tendenz"] or 0,
                "form": formketten.get(zeile["id"], []),
            }
        )
    return ergebnis


def _tipp_formketten(conn: sqlite3.Connection, limit: int = 3) -> dict[int, list[int]]:
    """Punkte der letzten gewerteten Tipps je Nutzer, neueste zuerst —
    die "Formkette" in der Rangliste. Höchstens die letzten 3 Spiele, sonst
    bleibt auf schmalen Handys kein Platz mehr für den Namen (v0.3)."""
    zeilen = conn.execute(
        """
        SELECT nutzer_id, punkte FROM (
            SELECT t.nutzer_id, t.punkte,
                   ROW_NUMBER() OVER (
                       PARTITION BY t.nutzer_id ORDER BY s.anstoss_utc DESC, t.spiel_id DESC
                   ) AS reihe
            FROM tipp t JOIN spiel s ON s.id = t.spiel_id
            WHERE t.punkte IS NOT NULL
        ) WHERE reihe <= ?
        ORDER BY nutzer_id, reihe
        """,
        (limit,),
    ).fetchall()
    ketten: dict[int, list[int]] = {}
    for zeile in zeilen:
        ketten.setdefault(zeile["nutzer_id"], []).append(zeile["punkte"])
    return ketten
