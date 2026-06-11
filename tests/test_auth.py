from __future__ import annotations

import pytest

from app.services import nutzer as nutzer_service


@pytest.fixture
def alex_id(conn) -> int:
    return nutzer_service.nutzer_anlegen(
        conn, anzeigename="Alex", pin="1234", rolle="admin", akteur="test"
    )


def test_login_und_me(client, conn, alex_id):
    antwort = client.post("/api/login", json={"anzeigename": "Alex", "pin": "1234"})
    assert antwort.status_code == 200
    assert antwort.json()["anzeigename"] == "Alex"
    assert antwort.json()["rolle"] == "admin"
    assert "wm26_session" in antwort.cookies

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["id"] == alex_id


def test_login_name_gross_klein_egal(client, conn, alex_id):
    # COLLATE NOCASE auf anzeigename: exakter Treffer unabhängig von Schreibweise
    antwort = client.post("/api/login", json={"anzeigename": "alex", "pin": "1234"})
    assert antwort.status_code == 200


def test_login_falsche_pin(client, conn, alex_id):
    antwort = client.post("/api/login", json={"anzeigename": "Alex", "pin": "9999"})
    assert antwort.status_code == 401


def test_login_unbekannter_name(client, conn):
    antwort = client.post("/api/login", json={"anzeigename": "Niemand", "pin": "1234"})
    assert antwort.status_code == 401


def test_lockout_nach_fehlversuchen(client, conn, alex_id):
    for _ in range(3):  # einstellungen.login_max_fehlversuche = 3
        client.post("/api/login", json={"anzeigename": "Alex", "pin": "9999"})
    gesperrt = client.post("/api/login", json={"anzeigename": "Alex", "pin": "1234"})
    assert gesperrt.status_code == 429
    assert "Retry-After" in gesperrt.headers


def test_logout(client, conn, alex_id):
    client.post("/api/login", json={"anzeigename": "Alex", "pin": "1234"})
    assert client.get("/api/me").status_code == 200
    antwort = client.post("/api/logout")
    assert antwort.status_code == 204
    assert client.get("/api/me").status_code == 401


def test_me_ohne_login(client):
    assert client.get("/api/me").status_code == 401


def test_nutzer_anlegen_validierung(conn):
    with pytest.raises(ValueError):
        nutzer_service.nutzer_anlegen(conn, anzeigename="Kurz", pin="12", akteur="test")
    with pytest.raises(ValueError):
        nutzer_service.nutzer_anlegen(conn, anzeigename="", pin="1234", akteur="test")
    with pytest.raises(ValueError):
        nutzer_service.nutzer_anlegen(conn, anzeigename="X", pin="12 34", akteur="test")


def test_nutzer_doppelter_name(conn):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Doppel", pin="1234", akteur="test")
    with pytest.raises(ValueError, match="bereits vergeben"):
        nutzer_service.nutzer_anlegen(conn, anzeigename="doppel", pin="5678", akteur="test")


def test_change_log_bei_nutzeranlage(conn, alex_id):
    eintrag = conn.execute(
        "SELECT * FROM change_log WHERE entitaet = 'nutzer' AND entitaet_id = ?", (alex_id,)
    ).fetchone()
    assert eintrag is not None
    # quelle ist bei manueller Anlage immer "admin", akteur ist der Aufrufer
    assert eintrag["quelle"] == "admin"
    assert eintrag["akteur"] == "test"


def test_health_liefert_version(client):
    antwort = client.get("/api/health")
    assert antwort.status_code == 200
    daten = antwort.json()
    assert daten["status"] == "ok"
    assert daten["version"].count(".") == 2
