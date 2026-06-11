"""Tests für Registrierung (Gruppen-Passwort) und Admin-Nutzerverwaltung
(Löschen, PIN-Reset, KI-Freischaltung)."""
from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import db as db_modul
from app.main import create_app
from app.services import nutzer as nutzer_service

GRUPPEN_PASSWORT = "geheim123"


@pytest.fixture
def reg_einstellungen(einstellungen):
    return dataclasses.replace(einstellungen, registrierung_passwort=GRUPPEN_PASSWORT)


@pytest.fixture
def reg_client(reg_einstellungen) -> Iterator[TestClient]:
    app = create_app(reg_einstellungen)
    with TestClient(app) as test_client:
        yield test_client


# ---------- Registrierung ----------


def test_registrierung_erfolgreich(reg_client):
    antwort = reg_client.post(
        "/api/registrieren",
        json={"anzeigename": "Neuling", "pin": "471147", "gruppen_passwort": GRUPPEN_PASSWORT},
    )
    assert antwort.status_code == 201
    daten = antwort.json()
    assert daten["rolle"] == "mitglied"
    assert daten["ki_freigeschaltet"] is False
    # Direkt angemeldet: Session-Cookie gesetzt, /api/me funktioniert
    assert "wm26_session" in antwort.cookies
    assert reg_client.get("/api/me").status_code == 200


def test_registrierung_falsches_gruppenpasswort(reg_client):
    antwort = reg_client.post(
        "/api/registrieren",
        json={"anzeigename": "Neuling", "pin": "471147", "gruppen_passwort": "falsch"},
    )
    assert antwort.status_code == 403
    assert reg_client.get("/api/me").status_code == 401


def test_registrierung_deaktiviert_ohne_passwort(client):
    # Standard-Einstellungen: registrierung_passwort leer → Registrierung aus
    antwort = client.post(
        "/api/registrieren",
        json={"anzeigename": "Neuling", "pin": "471147", "gruppen_passwort": "egal"},
    )
    assert antwort.status_code == 403
    assert "deaktiviert" in antwort.json()["detail"]


def test_registrierung_doppelter_name(reg_client):
    json = {"anzeigename": "Doppel", "pin": "471147", "gruppen_passwort": GRUPPEN_PASSWORT}
    assert reg_client.post("/api/registrieren", json=json).status_code == 201
    reg_client.post("/api/logout")
    assert reg_client.post("/api/registrieren", json=json).status_code == 409


def test_registrierung_sperre_nach_fehlversuchen(reg_client):
    # einstellungen.login_max_fehlversuche = 3 (conftest)
    for _ in range(3):
        reg_client.post(
            "/api/registrieren",
            json={"anzeigename": "Rater", "pin": "471147", "gruppen_passwort": "falsch"},
        )
    gesperrt = reg_client.post(
        "/api/registrieren",
        json={"anzeigename": "Rater", "pin": "471147", "gruppen_passwort": GRUPPEN_PASSWORT},
    )
    assert gesperrt.status_code == 429
    assert "Retry-After" in gesperrt.headers


# ---------- Admin-Nutzerverwaltung ----------


@pytest.fixture
def admin_client(client, conn):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Alex", pin="1234", rolle="admin", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="t")
    client.post("/api/login", json={"anzeigename": "Alex", "pin": "1234"})
    return client


def _nutzer_id(conn, name: str) -> int:
    return conn.execute("SELECT id FROM nutzer WHERE anzeigename = ?", (name,)).fetchone()["id"]


def test_nutzerliste_enthaelt_ki_flag(admin_client):
    nutzer = admin_client.get("/api/admin/nutzer").json()
    assert {person["anzeigename"] for person in nutzer} == {"Alex", "Mia"}
    assert all(person["ki_freigeschaltet"] is False for person in nutzer)


def test_ki_freischalten_und_entziehen(admin_client, conn):
    mia_id = _nutzer_id(conn, "Mia")
    antwort = admin_client.patch(f"/api/admin/nutzer/{mia_id}", json={"ki_freigeschaltet": True})
    assert antwort.status_code == 200
    assert antwort.json()["ki_freigeschaltet"] is True

    # Mia sieht die Freischaltung in /api/me
    admin_client.post("/api/logout")
    admin_client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    assert admin_client.get("/api/me").json()["ki_freigeschaltet"] is True

    admin_client.post("/api/logout")
    admin_client.post("/api/login", json={"anzeigename": "Alex", "pin": "1234"})
    antwort = admin_client.patch(f"/api/admin/nutzer/{mia_id}", json={"ki_freigeschaltet": False})
    assert antwort.json()["ki_freigeschaltet"] is False


def test_pin_reset(admin_client, conn):
    mia_id = _nutzer_id(conn, "Mia")
    assert (
        admin_client.patch(f"/api/admin/nutzer/{mia_id}", json={"pin": "neu-pin-99"}).status_code
        == 200
    )
    admin_client.post("/api/logout")
    assert (
        admin_client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"}).status_code
        == 401
    )
    assert (
        admin_client.post(
            "/api/login", json={"anzeigename": "Mia", "pin": "neu-pin-99"}
        ).status_code
        == 200
    )


def test_pin_reset_validierung(admin_client, conn):
    mia_id = _nutzer_id(conn, "Mia")
    antwort = admin_client.patch(f"/api/admin/nutzer/{mia_id}", json={"pin": "mit leerzeichen"})
    assert antwort.status_code == 422


def test_nutzer_loeschen(admin_client, conn):
    mia_id = _nutzer_id(conn, "Mia")
    assert admin_client.delete(f"/api/admin/nutzer/{mia_id}").status_code == 204
    namen = {person["anzeigename"] for person in admin_client.get("/api/admin/nutzer").json()}
    assert "Mia" not in namen
    # Historie festgehalten
    eintrag = conn.execute(
        "SELECT * FROM change_log WHERE entitaet = 'nutzer' AND feld = 'geloescht'"
        " AND entitaet_id = ?",
        (mia_id,),
    ).fetchone()
    assert eintrag is not None and eintrag["alt_wert"] == "Mia"


def test_selbstloeschung_verboten(admin_client, conn):
    alex_id = _nutzer_id(conn, "Alex")
    assert admin_client.delete(f"/api/admin/nutzer/{alex_id}").status_code == 409


def test_unbekannter_nutzer_404(admin_client):
    assert admin_client.delete("/api/admin/nutzer/9999").status_code == 404
    assert (
        admin_client.patch("/api/admin/nutzer/9999", json={"ki_freigeschaltet": True}).status_code
        == 404
    )


def test_nutzerverwaltung_nur_fuer_admins(client, conn):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="t")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    assert client.get("/api/admin/nutzer").status_code == 403
    assert client.delete("/api/admin/nutzer/1").status_code == 403
    assert (
        client.patch("/api/admin/nutzer/1", json={"ki_freigeschaltet": True}).status_code == 403
    )


def test_wettbewerbe_liste(client, conn):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="t")
    assert client.get("/api/wettbewerbe").status_code == 401
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    wettbewerbe = client.get("/api/wettbewerbe").json()
    codes = {eintrag["code"]: eintrag["aktiv"] for eintrag in wettbewerbe}
    assert codes == {"WC": True, "BL1": False}
