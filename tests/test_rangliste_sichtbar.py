"""Tests für die Ranglisten-Sichtbarkeit pro Konto (v0.1.1).

Der Admin kann Konten (z. B. Test-/Dev-Profile) aus den Wertungs-Ansichten
nehmen: Rangliste, Podium und Top-Tipper zählen ohne sie (Plätze rücken auf).
Tipplisten im Spiel-Detail zeigen sie weiterhin — wer mittippt, bleibt beim
Spiel transparent.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import importer, nutzer as nutzer_service
from app.zeit import jetzt_iso

VERGANGENHEIT = "2020-06-15T18:00:00Z"

SPIELPLAN = {
    "teams": [
        {"fifa_code": "USA", "name": "USA", "gruppe": "A"},
        {"fifa_code": "MEX", "name": "Mexiko", "gruppe": "A"},
    ],
    "spiele": [
        {"runde": "Gruppe A", "anstoss_utc": VERGANGENHEIT, "heim": "USA", "gast": "MEX"},
    ],
}


@pytest.fixture
def welt(conn):
    """Spielplan + drei Profile: Admin, Mia und Dev (wird versteckt)."""
    importer.spielplan_importieren(conn, SPIELPLAN, akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="123456", rolle="admin", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="123456", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Dev", pin="123456", akteur="t")
    conn.commit()
    ids = {
        zeile["anzeigename"]: zeile["id"]
        for zeile in conn.execute("SELECT id, anzeigename FROM nutzer").fetchall()
    }
    spiel = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    return {"ids": ids, "spiel": spiel}


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


def _spiel_werten(client: TestClient, spiel_id: int) -> None:
    _als(client, "Chef")
    antwort = client.post(
        f"/api/admin/spiele/{spiel_id}/ergebnis",
        json={"tore_heim": 2, "tore_gast": 1, "status": "beendet", "ergebnis_nach": "90"},
    )
    assert antwort.status_code == 200


def test_admin_schaltet_sichtbarkeit_um(client, welt):
    _als(client, "Chef")
    dev = welt["ids"]["Dev"]
    liste = client.get("/api/admin/nutzer").json()
    assert all(person["rangliste_sichtbar"] is True for person in liste)

    antwort = client.patch(f"/api/admin/nutzer/{dev}", json={"rangliste_sichtbar": False})
    assert antwort.status_code == 200
    assert antwort.json()["rangliste_sichtbar"] is False

    personen = {p["anzeigename"]: p for p in client.get("/api/admin/nutzer").json()}
    assert personen["Dev"]["rangliste_sichtbar"] is False
    assert personen["Mia"]["rangliste_sichtbar"] is True


def test_sichtbarkeit_nur_fuer_admins(client, welt):
    _als(client, "Mia")
    antwort = client.patch(
        f"/api/admin/nutzer/{welt['ids']['Dev']}", json={"rangliste_sichtbar": False}
    )
    assert antwort.status_code == 403


def test_verstecktes_konto_fehlt_in_der_rangliste(client, conn, welt):
    spiel = welt["spiel"]
    # Dev exakt (4 P.), Mia Differenz (3 P.) — Dev läge eigentlich vorn
    _tipp_einfuegen(conn, welt["ids"]["Dev"], spiel, 2, 1)
    _tipp_einfuegen(conn, welt["ids"]["Mia"], spiel, 1, 0)
    _spiel_werten(client, spiel)

    _als(client, "Chef")
    client.patch(f"/api/admin/nutzer/{welt['ids']['Dev']}", json={"rangliste_sichtbar": False})

    # Für alle Profile (auch den Admin selbst): keine Dev-Zeile, Plätze rücken auf
    for name in ("Chef", "Mia"):
        _als(client, name)
        rangliste = client.get("/api/rangliste").json()
        namen = [eintrag["anzeigename"] for eintrag in rangliste]
        assert "Dev" not in namen
        assert rangliste[0]["anzeigename"] == "Mia"
        assert rangliste[0]["platz"] == 1
    # Tages- und Rundenwertung ebenso
    tag = client.get("/api/rangliste", params={"datum": "2020-06-15"}).json()
    assert all(eintrag["anzeigename"] != "Dev" for eintrag in tag)
    runde = client.get("/api/rangliste", params={"runde": "Gruppe A"}).json()
    assert all(eintrag["anzeigename"] != "Dev" for eintrag in runde)

    # Wieder einblenden: Dev zählt erneut und führt
    _als(client, "Chef")
    client.patch(f"/api/admin/nutzer/{welt['ids']['Dev']}", json={"rangliste_sichtbar": True})
    rangliste = client.get("/api/rangliste").json()
    assert rangliste[0]["anzeigename"] == "Dev"


def test_tippliste_zeigt_versteckte_weiterhin(client, conn, welt):
    spiel = welt["spiel"]
    _tipp_einfuegen(conn, welt["ids"]["Dev"], spiel, 2, 1)
    _tipp_einfuegen(conn, welt["ids"]["Mia"], spiel, 1, 0)
    _spiel_werten(client, spiel)
    _als(client, "Chef")
    client.patch(f"/api/admin/nutzer/{welt['ids']['Dev']}", json={"rangliste_sichtbar": False})

    _als(client, "Mia")
    detail = client.get(f"/api/spiele/{spiel}").json()
    tipps = {tipp["anzeigename"]: tipp for tipp in detail["tipps"]}
    # Transparenz beim Mittippen: Dev bleibt in der Tippliste sichtbar …
    assert "Dev" in tipps
    # … aber das Flag erlaubt dem Frontend, die Top-Tipper ohne Dev zu bauen.
    assert tipps["Dev"]["rangliste_sichtbar"] == 0
    assert tipps["Mia"]["rangliste_sichtbar"] == 1


def test_umschalten_landet_im_change_log(client, conn, welt):
    _als(client, "Chef")
    client.patch(f"/api/admin/nutzer/{welt['ids']['Dev']}", json={"rangliste_sichtbar": False})
    eintrag = conn.execute(
        "SELECT alt_wert, neu_wert, akteur FROM change_log"
        " WHERE entitaet = 'nutzer' AND feld = 'rangliste_sichtbar'"
    ).fetchone()
    assert eintrag is not None
    assert (eintrag["alt_wert"], eintrag["neu_wert"]) == ("1", "0")
    assert eintrag["akteur"] == "Chef"
