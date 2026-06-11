from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import importer, nutzer as nutzer_service
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
def spielplan_und_nutzer(conn):
    importer.spielplan_importieren(conn, SPIELPLAN, akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="1234", rolle="admin", akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Tom", pin="1234", akteur="test")
    zukunft_id = conn.execute(
        "SELECT id FROM spiel WHERE anstoss_utc = ?", (ZUKUNFT,)
    ).fetchone()["id"]
    vergangen_id = conn.execute(
        "SELECT id FROM spiel WHERE anstoss_utc = ?", (VERGANGENHEIT,)
    ).fetchone()["id"]
    return {"zukunft": zukunft_id, "vergangen": vergangen_id}


def _als(client: TestClient, name: str) -> None:
    antwort = client.post("/api/login", json={"anzeigename": name, "pin": "1234"})
    assert antwort.status_code == 200


def test_spiele_ohne_login_gesperrt(client):
    assert client.get("/api/spiele").status_code == 401


def test_tippabgabe_und_aenderung(client, spielplan_und_nutzer):
    _als(client, "Mia")
    spiel_id = spielplan_und_nutzer["zukunft"]
    antwort = client.post(
        "/api/tipps", json={"spiel_id": spiel_id, "tipp_heim": 2, "tipp_gast": 1}
    )
    assert antwort.status_code == 200

    # Ändern bis Anpfiff erlaubt (SPEC 3.3)
    antwort = client.post(
        "/api/tipps", json={"spiel_id": spiel_id, "tipp_heim": 3, "tipp_gast": 0}
    )
    assert antwort.status_code == 200

    spiele = client.get("/api/spiele").json()
    spiel = next(s for s in spiele if s["id"] == spiel_id)
    assert spiel["mein_tipp"] == {"tipp_heim": 3, "tipp_gast": 0, "punkte": None}
    assert spiel["tippbar"] is True


def test_tipp_nach_anpfiff_gesperrt(client, spielplan_und_nutzer):
    _als(client, "Mia")
    antwort = client.post(
        "/api/tipps",
        json={"spiel_id": spielplan_und_nutzer["vergangen"], "tipp_heim": 1, "tipp_gast": 0},
    )
    assert antwort.status_code == 409


def test_tipp_unbekanntes_spiel(client, spielplan_und_nutzer):
    _als(client, "Mia")
    antwort = client.post("/api/tipps", json={"spiel_id": 999, "tipp_heim": 1, "tipp_gast": 0})
    assert antwort.status_code == 404


def test_fremde_tipps_erst_ab_anpfiff(client, conn, spielplan_und_nutzer):
    zukunft = spielplan_und_nutzer["zukunft"]
    vergangen = spielplan_und_nutzer["vergangen"]

    _als(client, "Tom")
    client.post("/api/tipps", json={"spiel_id": zukunft, "tipp_heim": 1, "tipp_gast": 1})
    # Tipp auf das laufende Spiel direkt einfügen (Abgabe wäre gesperrt)
    tom_id = conn.execute("SELECT id FROM nutzer WHERE anzeigename = 'Tom'").fetchone()["id"]
    conn.execute(
        "INSERT INTO tipp (nutzer_id, spiel_id, tipp_heim, tipp_gast, abgegeben_utc) VALUES (?, ?, 2, 0, ?)",
        (tom_id, vergangen, jetzt_iso()),
    )
    conn.commit()

    _als(client, "Mia")
    # Vor Anpfiff: Toms Tipp ist nicht sichtbar
    detail = client.get(f"/api/spiele/{zukunft}").json()
    assert all(tipp["anzeigename"] != "Tom" for tipp in detail["tipps"])
    # Nach Anpfiff: Tipps aller sichtbar
    detail = client.get(f"/api/spiele/{vergangen}").json()
    assert any(tipp["anzeigename"] == "Tom" for tipp in detail["tipps"])


def test_admin_ergebnis_und_rangliste(client, conn, spielplan_und_nutzer):
    vergangen = spielplan_und_nutzer["vergangen"]
    mia_id = conn.execute("SELECT id FROM nutzer WHERE anzeigename = 'Mia'").fetchone()["id"]
    tom_id = conn.execute("SELECT id FROM nutzer WHERE anzeigename = 'Tom'").fetchone()["id"]
    for nutzer_id, heim, gast in ((mia_id, 2, 1), (tom_id, 1, 0)):
        conn.execute(
            "INSERT INTO tipp (nutzer_id, spiel_id, tipp_heim, tipp_gast, abgegeben_utc) VALUES (?, ?, ?, ?, ?)",
            (nutzer_id, vergangen, heim, gast, jetzt_iso()),
        )
    conn.commit()

    _als(client, "Chef")
    antwort = client.post(
        f"/api/admin/spiele/{vergangen}/ergebnis",
        json={"tore_heim": 2, "tore_gast": 1, "status": "beendet", "ergebnis_nach": "90"},
    )
    assert antwort.status_code == 200
    assert antwort.json()["tipps_gewertet"] == 2

    rangliste = client.get("/api/rangliste").json()
    eintraege = {eintrag["anzeigename"]: eintrag for eintrag in rangliste}
    assert eintraege["Mia"]["punkte"] == 4  # exakt
    assert eintraege["Tom"]["punkte"] == 3  # richtige Tordifferenz (1:0 bei 2:1)
    assert eintraege["Mia"]["platz"] == 1
    assert eintraege["Chef"]["punkte"] == 0  # ohne Tipps trotzdem gelistet

    # Tagesliste: am Spieltag des vergangenen Spiels
    tag = client.get("/api/rangliste", params={"datum": "2020-06-15"}).json()
    eintraege_tag = {eintrag["anzeigename"]: eintrag for eintrag in tag}
    assert eintraege_tag["Mia"]["punkte"] == 4
    # Rundenliste
    runde = client.get("/api/rangliste", params={"runde": "Gruppe A"}).json()
    assert {e["anzeigename"]: e["punkte"] for e in runde}["Mia"] == 4


def test_admin_nur_fuer_admins(client, spielplan_und_nutzer):
    _als(client, "Mia")
    antwort = client.post(
        "/api/admin/nutzer", json={"anzeigename": "Neu", "pin": "123456", "rolle": "mitglied"}
    )
    assert antwort.status_code == 403


def test_admin_nutzer_anlegen(client, spielplan_und_nutzer):
    _als(client, "Chef")
    antwort = client.post(
        "/api/admin/nutzer", json={"anzeigename": "Neu", "pin": "567890", "rolle": "mitglied"}
    )
    assert antwort.status_code == 201
    doppelt = client.post(
        "/api/admin/nutzer", json={"anzeigename": "Neu", "pin": "567890", "rolle": "mitglied"}
    )
    assert doppelt.status_code == 409


def test_admin_spielplan_import_endpunkt(client, spielplan_und_nutzer):
    _als(client, "Chef")
    antwort = client.post(
        "/api/admin/spielplan-import",
        json={
            "teams": [{"fifa_code": "CAN", "name": "Kanada", "gruppe": "B"}],
            "spiele": [],
        },
    )
    assert antwort.status_code == 200
    assert antwort.json()["teams"] == 1
