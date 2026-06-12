"""Tests für die Nutzerprofil-Ansicht (v0.2): Kopf, Statistik, Abzeichen
und die Tipp-Historie mit Geheimhaltung (nur angepfiffene Spiele)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import abzeichen, importer, nutzer as nutzer_service, tippspiel
from app.zeit import jetzt_iso

ALT_1 = "2020-06-10T18:00:00Z"
ALT_2 = "2020-06-14T18:00:00Z"
ZUKUNFT = "2030-06-15T18:00:00Z"

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


def _tipp(conn, nutzer_id: int, spiel_id: int, heim: int, gast: int) -> None:
    conn.execute(
        "INSERT INTO tipp (nutzer_id, spiel_id, tipp_heim, tipp_gast, abgegeben_utc)"
        " VALUES (?, ?, ?, ?, ?)",
        (nutzer_id, spiel_id, heim, gast, jetzt_iso()),
    )
    conn.commit()


@pytest.fixture
def welt(conn, einstellungen):
    importer.spielplan_importieren(conn, SPIELPLAN, akteur="test")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="123456", rolle="admin", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="123456", akteur="t")
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
    spiele = {
        zeile["anstoss_utc"]: zeile["id"]
        for zeile in conn.execute("SELECT id, anstoss_utc FROM spiel").fetchall()
    }
    # Mia: exakt (4 P) + daneben (0 P) + ein Zukunfts-Tipp (muss verborgen bleiben)
    _tipp(conn, ids["Mia"], spiele[ALT_1], 2, 0)
    _tipp(conn, ids["Mia"], spiele[ALT_2], 0, 1)
    _tipp(conn, ids["Mia"], spiele[ZUKUNFT], 1, 1)
    # Chef: richtige Tendenz am ersten Tag (2 P) — Mia bleibt Tagessiegerin
    _tipp(conn, ids["Chef"], spiele[ALT_1], 1, 0)
    tippspiel.spiel_auswerten(conn, spiele[ALT_1], einstellungen, akteur="test")
    tippspiel.spiel_auswerten(conn, spiele[ALT_2], einstellungen, akteur="test")
    return {"ids": ids, "spiele": spiele}


def _als(client: TestClient, name: str) -> None:
    assert client.post("/api/login", json={"anzeigename": name, "pin": "123456"}).status_code == 200


def test_profil_endpunkt(client, conn, welt):
    _als(client, "Chef")
    profil = client.get(f"/api/profil/{welt['ids']['Mia']}").json()

    assert profil["nutzer"]["anzeigename"] == "Mia"
    assert profil["rangliste"]["platz"] == 1
    assert profil["rangliste"]["punkte"] == 4
    assert profil["rangliste"]["exakt"] == 1

    # Geheimhaltung: der Tipp zum geplanten Spiel fehlt; neueste zuerst
    assert [t["punkte"] for t in profil["tipps"]] == [0, 4]
    assert all(t["status"] != "geplant" for t in profil["tipps"])
    assert profil["tipps"][1]["heim_code"] == "GER"
    assert profil["tipps"][1]["tipp_heim"] == 2

    # Abzeichen: Tagessiegerin (Tag 1 gewonnen), Exakt-Profi noch nicht (1/3)
    abz = {a["schluessel"]: a for a in profil["abzeichen"]}
    assert abz["tagessieger"]["erreicht"] is True
    assert abz["tagessieger"]["wert"] == 1
    assert abz["exakt_profi"]["erreicht"] is False
    assert abz["exakt_profi"]["wert"] == 1


def test_profil_unbekannt_und_auth(client, welt):
    assert client.get("/api/profil/1").status_code == 401
    _als(client, "Mia")
    assert client.get("/api/profil/99999").status_code == 404


def test_profil_ki_gate(client, conn, welt):
    """Review-Fix v0.2: das KI-Profil sehen nur Freigeschaltete (sonst 404)."""
    nutzer_service.nutzer_anlegen(conn, anzeigename="Orakel", pin="123456", rolle="ki", akteur="t")
    conn.commit()
    ki_id = conn.execute(
        "SELECT id FROM nutzer WHERE anzeigename = 'Orakel'"
    ).fetchone()["id"]
    _als(client, "Mia")  # nicht freigeschaltet
    assert client.get(f"/api/profil/{ki_id}").status_code == 404
    _als(client, "Chef")  # Admin sieht die KI
    assert client.get(f"/api/profil/{ki_id}").status_code == 200


def test_profil_verbirgt_abgesagtes_spiel_vor_anstoss(client, conn, welt):
    """Review-Fix v0.2: Absage vor Anpfiff verrät die Tipps nicht vorzeitig."""
    conn.execute("UPDATE spiel SET status='abgesagt' WHERE anstoss_utc = ?", (ZUKUNFT,))
    conn.commit()
    _als(client, "Chef")
    profil = client.get(f"/api/profil/{welt['ids']['Mia']}").json()
    assert len(profil["tipps"]) == 2  # weiterhin nur die zwei gespielten


def test_abzeichen_frueher_vogel_am_schwellen_tag(conn, welt):
    """Review-Fix v0.2: 26 h vor Anstoß zählt auch dann, wenn Abgabe und
    24-h-Schwelle auf denselben Kalendertag fallen (datetime()-Formatfalle)."""
    conn.execute(
        "UPDATE tipp SET abgegeben_utc = '2030-06-14T16:00:00Z' WHERE spiel_id = ?",
        (welt["spiele"][ZUKUNFT],),
    )
    conn.commit()
    eintraege = {a["schluessel"]: a for a in abzeichen.fuer_nutzer(conn, welt["ids"]["Mia"])}
    assert eintraege["frueher_vogel"]["wert"] == 1


def test_abzeichen_serien(conn, welt):
    """Serien zählen aufeinanderfolgende gewertete Tipps in Spielreihenfolge."""
    eintraege = {a["schluessel"]: a for a in abzeichen.fuer_nutzer(conn, welt["ids"]["Mia"])}
    # Mia: 4 P, dann 0 P → längste Treffer-Serie 1, längste Exakt-Serie 1
    assert eintraege["serien_koenig"]["wert"] == 1
    assert eintraege["perfekte_serie"]["wert"] == 1
    # Früher Vogel zählt die frühe Abgabe selbst (auch vor ungespielten
    # Spielen): nur der Zukunfts-Tipp liegt > 24 h vor dem Anstoß.
    assert eintraege["frueher_vogel"]["wert"] == 1
    assert eintraege["bonus_orakel"]["wert"] == 0