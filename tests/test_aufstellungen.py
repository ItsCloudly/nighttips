"""Tests für den Aufstellungs-Sync (inoffizielle ESPN-API, v0.1.1).

Kein echtes HTTP: scoreboard/summary werden injiziert. Geprüft werden
Event-Zuordnung, Spieler-Matching (Trikotnummer/Name/Nachname), das
Zurückhalten unvollständiger Aufstellungen und die „übliche Startelf".
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.services import aufstellungen, importer, nutzer as nutzer_service
from app.zeit import iso_utc, jetzt_utc

ANSTOSS = iso_utc(jetzt_utc() + timedelta(minutes=60))

SPIELPLAN = {
    "teams": [
        {"fifa_code": "USA", "name": "USA", "gruppe": "A"},
        {"fifa_code": "MEX", "name": "Mexiko", "gruppe": "A"},
    ],
    "spiele": [
        {"runde": "Gruppe A", "anstoss_utc": ANSTOSS, "heim": "USA", "gast": "MEX"},
    ],
}

POSITIONEN = ["Torwart"] + ["Abwehr"] * 4 + ["Mittelfeld"] * 3 + ["Sturm"] * 3 + ["Mittelfeld"] * 3


def _kader_anlegen(conn, team_id: int, praefix: str) -> None:
    """14 Spieler: Nummern 1–14, realistische Positionsverteilung."""
    for nummer in range(1, 15):
        conn.execute(
            "INSERT INTO spieler (team_id, name, trikotnummer, position)"
            " VALUES (?, ?, ?, ?)",
            (team_id, f"{praefix} Spieler{nummer}", nummer, POSITIONEN[nummer - 1]),
        )


@pytest.fixture
def welt(conn):
    importer.spielplan_importieren(conn, SPIELPLAN, akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="123456", akteur="t")
    teams = {
        zeile["fifa_code"]: zeile["id"]
        for zeile in conn.execute("SELECT id, fifa_code FROM team").fetchall()
    }
    _kader_anlegen(conn, teams["USA"], "Usa")
    _kader_anlegen(conn, teams["MEX"], "Mex")
    conn.commit()
    spiel = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    return {"spiel": spiel, "usa": teams["USA"], "mex": teams["MEX"]}


def _scoreboard() -> dict:
    return {
        "events": [
            {
                "id": "401",
                "date": ANSTOSS,
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "team": {"displayName": "USA"}},
                            {"homeAway": "away", "team": {"displayName": "Mexico"}},
                        ]
                    }
                ],
            }
        ]
    }


def _roster(praefix: str, *, starter: int = 11, bank: int = 3) -> dict:
    eintraege = []
    for nummer in range(1, starter + bank + 1):
        eintraege.append(
            {
                "starter": nummer <= starter,
                "jersey": str(nummer),
                "athlete": {"displayName": f"{praefix} Spieler{nummer}"},
                "position": {"abbreviation": "M"},
            }
        )
    return {"roster": eintraege, "formation": "4-3-3"}


def _summary(*, usa_starter: int = 11, mex_starter: int = 11) -> dict:
    return {
        "rosters": [
            {"homeAway": "home", **_roster("Usa", starter=usa_starter)},
            {"homeAway": "away", **_roster("Mex", starter=mex_starter)},
        ]
    }


def test_sync_ordnet_zu_und_speichert(conn, einstellungen, welt):
    bericht = aufstellungen.aufstellungen_sync(
        conn, einstellungen, scoreboard=_scoreboard(), summary_lader=lambda ref: _summary()
    )
    assert bericht.geprueft == 1
    assert bericht.gespeichert == 2
    assert conn.execute("SELECT espn_ref FROM spiel").fetchone()["espn_ref"] == "401"
    starter = conn.execute(
        "SELECT COUNT(*) AS anzahl FROM aufstellung WHERE rolle = 'startelf'"
    ).fetchone()["anzahl"]
    bank = conn.execute(
        "SELECT COUNT(*) AS anzahl FROM aufstellung WHERE rolle = 'bank'"
    ).fetchone()["anzahl"]
    assert (starter, bank) == (22, 6)
    # Spiel ist versorgt → kein Kandidat mehr, zweiter Lauf prüft nichts
    zweiter = aufstellungen.aufstellungen_sync(
        conn, einstellungen, scoreboard=_scoreboard(), summary_lader=lambda ref: _summary()
    )
    assert zweiter.geprueft == 0


def test_unvollstaendige_aufstellung_wartet(conn, einstellungen, welt):
    """Vor der Veröffentlichung (weniger als 11 Starter) wird nichts gespeichert."""
    bericht = aufstellungen.aufstellungen_sync(
        conn,
        einstellungen,
        scoreboard=_scoreboard(),
        summary_lader=lambda ref: _summary(usa_starter=4, mex_starter=11),
    )
    assert bericht.unvollstaendig == 1
    assert bericht.gespeichert == 1  # Mexiko war schon komplett
    usa_zeilen = conn.execute(
        "SELECT COUNT(*) AS anzahl FROM aufstellung WHERE team_id = ?", (welt["usa"],)
    ).fetchone()["anzahl"]
    assert usa_zeilen == 0
    # Später kommt die volle Elf → nachgetragen
    nachzug = aufstellungen.aufstellungen_sync(
        conn, einstellungen, scoreboard=_scoreboard(), summary_lader=lambda ref: _summary()
    )
    assert nachzug.gespeichert >= 1


def test_spieler_matching_ueber_nachnamen(conn, einstellungen, welt):
    """Ohne Trikotnummer zählt der (eindeutige) Nachname."""
    summary = _summary()
    eintrag = summary["rosters"][0]["roster"][0]
    eintrag["jersey"] = None
    eintrag["athlete"]["displayName"] = "U. Spieler1"
    bericht = aufstellungen.aufstellungen_sync(
        conn, einstellungen, scoreboard=_scoreboard(), summary_lader=lambda ref: summary
    )
    assert bericht.gespeichert == 2
    assert bericht.spieler_unbekannt == 0


def test_uebliche_startelf_und_team_endpoint(client, conn, einstellungen, welt):
    aufstellungen.aufstellungen_sync(
        conn, einstellungen, scoreboard=_scoreboard(), summary_lader=lambda ref: _summary()
    )
    startelf = aufstellungen.uebliche_startelf(conn, welt["usa"])
    assert startelf is not None
    assert len(startelf["spieler"]) == 11
    assert startelf["formation"] == "4-3-3"
    assert startelf["spiele_basis"] == 1
    # Ohne Daten: None (Team-Lupe fällt auf den Kader zurück)
    conn.execute("DELETE FROM aufstellung WHERE team_id = ?", (welt["mex"],))
    conn.commit()
    assert aufstellungen.uebliche_startelf(conn, welt["mex"]) is None

    client.post("/api/login", json={"anzeigename": "Mia", "pin": "123456"})
    team = client.get(f"/api/teams/{welt['usa']}").json()
    assert len(team["startelf"]["spieler"]) == 11
    assert client.get(f"/api/teams/{welt['mex']}").json()["startelf"] is None


def test_duell_aufstellungen_im_spiel_detail(client, conn, einstellungen, welt):
    """Das Spiel-Detail liefert beide Startelfen fürs Duell-Feld."""
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "123456"})
    # Vor dem Sync: Sektion bleibt leer
    assert client.get(f"/api/spiele/{welt['spiel']}").json()["aufstellungen"] is None

    aufstellungen.aufstellungen_sync(
        conn, einstellungen, scoreboard=_scoreboard(), summary_lader=lambda ref: _summary()
    )
    detail = client.get(f"/api/spiele/{welt['spiel']}").json()
    duell = detail["aufstellungen"]
    assert len(duell["heim"]["startelf"]) == 11
    assert len(duell["gast"]["startelf"]) == 11
    assert duell["heim"]["formation"] == "4-3-3"
    assert duell["heim"]["startelf"][0]["position"] == "Torwart"


def test_iso_normalisierung():
    assert aufstellungen._iso_normalisieren("2026-06-11T19:00Z") == "2026-06-11T19:00:00Z"
    assert aufstellungen._iso_normalisieren("2026-06-11T19:00:00Z") == "2026-06-11T19:00:00Z"
