"""Tests für Pins und Web-Push (SPEC 5.5).

Der eigentliche Versand (pywebpush) wird gemockt — geprüft werden Empfänger-
Auswahl (Pins), Doppelversand-Schutz und das Aufräumen toter Abos.
"""
from __future__ import annotations

import dataclasses
from datetime import timedelta

import pytest

from app import db as db_modul
from app.services import nutzer as nutzer_service, push, sync
from app.zeit import iso_utc, jetzt_utc
from tests.test_grunddaten import API_TEAMS, ApiAttrappe, _api_match


@pytest.fixture
def push_einstellungen(einstellungen):
    return dataclasses.replace(
        einstellungen,
        vapid_private_key="test-privat",
        vapid_public_key="test-oeffentlich",
        vapid_subject="mailto:test@example.org",
    )


@pytest.fixture
def gesendete(monkeypatch):
    """Fängt webpush-Aufrufe ab; Liste der (endpoint, payload)-Paare."""
    aufrufe: list[tuple[str, str]] = []

    def fake_webpush(subscription_info, data, **kwargs):
        aufrufe.append((subscription_info["endpoint"], data))

    import pywebpush

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    return aufrufe


def _nutzer_mit_abo(conn, name: str, endpoint: str) -> int:
    nutzer_id = nutzer_service.nutzer_anlegen(conn, anzeigename=name, pin="1234", akteur="test")
    push.subscription_speichern(
        conn, nutzer_id=nutzer_id, endpoint=endpoint, p256dh="schluessel", auth="geheim"
    )
    return nutzer_id


def _pin(conn, nutzer_id: int, typ: str, ref_id: int) -> None:
    with db_modul.schreib_transaktion(conn):
        conn.execute(
            "INSERT INTO pin (nutzer_id, typ, ref_id, erstellt_utc) VALUES (?, ?, ?, ?)",
            (nutzer_id, typ, ref_id, "2026-06-10T00:00:00Z"),
        )


def test_pin_endpunkte(client, conn, einstellungen):
    api = ApiAttrappe(API_TEAMS, [_api_match()])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="test")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    team_id = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]

    assert client.put(f"/api/pins/spiel/{spiel_id}").status_code == 204
    assert client.put(f"/api/pins/team/{team_id}").status_code == 204
    assert client.put("/api/pins/spiel/99999").status_code == 404
    assert client.put(f"/api/pins/quatsch/{spiel_id}").status_code == 404

    pins = client.get("/api/pins").json()
    assert {(p["typ"], p["ref_id"]) for p in pins} == {("spiel", spiel_id), ("team", team_id)}

    spiele = client.get("/api/spiele").json()
    assert spiele[0]["gepinnt"] is True
    assert spiele[0]["team_gepinnt"] is True

    assert client.delete(f"/api/pins/spiel/{spiel_id}").status_code == 204
    assert len(client.get("/api/pins").json()) == 1


def test_tor_push_nur_an_pin_nutzer(conn, push_einstellungen, gesendete):
    api = ApiAttrappe(API_TEAMS, [_api_match()])
    sync.stammdaten_sync(conn, push_einstellungen, api=api)
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    team_ger = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]

    fan = _nutzer_mit_abo(conn, "Fan", "https://push.example/fan")
    _nutzer_mit_abo(conn, "Ignorant", "https://push.example/ignorant")
    _pin(conn, fan, "team", team_ger)

    # Anpfiff + Tor
    live_match = _api_match()
    live_match["status"] = "IN_PLAY"
    live_match["score"] = {"winner": None, "duration": "REGULAR", "fullTime": {"home": 0, "away": 0}}
    sync.ergebnis_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [live_match]))
    tor_match = _api_match()
    tor_match["status"] = "IN_PLAY"
    tor_match["score"] = {"winner": None, "duration": "REGULAR", "fullTime": {"home": 1, "away": 0}}
    sync.ergebnis_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [tor_match]))

    assert len(gesendete) == 1
    endpoint, payload = gesendete[0]
    assert endpoint == "https://push.example/fan"
    assert "Tor" in payload

    # Gleicher Stand nochmal gesynct -> kein Doppelversand
    sync.ergebnis_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [tor_match]))
    assert len(gesendete) == 1

    # Endstand
    ende = _api_match()
    ende["status"] = "FINISHED"
    ende["score"] = {"winner": "HOME_TEAM", "duration": "REGULAR", "fullTime": {"home": 1, "away": 0}}
    sync.ergebnis_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [ende]))
    assert len(gesendete) == 2
    assert "Endstand" in gesendete[1][1]


