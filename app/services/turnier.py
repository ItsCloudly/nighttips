"""Turnierstand-Ableitungen aus den Spieldaten.

Ausgeschieden ist ein Team, wenn es ein beendetes K.o.-Spiel verloren hat,
oder wenn die Gruppenphase komplett gespielt ist und das Team in keinem
K.o.-Spiel (mit gesetzten Teams) vorkommt. Bewusst aus den Spielen abgeleitet
statt als Flag gepflegt — funktioniert so auch für andere Wettbewerbe
(z. B. Ligen ohne K.o.-Runde: dort scheidet schlicht niemand aus).

Dazu: Schlagwort-Kategorien für News (Verletzung, Wechsel, Aufstellung …),
zur Abfragezeit berechnet, damit sich die Wortlisten ohne Migration
weiterentwickeln lassen.
"""
from __future__ import annotations

import re
import sqlite3


def _sieger_verlierer(spiel: sqlite3.Row) -> tuple[int | None, int | None]:
    if spiel["elfmeter_sieger_team_id"] is not None:
        sieger = spiel["elfmeter_sieger_team_id"]
        verlierer = (
            spiel["gast_team_id"] if sieger == spiel["heim_team_id"] else spiel["heim_team_id"]
        )
        return sieger, verlierer
    if spiel["tore_heim"] is None or spiel["tore_gast"] is None:
        return None, None
    if spiel["tore_heim"] > spiel["tore_gast"]:
        return spiel["heim_team_id"], spiel["gast_team_id"]
    if spiel["tore_gast"] > spiel["tore_heim"]:
        return spiel["gast_team_id"], spiel["heim_team_id"]
    return None, None


def ausgeschiedene_teams(conn: sqlite3.Connection) -> set[int]:
    """Team-IDs, die nicht mehr im Turnier sind."""
    raus: set[int] = set()
    ko_spiele = conn.execute(
        "SELECT runde, status, heim_team_id, gast_team_id, tore_heim, tore_gast,"
        " elfmeter_sieger_team_id FROM spiel WHERE runde NOT LIKE 'Gruppe %'"
    ).fetchall()
    # K.o.-Verlierer (Spiel um Platz 3 entscheidet nicht über Ausscheiden —
    # beide stehen schon fest als ausgeschieden aus dem Halbfinale).
    for spiel in ko_spiele:
        if spiel["status"] != "beendet" or spiel["runde"] == "Spiel um Platz 3":
            continue
        _, verlierer = _sieger_verlierer(spiel)
        if verlierer is not None:
            raus.add(verlierer)
    # Gruppenphase vorbei? Dann sind alle raus, die in keinem K.o.-Spiel stehen.
    offene_gruppenspiele = conn.execute(
        "SELECT COUNT(*) AS n FROM spiel WHERE runde LIKE 'Gruppe %'"
        " AND status NOT IN ('beendet', 'abgesagt')"
    ).fetchone()["n"]
    gruppen_gespielt = conn.execute(
        "SELECT COUNT(*) AS n FROM spiel WHERE runde LIKE 'Gruppe %' AND status = 'beendet'"
    ).fetchone()["n"]
    if offene_gruppenspiele == 0 and gruppen_gespielt > 0:
        im_ko: set[int] = set()
        for spiel in ko_spiele:
            for feld in ("heim_team_id", "gast_team_id"):
                if spiel[feld] is not None:
                    im_ko.add(spiel[feld])
        # Nur anwenden, wenn die K.o.-Paarungen überhaupt schon gesetzt sind.
        if im_ko:
            alle = {
                zeile["id"] for zeile in conn.execute("SELECT id FROM team").fetchall()
            }
            raus |= alle - im_ko
    return raus


# Ein Artikel kann mehrere Tags tragen; die Reihenfolge bestimmt die Anzeige-Priorität.
NEWS_TAGS: list[tuple[str, re.Pattern]] = [
    (
        "Verletzung",
        re.compile(
            r"verletz|ausfall|fällt aus|fallen aus|muskel|bänder|kreuzband|angeschlagen"
            r"|fraglich|reha|operation|operiert|gesperrt|sperre",
            re.IGNORECASE,
        ),
    ),
    (
        "Wechsel",
        re.compile(
            r"wechsel|transfer|verpflicht|unterschreib|ablöse|leihe|verlässt|neuzugang",
            re.IGNORECASE,
        ),
    ),
    (
        "Aufstellung",
        re.compile(
            r"aufstellung|startelf|kader|nominier|bank|formation|system",
            re.IGNORECASE,
        ),
    ),
    (
        "Ergebnis",
        re.compile(r"sieg|niederlage|remis|unentschieden|gewinnt|verliert|\d+:\d+", re.IGNORECASE),
    ),
]


def news_tags(text: str) -> list[str]:
    return [tag for tag, muster in NEWS_TAGS if muster.search(text)]
