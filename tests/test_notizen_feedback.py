"""Tests für private Spiel-Notizen und das Feedback-Postfach (v0.1.1).

Notizen sind strikt privat (nur der Verfasser sieht sie), Feedback landet
im Admin-Posteingang und ist gegen Spam rate-limitiert.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import importer, nutzer as nutzer_service

SPIELPLAN = {
    "teams": [
        {"fifa_code": "USA", "name": "USA", "gruppe": "A"},
        {"fifa_code": "MEX", "name": "Mexiko", "gruppe": "A"},
    ],
    "spiele": [
        {"runde": "Gruppe A", "anstoss_utc": "2030-06-15T18:00:00Z", "heim": "USA", "gast": "MEX"},
    ],
}


@pytest.fixture
def welt(conn):
    importer.spielplan_importieren(conn, SPIELPLAN, akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="123456", rolle="admin", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="123456", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Ben", pin="123456", akteur="t")
    conn.commit()
    return {"spiel": conn.execute("SELECT id FROM spiel").fetchone()["id"]}


def _als(client: TestClient, name: str, pin: str = "123456") -> None:
    antwort = client.post("/api/login", json={"anzeigename": name, "pin": pin})
    assert antwort.status_code == 200


# ---------- Notizen ----------


def test_notiz_anlegen_aendern_loeschen(client, welt):
    spiel = welt["spiel"]
    _als(client, "Mia")
    assert client.get(f"/api/notizen/{spiel}").json()["notiz"] is None

    antwort = client.put(f"/api/notizen/{spiel}", json={"text": "Mexiko wackelt hinten."})
    assert antwort.status_code == 200
    assert antwort.json()["text"] == "Mexiko wackelt hinten."

    # Upsert: zweites Speichern überschreibt, statt zu duplizieren
    client.put(f"/api/notizen/{spiel}", json={"text": "Doch lieber Remis tippen."})
    notiz = client.get(f"/api/notizen/{spiel}").json()["notiz"]
    assert notiz["text"] == "Doch lieber Remis tippen."

    # Notiz hängt am Spiel-Detail und als Flag an der Spieleliste
    detail = client.get(f"/api/spiele/{spiel}").json()
    assert detail["notiz"]["text"] == "Doch lieber Remis tippen."
    liste = client.get("/api/spiele").json()
    assert liste[0]["hat_notiz"] is True

    assert client.delete(f"/api/notizen/{spiel}").status_code == 204
    assert client.get(f"/api/notizen/{spiel}").json()["notiz"] is None
    assert client.get("/api/spiele").json()[0]["hat_notiz"] is False


def test_notiz_ist_privat(client, welt):
    spiel = welt["spiel"]
    _als(client, "Mia")
    client.put(f"/api/notizen/{spiel}", json={"text": "Geheim: Bauchgefühl sagt 3:0."})

    _als(client, "Ben")
    assert client.get(f"/api/notizen/{spiel}").json()["notiz"] is None
    assert client.get(f"/api/spiele/{spiel}").json()["notiz"] is None
    assert client.get("/api/spiele").json()[0]["hat_notiz"] is False


def test_notiz_validierung(client, welt):
    spiel = welt["spiel"]
    _als(client, "Mia")
    assert client.put(f"/api/notizen/{spiel}", json={"text": ""}).status_code == 422
    assert client.put(f"/api/notizen/{spiel}", json={"text": "x" * 2001}).status_code == 422
    assert client.put("/api/notizen/99999", json={"text": "gibt es nicht"}).status_code == 404


def test_notiz_braucht_login(client, welt):
    assert client.get(f"/api/notizen/{welt['spiel']}").status_code == 401


# ---------- Feedback ----------


def test_feedback_senden_und_posteingang(client, welt):
    _als(client, "Mia")
    antwort = client.post(
        "/api/feedback", json={"kategorie": "fehler", "nachricht": "Der Ticker hängt bei Spiel 3."}
    )
    assert antwort.status_code == 201

    # Mitglieder kommen nicht an den Posteingang
    assert client.get("/api/admin/feedback").status_code == 403

    _als(client, "Chef")
    eintraege = client.get("/api/admin/feedback").json()
    assert len(eintraege) == 1
    eintrag = eintraege[0]
    assert eintrag["anzeigename"] == "Mia"
    assert eintrag["kategorie"] == "fehler"
    assert eintrag["status"] == "offen"

    # Erledigt-Toggle und Löschen
    umgeschaltet = client.post(f"/api/admin/feedback/{eintrag['id']}/umschalten").json()
    assert umgeschaltet["status"] == "erledigt"
    assert client.post(f"/api/admin/feedback/{eintrag['id']}/umschalten").json()["status"] == "offen"
    assert client.delete(f"/api/admin/feedback/{eintrag['id']}").status_code == 204
    assert client.get("/api/admin/feedback").json() == []


def test_feedback_offene_zuerst(client, welt):
    _als(client, "Mia")
    for nummer in (1, 2):
        client.post(
            "/api/feedback", json={"kategorie": "idee", "nachricht": f"Idee Nummer {nummer}"}
        )
    _als(client, "Chef")
    erster = client.get("/api/admin/feedback").json()[0]
    client.post(f"/api/admin/feedback/{erster['id']}/umschalten")
    eintraege = client.get("/api/admin/feedback").json()
    assert [eintrag["status"] for eintrag in eintraege] == ["offen", "erledigt"]


def test_feedback_validierung_und_ratelimit(client, welt):
    _als(client, "Mia")
    assert (
        client.post("/api/feedback", json={"kategorie": "quatsch", "nachricht": "Hallo Welt"})
        .status_code
        == 422
    )
    assert (
        client.post("/api/feedback", json={"kategorie": "idee", "nachricht": "ab"}).status_code
        == 422
    )
    # Spamschutz: ab der sechsten Meldung pro Stunde ist Schluss
    for nummer in range(5):
        antwort = client.post(
            "/api/feedback", json={"kategorie": "idee", "nachricht": f"Meldung {nummer}"}
        )
        assert antwort.status_code == 201
    gebremst = client.post(
        "/api/feedback", json={"kategorie": "idee", "nachricht": "Eine zu viel"}
    )
    assert gebremst.status_code == 429
