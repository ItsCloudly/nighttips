"""Tests für den Quoten-Sync (The Odds API, v0.1.1).

Kein echtes HTTP: quoten_sync bekommt die API-Antwort als `daten`-Liste.
Geprüft werden Spiel-Zuordnung (Namen + Anstoßzeit), Upsert, Statusbuch
und die Auslieferung im Spiel-Detail.
"""
from __future__ import annotations

import pytest

from app.services import importer, nutzer as nutzer_service, quoten

ANSTOSS_A = "2030-06-15T18:00:00Z"
ANSTOSS_E = "2030-06-16T18:00:00Z"

SPIELPLAN = {
    "teams": [
        {"fifa_code": "USA", "name": "USA", "gruppe": "A"},
        {"fifa_code": "MEX", "name": "Mexiko", "gruppe": "A"},
        {"fifa_code": "GER", "name": "Deutschland", "gruppe": "E"},
        {"fifa_code": "SCO", "name": "Schottland", "gruppe": "E"},
    ],
    "spiele": [
        {"runde": "Gruppe A", "anstoss_utc": ANSTOSS_A, "heim": "USA", "gast": "MEX"},
        {"runde": "Gruppe E", "anstoss_utc": ANSTOSS_E, "heim": "GER", "gast": "SCO"},
    ],
}


def _event(heim: str, gast: str, anstoss: str, *, mit_quoten: bool = True) -> dict:
    event = {"home_team": heim, "away_team": gast, "commence_time": anstoss, "bookmakers": []}
    if mit_quoten:
        event["bookmakers"] = [
            {
                "key": "tipico_de",
                "title": "Tipico",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": heim, "price": 2.1},
                            {"name": "Draw", "price": 3.3},
                            {"name": gast, "price": 3.6},
                        ],
                    }
                ],
            }
        ]
    return event


@pytest.fixture
def welt(conn):
    importer.spielplan_importieren(conn, SPIELPLAN, akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="123456", akteur="t")
    conn.commit()
    spiele = {
        zeile["runde"]: zeile["id"]
        for zeile in conn.execute("SELECT id, runde FROM spiel").fetchall()
    }
    return {"gruppe_a": spiele["Gruppe A"], "gruppe_e": spiele["Gruppe E"]}


def test_quoten_sync_ordnet_spiele_zu(conn, einstellungen, welt):
    daten = [
        _event("USA", "Mexico", ANSTOSS_A),  # Namens-Match (engl. → dt.)
        _event("Germany", "Scotland", ANSTOSS_E),
        _event("Brazil", "France", "2031-01-01T00:00:00Z"),  # kein Spiel → keine Zuordnung
        _event("Japan", "Norway", ANSTOSS_A, mit_quoten=False),  # ohne Buchmacher-Quote
    ]
    bericht = quoten.quoten_sync(conn, einstellungen, daten=daten)
    assert bericht.events == 4
    assert bericht.aktualisiert == 2
    assert bericht.ohne_zuordnung == 1
    assert bericht.ohne_quoten == 1

    zeile = conn.execute(
        "SELECT anbieter, heim, remis, gast FROM quote WHERE spiel_id = ?",
        (welt["gruppe_a"],),
    ).fetchone()
    assert zeile["anbieter"] == "Tipico"
    assert (zeile["heim"], zeile["remis"], zeile["gast"]) == (2.1, 3.3, 3.6)

    status = conn.execute(
        "SELECT status FROM sync_status WHERE job = 'quoten'"
    ).fetchone()
    assert status["status"] == "ok"
    # Nach erfolgreichem Lauf ist der Job für heute durch
    assert quoten.abruf_faellig(conn) is False


def test_quoten_fallback_eindeutige_anstosszeit(conn, einstellungen, welt):
    """Unbekannte Teamnamen, aber nur EIN Spiel zu der Anstoßzeit → zuordnen."""
    daten = [_event("U.S.A.", "El Tri", ANSTOSS_A)]
    bericht = quoten.quoten_sync(conn, einstellungen, daten=daten)
    assert bericht.aktualisiert == 1
    assert conn.execute(
        "SELECT COUNT(*) AS anzahl FROM quote WHERE spiel_id = ?", (welt["gruppe_a"],)
    ).fetchone()["anzahl"] == 1


