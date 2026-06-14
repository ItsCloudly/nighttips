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

    assert client.put(f"/api/pins/team/{team_id}").status_code == 204
    # v0.2: neue Spiel-Pins gibt es nicht mehr — Favoriten sind Teams
    assert client.put(f"/api/pins/spiel/{spiel_id}").status_code == 410
    assert client.put("/api/pins/team/99999").status_code == 404
    assert client.put(f"/api/pins/quatsch/{spiel_id}").status_code == 404

    pins = client.get("/api/pins").json()
    assert {(p["typ"], p["ref_id"]) for p in pins} == {("team", team_id)}

    spiele = client.get("/api/spiele").json()
    assert spiele[0]["team_gepinnt"] is True

    # Alt-Pins aus der Zeit vor v0.2 bleiben sichtbar und löschbar
    mia_id = conn.execute("SELECT id FROM nutzer WHERE anzeigename = 'Mia'").fetchone()["id"]
    _pin(conn, mia_id, "spiel", spiel_id)
    assert len(client.get("/api/pins").json()) == 2
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
    # Alt-Spiel-Pin (vor v0.2): ist stillgelegt und löst KEINE Pushes mehr aus
    nostalgiker = _nutzer_mit_abo(conn, "Nostalgiker", "https://push.example/nostalgiker")
    _pin(conn, nostalgiker, "spiel", spiel_id)

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


def test_tipp_erinnerung_persoenliche_vorlaufzeit(conn, push_einstellungen, gesendete):
    """v0.1.1: Jeder Nutzer hat sein eigenes Erinnerungs-Fenster (NULL = Standard 120,
    0 = abbestellt). Anstoß in 3 h: nur das 12-h-Fenster ist schon fällig."""
    match = _api_match()
    match["utcDate"] = iso_utc(jetzt_utc() + timedelta(minutes=180))
    sync.stammdaten_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [match]))

    frueh = _nutzer_mit_abo(conn, "Frueh", "https://push.example/frueh")  # 720 → fällig
    knapp = _nutzer_mit_abo(conn, "Knapp", "https://push.example/knapp")  # 30 → noch nicht
    nie = _nutzer_mit_abo(conn, "Nie", "https://push.example/nie")  # 0 → aus
    _nutzer_mit_abo(conn, "Standard", "https://push.example/standard")  # NULL → 120 → noch nicht
    with db_modul.schreib_transaktion(conn):
        conn.execute("UPDATE nutzer SET tipp_erinnerung_minuten = 720 WHERE id = ?", (frueh,))
        conn.execute("UPDATE nutzer SET tipp_erinnerung_minuten = 30 WHERE id = ?", (knapp,))
        conn.execute("UPDATE nutzer SET tipp_erinnerung_minuten = 0 WHERE id = ?", (nie,))

    assert push.erinnerungen_pruefen(conn, push_einstellungen) == 1
    assert gesendete[0][0] == "https://push.example/frueh"
    # Dedup über push_versand: zweiter Lauf schickt nichts Neues
    assert push.erinnerungen_pruefen(conn, push_einstellungen) == 0


def test_erinnerungs_einstellung_endpunkt(client, conn, einstellungen):
    """PATCH /api/me/einstellungen setzt die persönliche Vorlaufzeit."""
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="test")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    assert client.get("/api/me").json()["tipp_erinnerung_minuten"] is None

    antwort = client.patch("/api/me/einstellungen", json={"tipp_erinnerung_minuten": 45})
    assert antwort.status_code == 200
    assert client.get("/api/me").json()["tipp_erinnerung_minuten"] == 45
    # Grenzen: 0 (aus) ist erlaubt, mehr als 12 h nicht
    assert client.patch("/api/me/einstellungen", json={"tipp_erinnerung_minuten": 0}).status_code == 200
    assert (
        client.patch("/api/me/einstellungen", json={"tipp_erinnerung_minuten": 9999}).status_code
        == 422
    )


def test_anpfiff_erinnerung_fuer_pins(conn, push_einstellungen, gesendete):
    match = _api_match()
    match["utcDate"] = iso_utc(jetzt_utc() + timedelta(minutes=20))
    sync.stammdaten_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [match]))
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    team_ger = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    fan = _nutzer_mit_abo(conn, "Fan", "https://push.example/fan")
    _pin(conn, fan, "team", team_ger)
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


# ---------- v0.3: granulare Push-Vorlieben + Chat-Push ----------


def test_einstellungen_v3_teilupdate(client, conn):
    """PATCH ändert nur die übergebenen Felder; /api/me spiegelt sie."""
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="test")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    me = client.get("/api/me").json()
    assert me["anpfiff_erinnerung_minuten"] is None
    assert me["push_chat"] is False
    assert me["push_team_tore"] is True

    antwort = client.patch(
        "/api/me/einstellungen",
        json={"anpfiff_erinnerung_minuten": 30, "push_chat": True, "push_team_tore": False},
    )
    assert antwort.status_code == 200
    me = client.get("/api/me").json()
    assert me["anpfiff_erinnerung_minuten"] == 30
    assert me["push_chat"] is True
    assert me["push_team_tore"] is False
    # Nicht mitgeschickte Felder bleiben unverändert
    assert me["tipp_erinnerung_minuten"] is None
    # 0 (aus) erlaubt, mehr als 12 h nicht
    assert client.patch("/api/me/einstellungen", json={"anpfiff_erinnerung_minuten": 0}).status_code == 200
    assert (
        client.patch("/api/me/einstellungen", json={"anpfiff_erinnerung_minuten": 9999}).status_code
        == 422
    )


