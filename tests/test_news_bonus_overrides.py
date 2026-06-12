"""Tests für RSS-News (SPEC 4.3/5.6), Bonusfragen (SPEC 5.4) und
Admin-Overrides (SPEC 3.5): Override-Mergelogik admin > api."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app import db as db_modul
from app.services import bonus, news, nutzer as nutzer_service, sync
from app.zeit import iso_utc, jetzt_utc
from tests.test_grunddaten import API_TEAMS, ApiAttrappe, _api_match

RSS_BEISPIEL = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Kicker WM</title>
  <item>
    <title>Deutschland mit Startelf-&#220;berraschung</title>
    <link>https://news.example/de-startelf</link>
    <description><![CDATA[<p>Wirtz beginnt, &amp; Kimmich auf der Sechs.</p>]]></description>
    <pubDate>Wed, 10 Jun 2026 18:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Stadionführer Toronto</title>
    <link>https://news.example/toronto</link>
    <description>Alles zum BMO Field.</description>
    <pubDate>Wed, 10 Jun 2026 17:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM_BEISPIEL = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Sportschau</title>
  <entry>
    <title>Schottland-Fans feiern</title>
    <link rel="alternate" href="https://news.example/sco-fans"/>
    <summary>Tartan Army in Kanada.</summary>
    <published>2026-06-10T16:00:00Z</published>
  </entry>
</feed>"""


def _feed_anlegen(conn, url: str = "https://news.example/rss") -> int:
    with db_modul.schreib_transaktion(conn):
        cursor = conn.execute(
            "INSERT INTO feed (url, titel, aktiv) VALUES (?, 'Test', 1)", (url,)
        )
        return cursor.lastrowid


def test_rss_parsen_dedupe_und_team_zuordnung(conn, einstellungen):
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match()]))
    feed_id = _feed_anlegen(conn)
    feed = conn.execute("SELECT * FROM feed WHERE id = ?", (feed_id,)).fetchone()

    neu = news.feed_abrufen(conn, feed, xml_text=RSS_BEISPIEL)
    assert neu == 2
    # Zweiter Abruf: Link-Hash dedupliziert
    assert news.feed_abrufen(conn, feed, xml_text=RSS_BEISPIEL) == 0

    items = conn.execute("SELECT * FROM news_item ORDER BY id").fetchall()
    assert items[0]["titel"] == "Deutschland mit Startelf-Überraschung"
    assert "<p>" not in items[0]["zusammenfassung"]  # HTML entfernt
    assert items[0]["veroeffentlicht_utc"] == "2026-06-10T18:00:00Z"
    team = conn.execute(
        "SELECT name FROM team WHERE id = ?", (items[0]["team_id"],)
    ).fetchone()
    assert team["name"] == "Deutschland"
    assert items[1]["team_id"] is None  # Stadionführer matcht kein Team

    aktualisiert = conn.execute("SELECT letzter_abruf_utc FROM feed").fetchone()
    assert aktualisiert["letzter_abruf_utc"] is not None


def test_atom_parsen(conn, einstellungen):
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match()]))
    feed_id = _feed_anlegen(conn, "https://news.example/atom")
    feed = conn.execute("SELECT * FROM feed WHERE id = ?", (feed_id,)).fetchone()
    assert news.feed_abrufen(conn, feed, xml_text=ATOM_BEISPIEL) == 1
    item = conn.execute("SELECT * FROM news_item").fetchone()
    assert item["titel"] == "Schottland-Fans feiern"
    team = conn.execute("SELECT name FROM team WHERE id = ?", (item["team_id"],)).fetchone()
    assert team["name"] == "Schottland"


def test_news_endpunkt_mit_teamfilter(client, conn, einstellungen):
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match()]))
    feed_id = _feed_anlegen(conn)
    feed = conn.execute("SELECT * FROM feed WHERE id = ?", (feed_id,)).fetchone()
    news.feed_abrufen(conn, feed, xml_text=RSS_BEISPIEL)

    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="test")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    alle = client.get("/api/news").json()
    assert len(alle) == 2
    assert alle[0]["titel"].startswith("Deutschland")  # neueste zuerst

    team_id = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    gefiltert = client.get(f"/api/news?team_id={team_id}").json()
    assert len(gefiltert) == 1