def test_tipp_erinnerung_fuer_alle_ohne_tipp(conn, push_einstellungen, gesendete, einstellungen):
    match = _api_match()
    match["utcDate"] = iso_utc(jetzt_utc() + timedelta(minutes=90))
    sync.stammdaten_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [match]))
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]

    tipper = _nutzer_mit_abo(conn, "Tipper", "https://push.example/tipper")
    _nutzer_mit_abo(conn, "Schnarcher", "https://push.example/schnarcher")
    from app.services import tippspiel

    tippspiel.tipp_abgeben(conn, nutzer_id=tipper, spiel_id=spiel_id, tipp_heim=2, tipp_gast=1)

    gesendet = push.erinnerungen_pruefen(conn, push_einstellungen)
    assert gesendet == 1
    assert gesendete[0][0] == "https://push.example/schnarcher"
    assert "tippen" in gesendete[0][1]

    # Zweiter Lauf: kein Doppelversand
    assert push.erinnerungen_pruefen(conn, push_einstellungen) == 0


def test_anpfiff_erinnerung_fuer_pins(conn, push_einstellungen, gesendete):
    match = _api_match()
    match["utcDate"] = iso_utc(jetzt_utc() + timedelta(minutes=20))
    sync.stammdaten_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [match]))
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    fan = _nutzer_mit_abo(conn, "Fan", "https://push.example/fan")
    _pin(conn, fan, "spiel", spiel_id)
    from app.services import tippspiel

    tippspiel.tipp_abgeben(conn, nutzer_id=fan, spiel_id=spiel_id, tipp_heim=2, tipp_gast=1)

    gesendet = push.erinnerungen_pruefen(conn, push_einstellungen)
    assert gesendet == 1
    assert "Anpfiff" in gesendete[0][1]


def test_totes_abo_wird_entfernt(conn, push_einstellungen, monkeypatch):
    import pywebpush

    class FakeResponse:
        status_code = 410

    def kaputt(subscription_info, data, **kwargs):
        raise pywebpush.WebPushException("Gone", response=FakeResponse())

    monkeypatch.setattr(pywebpush, "webpush", kaputt)
    nutzer_id = _nutzer_mit_abo(conn, "Wechsler", "https://push.example/alt")
    push.senden(
        conn, push_einstellungen, nutzer_ids=[nutzer_id], anlass="test", ref_id=1,
        titel="T", text="x",
    )
    rest = conn.execute("SELECT COUNT(*) AS n FROM push_subscription").fetchone()["n"]
    assert rest == 0


def test_push_endpunkte(client, conn, push_einstellungen, einstellungen, monkeypatch):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="test")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})

    # Ohne VAPID-Konfiguration: Schlüssel leer, Subscribe 503
    info = client.get("/api/push/vapid-key").json()
    assert info["aktiv"] is False
    abo = {"endpoint": "https://push.example/geraet1", "keys": {"p256dh": "a", "auth": "b"}}
    assert client.post("/api/push/subscribe", json=abo).status_code == 503

    # Mit Konfiguration
    client.app.state.einstellungen = push_einstellungen
    assert client.post("/api/push/subscribe", json=abo).status_code == 204
    anzahl = conn.execute("SELECT COUNT(*) AS n FROM push_subscription").fetchone()["n"]
    assert anzahl == 1
    assert (
        client.post("/api/push/unsubscribe", json={"endpoint": abo["endpoint"]}).status_code
        == 204
    )
    anzahl = conn.execute("SELECT COUNT(*) AS n FROM push_subscription").fetchone()["n"]
    assert anzahl == 0