def test_quoten_fenster_fallback_bei_parallelen_anstoessen(conn, einstellungen):
    """Versetzte API-Anstoßzeit + paralleler Anstoß: Teamnamen im ±3-h-Fenster
    ordnen trotzdem zu. Der Eindeutigkeits-Fallback hilft bei zwei
    gleichzeitigen Spielen nicht — der Backlog-Fall Brasilien–Haiti /
    Bosnien–Katar blieb so ohne Quote."""
    anstoss = "2030-06-17T18:00:00Z"
    importer.spielplan_importieren(
        conn,
        {
            "teams": [
                {"fifa_code": "BRA", "name": "Brasilien", "gruppe": "C"},
                {"fifa_code": "HAI", "name": "Haiti", "gruppe": "C"},
                {"fifa_code": "BIH", "name": "Bosnien-Herzegowina", "gruppe": "C"},
                {"fifa_code": "QAT", "name": "Katar", "gruppe": "C"},
            ],
            "spiele": [
                {"runde": "Gruppe C", "anstoss_utc": anstoss, "heim": "BRA", "gast": "HAI"},
                {"runde": "Gruppe C", "anstoss_utc": anstoss, "heim": "BIH", "gast": "QAT"},
            ],
        },
        akteur="test",
    )
    conn.commit()
    versetzt = "2030-06-17T19:00:00Z"  # die API listet beide Spiele +1 h
    bericht = quoten.quoten_sync(
        conn,
        einstellungen,
        daten=[
            _event("Brazil", "Haiti", versetzt),
            _event("Bosnia and Herzegovina", "Qatar", versetzt),
        ],
    )
    assert bericht.aktualisiert == 2
    assert bericht.ohne_zuordnung == 0
    zugeordnet = {
        zeile["name"]
        for zeile in conn.execute(
            "SELECT th.name FROM quote q"
            " JOIN spiel s ON s.id = q.spiel_id"
            " JOIN team th ON th.id = s.heim_team_id"
        ).fetchall()
    }
    assert zugeordnet == {"Brasilien", "Bosnien-Herzegowina"}


def test_quoten_upsert_statt_duplikat(conn, einstellungen, welt):
    quoten.quoten_sync(conn, einstellungen, daten=[_event("USA", "Mexico", ANSTOSS_A)])
    zweiter = _event("USA", "Mexico", ANSTOSS_A)
    zweiter["bookmakers"][0]["markets"][0]["outcomes"][0]["price"] = 1.85
    quoten.quoten_sync(conn, einstellungen, daten=[zweiter])

    zeilen = conn.execute(
        "SELECT heim FROM quote WHERE spiel_id = ?", (welt["gruppe_a"],)
    ).fetchall()
    assert len(zeilen) == 1
    assert zeilen[0]["heim"] == 1.85


def test_quote_im_spiel_detail(client, conn, einstellungen, welt):
    quoten.quoten_sync(conn, einstellungen, daten=[_event("USA", "Mexico", ANSTOSS_A)])
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "123456"})
    detail = client.get(f"/api/spiele/{welt['gruppe_a']}").json()
    assert detail["quote"]["anbieter"] == "Tipico"
    assert detail["quote"]["heim"] == 2.1
    # Spiel ohne Quote liefert null
    assert client.get(f"/api/spiele/{welt['gruppe_e']}").json()["quote"] is None


def test_quoten_unter_eins_werden_verworfen(conn, einstellungen, welt):
    """Dezimalquoten < 1.0 sind Datenmüll — der Sync überspringt sie sauber."""
    kaputt = _event("USA", "Mexico", ANSTOSS_A)
    kaputt["bookmakers"][0]["markets"][0]["outcomes"][1]["price"] = 0.5
    bericht = quoten.quoten_sync(conn, einstellungen, daten=[kaputt])
    assert bericht.ohne_quoten == 1
    assert bericht.aktualisiert == 0
    assert conn.execute("SELECT COUNT(*) AS anzahl FROM quote").fetchone()["anzahl"] == 0


def test_quoten_aktiv_nur_mit_token(einstellungen):
    import dataclasses

    assert quoten.aktiv(einstellungen) is False
    mit_token = dataclasses.replace(einstellungen, quoten_token="abc123")
    assert quoten.aktiv(mit_token) is True