def test_tor_push_respektiert_schalter(conn, push_einstellungen, gesendete):
    """Wer Tore & Endstand abbestellt (push_team_tore=0), bekommt keine."""
    sync.stammdaten_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match()]))
    team_ger = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    fan = _nutzer_mit_abo(conn, "Fan", "https://push.example/fan")
    stumm = _nutzer_mit_abo(conn, "Stumm", "https://push.example/stumm")
    _pin(conn, fan, "team", team_ger)
    _pin(conn, stumm, "team", team_ger)
    with db_modul.schreib_transaktion(conn):
        conn.execute("UPDATE nutzer SET push_team_tore = 0 WHERE id = ?", (stumm,))

    live = _api_match()
    live["status"] = "IN_PLAY"
    live["score"] = {"winner": None, "duration": "REGULAR", "fullTime": {"home": 0, "away": 0}}
    sync.ergebnis_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [live]))
    tor = _api_match()
    tor["status"] = "IN_PLAY"
    tor["score"] = {"winner": None, "duration": "REGULAR", "fullTime": {"home": 1, "away": 0}}
    sync.ergebnis_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [tor]))

    assert [endpoint for endpoint, _ in gesendete] == ["https://push.example/fan"]


def test_anpfiff_persoenlicher_vorlauf(conn, push_einstellungen, gesendete):
    """Anpfiff-Vorlauf je Nutzer: NULL = Server-Standard (60), 0 = aus."""
    match = _api_match()
    match["utcDate"] = iso_utc(jetzt_utc() + timedelta(minutes=45))
    sync.stammdaten_sync(conn, push_einstellungen, api=ApiAttrappe(API_TEAMS, [match]))
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    team_ger = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    from app.services import tippspiel

    standard = _nutzer_mit_abo(conn, "Standard", "https://push.example/standard")  # NULL → 60 → fällig
    knapp = _nutzer_mit_abo(conn, "Knapp", "https://push.example/knapp")  # 30 → noch nicht
    aus = _nutzer_mit_abo(conn, "Aus", "https://push.example/aus")  # 0 → aus
    for nid in (standard, knapp, aus):
        _pin(conn, nid, "team", team_ger)
        # Tipp abgeben, damit die Tipp-Erinnerung den Anpfiff-Test nicht überlagert
        tippspiel.tipp_abgeben(conn, nutzer_id=nid, spiel_id=spiel_id, tipp_heim=1, tipp_gast=0)
    with db_modul.schreib_transaktion(conn):
        conn.execute("UPDATE nutzer SET anpfiff_erinnerung_minuten = 30 WHERE id = ?", (knapp,))
        conn.execute("UPDATE nutzer SET anpfiff_erinnerung_minuten = 0 WHERE id = ?", (aus,))

    assert push.erinnerungen_pruefen(conn, push_einstellungen) == 1
    assert [endpoint for endpoint, _ in gesendete] == ["https://push.example/standard"]
    assert "Anpfiff" in gesendete[0][1]
    # Dedup über push_versand: zweiter Lauf schickt nichts Neues
    assert push.erinnerungen_pruefen(conn, push_einstellungen) == 0


def test_chat_push_nur_abonnenten(conn, push_einstellungen, gesendete):
    """Chat-Push geht nur an push_chat=1 — nie an den Autor selbst."""
    from app.services import chat

    autor = _nutzer_mit_abo(conn, "Autor", "https://push.example/autor")
    leser = _nutzer_mit_abo(conn, "Leser", "https://push.example/leser")
    _nutzer_mit_abo(conn, "Stumm", "https://push.example/stumm")  # push_chat = 0 (Standard)
    with db_modul.schreib_transaktion(conn):
        conn.execute("UPDATE nutzer SET push_chat = 1 WHERE id IN (?, ?)", (autor, leser))
    nachricht_id = chat.nachricht_anlegen(conn, nutzer_id=autor, inhalt="Wer kommt heute?")

    gesendet = push.chat_benachrichtigen(
        push_einstellungen, nachricht_id=nachricht_id, autor_id=autor
    )
    assert gesendet == 1
    assert [endpoint for endpoint, _ in gesendete] == ["https://push.example/leser"]
    assert "Autor" in gesendete[0][1]  # Titel trägt den Namen
    # Dieselbe Nachricht erneut → Dedup, nichts Neues
    assert (
        push.chat_benachrichtigen(push_einstellungen, nachricht_id=nachricht_id, autor_id=autor)
        == 0
    )
