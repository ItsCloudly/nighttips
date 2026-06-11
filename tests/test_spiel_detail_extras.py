"""Tests für die Spiel-Lupe-Zusatzdaten:
Tipp-Verteilung (Heim/Remis/Auswärts) und Formketten der Teams.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import importer, nutzer as nutzer_service
from app.zeit import jetzt_iso

ZUKUNFT = "2030-06-15T18:00:00Z"
ALT_1 = "2020-06-10T18:00:00Z"
ALT_2 = "2020-06-14T18:00:00Z"

SPIELPLAN = {
    "teams": [
        {"fifa_code": "GER", "name": "Deutschland", "gruppe": "E"},
        {"fifa_code": "SCO", "name": "Schottland", "gruppe": "E"},
        {"fifa_code": "HUN", "name": "Ungarn", "gruppe": "E"},
    ],
    "spiele": [
        {"runde": "Gruppe E", "anstoss_utc": ALT_1, "heim": "GER", "gast": "SCO"},
        {"runde": "Gruppe E", "anstoss_utc": ALT_2, "heim": "HUN", "gast": "GER"},
        {"runde": "Gruppe E", "anstoss_utc": ZUKUNFT, "heim": "GER", "gast": "HUN"},
    ],
}


@pytest.fixture
def welt(conn):
    importer.spielplan_importieren(conn, SPIELPLAN, akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="123456", rolle="admin", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="123456", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Claude", pin="123456", rolle="ki", akteur="t")
    # GER gewinnt 2:0, verliert dann 0:1 gegen HUN
    conn.execute(
        "UPDATE spiel SET status='beendet', tore_heim=2, tore_gast=0 WHERE anstoss_utc = ?",
        (ALT_1,),
    )
    conn.execute(
        "UPDATE spiel SET status='beendet', tore_heim=1, tore_gast=0 WHERE anstoss_utc = ?",
        (ALT_2,),
    )
    conn.commit()
    ids = {
        zeile["anzeigename"]: zeile["id"]
        for zeile in conn.execute("SELECT id, anzeigename FROM nutzer").fetchall()
    }
    zukunft = conn.execute("SELECT id FROM spiel WHERE anstoss_utc = ?", (ZUKUNFT,)).fetchone()["id"]
    return {"ids": ids, "zukunft": zukunft}


def _als(client: TestClient, name: str) -> None:
    assert client.post("/api/login", json={"anzeigename": name, "pin": "123456"}).status_code == 200


def _tipp(conn, nutzer_id: int, spiel_id: int, heim: int, gast: int) -> None:
    conn.execute(
        "INSERT INTO tipp (nutzer_id, spiel_id, tipp_heim, tipp_gast, abgegeben_utc)"
        " VALUES (?, ?, ?, ?, ?)",
        (nutzer_id, spiel_id, heim, gast, jetzt_iso()),
    )
    conn.commit()


def test_tipp_verteilung_aggregiert_ohne_einzeltipps(client, conn, welt):
    spiel_id = welt["zukunft"]
    _tipp(conn, welt["ids"]["Chef"], spiel_id, 2, 1)   # heim
    _tipp(conn, welt["ids"]["Mia"], spiel_id, 1, 1)    # remis
    _tipp(conn, welt["ids"]["Claude"], spiel_id, 2, 0)  # heim (KI)

    # Admin sieht die KI in der Verteilung
    _als(client, "Chef")
    detail = client.get(f"/api/spiele/{spiel_id}").json()
    assert detail["tipp_verteilung"] == {"heim": 2, "remis": 1, "gast": 0, "gesamt": 3}

    # Mia (KI aus): Verteilung ohne den KI-Tipp, eigene Tippliste unverändert klein
    _als(client, "Mia")
    detail = client.get(f"/api/spiele/{spiel_id}").json()
    assert detail["tipp_verteilung"] == {"heim": 1, "remis": 1, "gast": 0, "gesamt": 2}
    assert [t["anzeigename"] for t in detail["tipps"]] == ["Mia"]


def test_formkette_neueste_zuerst(client, conn, welt):
    _als(client, "Chef")
    detail = client.get(f"/api/spiele/{welt['zukunft']}").json()
    # GER: zuletzt 0:1 verloren (N), davor 2:0 gewonnen (S)
    assert detail["form"]["heim"] == ["N", "S"]
    # HUN: zuletzt 1:0 gewonnen (S)
    assert detail["form"]["gast"] == ["S"]


def test_rangliste_formkette(client, conn, welt):
    """Rangliste liefert die Punkte der letzten gewerteten Tipps (Formkette)."""
    from app.services import tippspiel

    alt_1 = conn.execute("SELECT id FROM spiel WHERE anstoss_utc = ?", (ALT_1,)).fetchone()["id"]
    alt_2 = conn.execute("SELECT id FROM spiel WHERE anstoss_utc = ?", (ALT_2,)).fetchone()["id"]
    # Mia: exakt auf Spiel 1 (4 P.), daneben auf Spiel 2 (0 P.)
    conn.execute(
        "INSERT INTO tipp (nutzer_id, spiel_id, tipp_heim, tipp_gast, abgegeben_utc, punkte)"
        " VALUES (?, ?, 2, 0, ?, 4), (?, ?, 0, 2, ?, 0)",
        (welt["ids"]["Mia"], alt_1, jetzt_iso(), welt["ids"]["Mia"], alt_2, jetzt_iso()),
    )
    conn.commit()
    eintraege = tippspiel.rangliste(conn)
    mia = next(e for e in eintraege if e["anzeigename"] == "Mia")
    # Neueste zuerst: Spiel 2 (0 P.) liegt zeitlich nach Spiel 1 (4 P.)
    assert mia["form"] == [0, 4]
    chef = next(e for e in eintraege if e["anzeigename"] == "Chef")
    assert chef["form"] == []