def test_bonusfrage_ablauf(client, conn, einstellungen):
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match()]))
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="1234", rolle="admin", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="t")
    team_ger = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    team_sco = conn.execute("SELECT id FROM team WHERE fifa_code = 'SCO'").fetchone()["id"]

    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    schluss = iso_utc(jetzt_utc() + timedelta(hours=2))
    frage = client.post(
        "/api/admin/bonusfragen",
        json={"frage": "Wer wird Weltmeister?", "typ": "team", "punkte_wert": 10,
              "einsendeschluss_utc": schluss},
    ).json()

    # Mia tippt Deutschland, Chef tippt Schottland
    client.post("/api/logout")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    antwort = client.post(
        "/api/bonustipps", json={"bonusfrage_id": frage["id"], "antwort_ref": team_ger}
    )
    assert antwort.status_code == 200
    # Ungültige Antwort-Referenz wird abgelehnt
    assert (
        client.post(
            "/api/bonustipps", json={"bonusfrage_id": frage["id"], "antwort_ref": 99999}
        ).status_code
        == 409
    )
    # Vor dem Einsendeschluss: eigene Antwort sichtbar, fremde nicht
    fragen = client.get("/api/bonusfragen").json()
    assert fragen[0]["offen"] is True
    assert fragen[0]["mein_tipp"]["antwort_name"] == "Deutschland"
    assert fragen[0]["tipps"] == []

    client.post("/api/logout")
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    client.post("/api/bonustipps", json={"bonusfrage_id": frage["id"], "antwort_ref": team_sco})

    # Auflösen: Deutschland wird Weltmeister
    aufgeloest = client.post(
        f"/api/admin/bonusfragen/{frage['id']}/aufloesen", json={"aufloesung_ref": team_ger}
    ).json()
    assert aufgeloest["tipps_gewertet"] == 2

    punkte = {
        zeile["nutzer_id"]: zeile["punkte"]
        for zeile in conn.execute("SELECT nutzer_id, punkte FROM bonustipp").fetchall()
    }
    assert sorted(punkte.values()) == [0, 10]

    # Nach Auflösung: Tipps aller sichtbar, weitere Abgabe gesperrt
    fragen = client.get("/api/bonusfragen").json()
    assert fragen[0]["offen"] is False
    assert fragen[0]["aufloesung_name"] == "Deutschland"
    assert len(fragen[0]["tipps"]) == 2
    assert (
        client.post(
            "/api/bonustipps", json={"bonusfrage_id": frage["id"], "antwort_ref": team_ger}
        ).status_code
        == 409
    )

    # Bonuspunkte zählen in der Gesamt-Rangliste, nicht in der Tageswertung
    rangliste = client.get("/api/rangliste").json()
    mia = next(zeile for zeile in rangliste if zeile["anzeigename"] == "Mia")
    assert mia["punkte"] == 10
    assert mia["bonus_punkte"] == 10
    heute = client.get(f"/api/rangliste?datum={iso_utc(jetzt_utc())[:10]}").json()
    mia_heute = next(zeile for zeile in heute if zeile["anzeigename"] == "Mia")
    assert mia_heute["punkte"] == 0


def test_bonusfrage_mehrfach_aufloesung(client, conn, einstellungen):
    """v0.2: Fragen wie „Wer erreicht das Halbfinale?" haben mehrere richtige
    Antworten — jeder Tipp auf eine davon punktet voll. Erneutes Auflösen
    ersetzt die Wertung (Korrektur)."""
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match()]))
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="1234", rolle="admin", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="t")
    team_ger = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    team_sco = conn.execute("SELECT id FROM team WHERE fifa_code = 'SCO'").fetchone()["id"]

    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    schluss = iso_utc(jetzt_utc() + timedelta(hours=2))
    frage = client.post(
        "/api/admin/bonusfragen",
        json={"frage": "Wer erreicht das Finale?", "typ": "team", "punkte_wert": 5,
              "einsendeschluss_utc": schluss},
    ).json()
    client.post("/api/bonustipps", json={"bonusfrage_id": frage["id"], "antwort_ref": team_sco})
    client.post("/api/logout")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    client.post("/api/bonustipps", json={"bonusfrage_id": frage["id"], "antwort_ref": team_ger})
    client.post("/api/logout")
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})

    # Erst (irrtümlich) nur Deutschland auflösen: Chef geht leer aus
    client.post(
        f"/api/admin/bonusfragen/{frage['id']}/aufloesen",
        json={"aufloesung_refs": [team_ger]},
    )
    punkte = {
        zeile["nutzer_id"]: zeile["punkte"]
        for zeile in conn.execute("SELECT nutzer_id, punkte FROM bonustipp").fetchall()
    }
    assert sorted(punkte.values()) == [0, 5]

    # Korrektur: BEIDE Teams stehen im Finale → beide Tipps punkten voll
    aufgeloest = client.post(
        f"/api/admin/bonusfragen/{frage['id']}/aufloesen",
        json={"aufloesung_refs": [team_ger, team_sco]},
    ).json()
    assert aufgeloest["tipps_gewertet"] == 2
    punkte = [
        zeile["punkte"]
        for zeile in conn.execute("SELECT punkte FROM bonustipp").fetchall()
    ]
    assert punkte == [5, 5]

    fragen = client.get("/api/bonusfragen").json()
    eintrag = next(f for f in fragen if f["id"] == frage["id"])
    assert eintrag["offen"] is False
    assert sorted(eintrag["aufloesung_namen"]) == ["Deutschland", "Schottland"]
    assert "Deutschland" in eintrag["aufloesung_name"]
    assert "Schottland" in eintrag["aufloesung_name"]

    # Leere Auflösung wird abgelehnt
    assert (
        client.post(
            f"/api/admin/bonusfragen/{frage['id']}/aufloesen", json={}
        ).status_code
        == 422
    )


