"""Tests für das KI-Gate (SPEC 5.2/5.4).

Ohne Freischaltung ist die App komplett KI-frei: keine KI-Tipps in Tipplisten,
keine KI-Zeile in Ranglisten (Plätze ohne den KI-Tipper durchgezählt), keine
KI-Antworten bei Bonusfragen. Freigeschaltete Profile, Admins und der
KI-Tipper selbst sehen alles unverändert.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import bonus, importer, nutzer as nutzer_service
from app.zeit import jetzt_iso

ZUKUNFT = "2030-06-15T18:00:00Z"
VERGANGENHEIT = "2020-06-15T18:00:00Z"

SPIELPLAN = {
    "teams": [
        {"fifa_code": "GER", "name": "Deutschland", "gruppe": "E"},
        {"fifa_code": "SCO", "name": "Schottland", "gruppe": "E"},
        {"fifa_code": "USA", "name": "USA", "gruppe": "A"},
        {"fifa_code": "MEX", "name": "Mexiko", "gruppe": "A"},
    ],
    "spiele": [
        {"runde": "Gruppe E", "anstoss_utc": ZUKUNFT, "heim": "GER", "gast": "SCO"},
        {"runde": "Gruppe A", "anstoss_utc": VERGANGENHEIT, "heim": "USA", "gast": "MEX"},
    ],
}


@pytest.fixture
def welt(conn):
    """Spielplan + vier Profile: Admin, Mia (KI aus), Ben (KI an), Claude (KI-Tipper)."""
    importer.spielplan_importieren(conn, SPIELPLAN, akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="123456", rolle="admin", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="123456", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Ben", pin="123456", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Claude", pin="123456", rolle="ki", akteur="t")
    conn.execute("UPDATE nutzer SET ki_freigeschaltet = 1 WHERE anzeigename = 'Ben'")
    conn.commit()
    ids = {
        zeile["anzeigename"]: zeile["id"]
        for zeile in conn.execute("SELECT id, anzeigename FROM nutzer").fetchall()
    }
    spiele = {
        "zukunft": conn.execute(
            "SELECT id FROM spiel WHERE anstoss_utc = ?", (ZUKUNFT,)
        ).fetchone()["id"],
        "vergangen": conn.execute(
            "SELECT id FROM spiel WHERE anstoss_utc = ?", (VERGANGENHEIT,)
        ).fetchone()["id"],
    }
    return {"ids": ids, "spiele": spiele}


def _als(client: TestClient, name: str, pin: str = "123456") -> None:
    antwort = client.post("/api/login", json={"anzeigename": name, "pin": pin})
    assert antwort.status_code == 200


def _tipp_einfuegen(conn, nutzer_id: int, spiel_id: int, heim: int, gast: int) -> None:
    conn.execute(
        "INSERT INTO tipp (nutzer_id, spiel_id, tipp_heim, tipp_gast, abgegeben_utc)"
        " VALUES (?, ?, ?, ?, ?)",
        (nutzer_id, spiel_id, heim, gast, jetzt_iso()),
    )
    conn.commit()


def test_tippliste_vor_anpfiff(client, conn, welt):
    zukunft = welt["spiele"]["zukunft"]
    _tipp_einfuegen(conn, welt["ids"]["Claude"], zukunft, 2, 1)
    _tipp_einfuegen(conn, welt["ids"]["Mia"], zukunft, 1, 0)

    # Mia (KI aus): nur der eigene Tipp, kein KI-Tipp
    _als(client, "Mia")
    tipps = client.get(f"/api/spiele/{zukunft}").json()["tipps"]
    assert [t["anzeigename"] for t in tipps] == ["Mia"]

    # Ben (KI an): eigener Tipp fehlt (nicht abgegeben), KI-Tipp sichtbar
    _als(client, "Ben")
    tipps = client.get(f"/api/spiele/{zukunft}").json()["tipps"]
    assert [t["anzeigename"] for t in tipps] == ["Claude"]

    # Admin sieht den KI-Tipp ebenfalls
    _als(client, "Chef")
    tipps = client.get(f"/api/spiele/{zukunft}").json()["tipps"]
    assert any(t["rolle"] == "ki" for t in tipps)


def test_tippliste_nach_anpfiff(client, conn, welt):
    vergangen = welt["spiele"]["vergangen"]
    _tipp_einfuegen(conn, welt["ids"]["Claude"], vergangen, 2, 1)
    _tipp_einfuegen(conn, welt["ids"]["Mia"], vergangen, 1, 0)
    _tipp_einfuegen(conn, welt["ids"]["Ben"], vergangen, 0, 0)

    # Mia (KI aus): alle menschlichen Tipps, aber kein KI-Tipp
    _als(client, "Mia")
    tipps = client.get(f"/api/spiele/{vergangen}").json()["tipps"]
    assert {t["anzeigename"] for t in tipps} == {"Mia", "Ben"}

    # Ben (KI an): KI-Tipp inklusive
    _als(client, "Ben")
    tipps = client.get(f"/api/spiele/{vergangen}").json()["tipps"]
    assert {t["anzeigename"] for t in tipps} == {"Mia", "Ben", "Claude"}


def test_rangliste_ohne_ki_neu_durchgezaehlt(client, conn, welt):
    vergangen = welt["spiele"]["vergangen"]
    # Claude exakt (4 P.), Mia Differenz (3 P.), Ben daneben (0 P.)
    _tipp_einfuegen(conn, welt["ids"]["Claude"], vergangen, 2, 1)
    _tipp_einfuegen(conn, welt["ids"]["Mia"], vergangen, 1, 0)
    _tipp_einfuegen(conn, welt["ids"]["Ben"], vergangen, 0, 0)

    _als(client, "Chef")
    antwort = client.post(
        f"/api/admin/spiele/{vergangen}/ergebnis",
        json={"tore_heim": 2, "tore_gast": 1, "status": "beendet", "ergebnis_nach": "90"},
    )
    assert antwort.status_code == 200

    # Admin-Welt: KI führt die Liste an
    rangliste = client.get("/api/rangliste").json()
    assert rangliste[0]["anzeigename"] == "Claude"
    assert rangliste[0]["platz"] == 1

    # Mia-Welt (KI aus): keine KI-Zeile, Plätze rücken auf
    _als(client, "Mia")
    rangliste = client.get("/api/rangliste").json()
    namen = [eintrag["anzeigename"] for eintrag in rangliste]
    assert "Claude" not in namen
    assert rangliste[0]["anzeigename"] == "Mia"
    assert rangliste[0]["platz"] == 1
    # Auch Tages- und Rundenliste bleiben KI-frei
    tag = client.get("/api/rangliste", params={"datum": "2020-06-15"}).json()
    assert all(eintrag["rolle"] != "ki" for eintrag in tag)
    runde = client.get("/api/rangliste", params={"runde": "Gruppe A"}).json()
    assert all(eintrag["rolle"] != "ki" for eintrag in runde)

    # Ben-Welt (KI an): KI-Zeile vorhanden, Mensch dahinter
    _als(client, "Ben")
    rangliste = client.get("/api/rangliste").json()
    assert rangliste[0]["anzeigename"] == "Claude"
    assert rangliste[1]["anzeigename"] == "Mia"
    assert rangliste[1]["platz"] == 2


def test_bonusfragen_ohne_ki(client, conn, welt):
    team_id = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    frage_id = bonus.frage_anlegen(
        conn,
        frage="Wer wird Weltmeister?",
        typ="team",
        punkte_wert=10,
        einsendeschluss_utc=VERGANGENHEIT,
    )
    conn.execute(
        "INSERT INTO bonustipp (nutzer_id, bonusfrage_id, antwort_ref, abgegeben_utc)"
        " VALUES (?, ?, ?, ?), (?, ?, ?, ?)",
        (
            welt["ids"]["Claude"], frage_id, team_id, jetzt_iso(),
            welt["ids"]["Mia"], frage_id, team_id, jetzt_iso(),
        ),
    )
    conn.commit()

    # Mia (KI aus): die KI-Antwort fehlt
    _als(client, "Mia")
    fragen = client.get("/api/bonusfragen").json()
    assert [t["anzeigename"] for t in fragen[0]["tipps"]] == ["Mia"]

    # Ben (KI an): die KI-Antwort ist sichtbar
    _als(client, "Ben")
    fragen = client.get("/api/bonusfragen").json()
    assert {t["anzeigename"] for t in fragen[0]["tipps"]} == {"Mia", "Claude"}


def test_ki_tipper_sieht_sich_selbst(client, conn, welt):
    vergangen = welt["spiele"]["vergangen"]
    _tipp_einfuegen(conn, welt["ids"]["Claude"], vergangen, 2, 1)

    _als(client, "Claude")
    tipps = client.get(f"/api/spiele/{vergangen}").json()["tipps"]
    assert any(t["rolle"] == "ki" for t in tipps)
    rangliste = client.get("/api/rangliste").json()
    assert any(eintrag["rolle"] == "ki" for eintrag in rangliste)
