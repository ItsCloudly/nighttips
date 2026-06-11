"""Tests für Profilbilder und Namensänderung (v0.1.1).

Uploads werden mit Pillow verifiziert und neu kodiert; ausgeliefert wird
nur an angemeldete Nutzer. Namensänderung wahrt die Eindeutigkeit.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services import nutzer as nutzer_service


@pytest.fixture
def nutzerpaar(conn):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="123456", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Ben", pin="123456", akteur="t")
    conn.commit()
    return {
        zeile["anzeigename"]: zeile["id"]
        for zeile in conn.execute("SELECT id, anzeigename FROM nutzer").fetchall()
    }


def _als(client, name, pin="123456"):
    assert client.post("/api/login", json={"anzeigename": name, "pin": pin}).status_code == 200


def _png(breite=600, hoehe=400, farbe=(200, 30, 30)) -> bytes:
    puffer = io.BytesIO()
    Image.new("RGB", (breite, hoehe), farbe).save(puffer, "PNG")
    return puffer.getvalue()


def test_pin_wechseln(client, conn, nutzerpaar):
    _als(client, "Mia")
    # Falsche aktuelle PIN → 403, nichts passiert
    antwort = client.post("/api/me/pin", json={"alte_pin": "999999", "neue_pin": "neuespin7"})
    assert antwort.status_code == 403
    assert client.get("/api/me").status_code == 200  # Sitzung lebt noch

    # Zu kurze neue PIN scheitert an der Validierung
    assert (
        client.post("/api/me/pin", json={"alte_pin": "123456", "neue_pin": "kurz"}).status_code
        == 422
    )

    # Erfolgreicher Wechsel: alle Sitzungen enden, Login nur noch mit neuer PIN
    antwort = client.post("/api/me/pin", json={"alte_pin": "123456", "neue_pin": "neuespin7"})
    assert antwort.status_code == 200
    assert client.get("/api/me").status_code == 401
    assert (
        client.post("/api/login", json={"anzeigename": "Mia", "pin": "123456"}).status_code == 401
    )
    _als(client, "Mia", pin="neuespin7")
    # Protokolliert ohne Klartext
    eintrag = conn.execute(
        "SELECT alt_wert, neu_wert FROM change_log WHERE feld = 'pin_hash'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert (eintrag["alt_wert"], eintrag["neu_wert"]) == ("(geheim)", "(geheim)")


def test_pin_wechsel_rate_limit(client, conn, nutzerpaar):
    _als(client, "Mia")
    for _ in range(5):
        client.post("/api/me/pin", json={"alte_pin": "999999", "neue_pin": "neuespin7"})
    antwort = client.post("/api/me/pin", json={"alte_pin": "123456", "neue_pin": "neuespin7"})
    assert antwort.status_code == 429


def test_profilbild_hochladen_und_abrufen(client, conn, nutzerpaar):
    _als(client, "Mia")
    antwort = client.put("/api/me/profilbild", content=_png())
    assert antwort.status_code == 200
    dateiname = antwort.json()["profilbild"]
    assert dateiname.endswith(".webp")
    assert client.get("/api/me").json()["profilbild"] == dateiname

    # Re-Encode: ausgeliefert wird ein 256x256-WebP
    bild_antwort = client.get(f"/api/profilbilder/{nutzerpaar['Mia']}")
    assert bild_antwort.status_code == 200
    assert bild_antwort.headers["content-type"] == "image/webp"
    bild = Image.open(io.BytesIO(bild_antwort.content))
    assert bild.size == (256, 256)

    # Andere angemeldete Mitspieler sehen das Bild auch
    _als(client, "Ben")
    assert client.get(f"/api/profilbilder/{nutzerpaar['Mia']}").status_code == 200


def test_profilbild_braucht_login(client, conn, nutzerpaar):
    _als(client, "Mia")
    client.put("/api/me/profilbild", content=_png())
    mia = nutzerpaar["Mia"]
    client.post("/api/logout")
    assert client.get(f"/api/profilbilder/{mia}").status_code == 401


def test_profilbild_validierung(client, conn, nutzerpaar):
    _als(client, "Mia")
    assert client.put("/api/me/profilbild", content=b"kein bild").status_code == 422
    zu_gross = b"x" * (2 * 1024 * 1024 + 1)
    assert client.put("/api/me/profilbild", content=zu_gross).status_code == 413
    assert client.get(f"/api/profilbilder/{nutzerpaar['Mia']}").status_code == 404


def test_profilbild_entfernen(client, conn, nutzerpaar, einstellungen):
    from app.services import profilbilder

    _als(client, "Mia")
    dateiname = client.put("/api/me/profilbild", content=_png()).json()["profilbild"]
    assert (profilbilder.verzeichnis(einstellungen) / dateiname).is_file()
    assert client.delete("/api/me/profilbild").status_code == 204
    assert client.get(f"/api/profilbilder/{nutzerpaar['Mia']}").status_code == 404
    assert not (profilbilder.verzeichnis(einstellungen) / dateiname).is_file()


def test_admin_entfernt_profilbild(client, conn, nutzerpaar):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="123456", rolle="admin", akteur="t")
    conn.commit()
    _als(client, "Mia")
    client.put("/api/me/profilbild", content=_png())
    # Mitglieder dürfen den Moderations-Endpunkt nicht nutzen
    assert client.delete(f"/api/admin/nutzer/{nutzerpaar['Ben']}/profilbild").status_code == 403
    _als(client, "Chef")
    assert client.delete(f"/api/admin/nutzer/{nutzerpaar['Mia']}/profilbild").status_code == 204
    assert client.get(f"/api/profilbilder/{nutzerpaar['Mia']}").status_code == 404


def test_name_aendern(client, conn, nutzerpaar):
    _als(client, "Mia")
    antwort = client.post("/api/me/name", json={"anzeigename": "Mia die Große"})
    assert antwort.status_code == 200
    assert client.get("/api/me").json()["anzeigename"] == "Mia die Große"
    # Kollision (case-insensitiv) wird abgewiesen
    assert client.post("/api/me/name", json={"anzeigename": "ben"}).status_code == 409
    # Änderung steht in der Historie
    eintrag = conn.execute(
        "SELECT alt_wert, neu_wert FROM change_log"
        " WHERE entitaet = 'nutzer' AND feld = 'anzeigename'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert (eintrag["alt_wert"], eintrag["neu_wert"]) == ("Mia", "Mia die Große")
    # Login mit dem neuen Namen funktioniert
    client.post("/api/logout")
    _als(client, "Mia die Große")