def test_override_ueberdauert_api_sync(client, conn, einstellungen):
    """Kern der Mergelogik (SPEC 3.5): admin > api, bis der Override aufgehoben wird."""
    beendet = _api_match()
    beendet["status"] = "FINISHED"
    beendet["score"] = {
        "winner": "HOME_TEAM", "duration": "REGULAR", "fullTime": {"home": 2, "away": 1},
    }
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [beendet]))
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]

    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="1234", rolle="admin", akteur="t")
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})

    # Admin korrigiert das Ergebnis (die API meldet falsch 2:1, richtig ist 3:1)
    antwort = client.post(
        f"/api/admin/spiele/{spiel_id}/ergebnis",
        json={"tore_heim": 3, "tore_gast": 1, "status": "beendet", "ergebnis_nach": "90"},
    )
    assert antwort.status_code == 200

    # Nächster API-Sync liefert weiter 2:1 — der Override gewinnt
    sync.ergebnis_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [beendet]))
    spiel = conn.execute("SELECT tore_heim, tore_gast FROM spiel").fetchone()
    assert (spiel["tore_heim"], spiel["tore_gast"]) == (3, 1)

    # Override-Liste zeigt den Eintrag; Aufheben gibt das Feld wieder frei
    liste = client.get("/api/admin/overrides").json()
    assert any(o["feld"] == "tore_heim" and o["wert"] == "3" for o in liste)
    for eintrag in liste:
        assert client.delete(f"/api/admin/overrides/{eintrag['id']}").status_code == 204

    sync.ergebnis_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [beendet]))
    spiel = conn.execute("SELECT tore_heim, tore_gast FROM spiel").fetchone()
    assert (spiel["tore_heim"], spiel["tore_gast"]) == (2, 1)


def test_feed_verwaltung(client, conn, einstellungen):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="1234", rolle="admin", akteur="t")
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})

    feed = client.post(
        "/api/admin/feeds", json={"url": "https://news.example/rss", "titel": "Kicker"}
    )
    assert feed.status_code == 201
    feed_id = feed.json()["id"]
    assert (
        client.post(
            "/api/admin/feeds", json={"url": "https://news.example/rss"}
        ).status_code
        == 409
    )
    assert client.post(f"/api/admin/feeds/{feed_id}/umschalten").json()["aktiv"] == 0
    assert news.abruf_faellig(conn) is False  # deaktivierter Feed zählt nicht
    assert client.post(f"/api/admin/feeds/{feed_id}/umschalten").json()["aktiv"] == 1
    assert news.abruf_faellig(conn) is True
    assert client.delete(f"/api/admin/feeds/{feed_id}").status_code == 204
    assert client.get("/api/admin/feeds").json() == []


def test_bonusfrage_nach_schluss_gesperrt(conn, einstellungen):
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match()]))
    nutzer_id = nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="t")
    team_id = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    frage_id = bonus.frage_anlegen(
        conn, frage="Torschützenkönig?", typ="team", punkte_wert=5,
        einsendeschluss_utc=iso_utc(jetzt_utc() - timedelta(minutes=1)),
    )
    with pytest.raises(bonus.BonusFehler):
        bonus.tipp_abgeben(conn, nutzer_id=nutzer_id, bonusfrage_id=frage_id, antwort_ref=team_id)
