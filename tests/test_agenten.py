"""Tests für die Agenten-Schnittstelle (SPEC 7.2/8.3):
Token-Scopes, Pseudonymisierung, KI-Tipp, Analysen, Beiträge-Sichtung,
Ausgeschieden-Ableitung und News-Tags."""
from __future__ import annotations

from app.services import agenten, nutzer as nutzer_service, sync, turnier
from tests.test_grunddaten import API_TEAMS, ApiAttrappe, _api_match


def _setup(client, conn, einstellungen, *, mit_ki: bool = True):
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match()]))
    nutzer_service.nutzer_anlegen(conn, anzeigename="Chef", pin="1234", rolle="admin", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="t")
    if mit_ki:
        nutzer_service.nutzer_anlegen(conn, anzeigename="Claude", pin="9876", rolle="ki", akteur="t")
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    antwort = client.post(
        "/api/admin/agent-tokens", json={"name": "skill", "scopes": ["read", "write_analysis"]}
    )
    assert antwort.status_code == 201
    token = antwort.json()["token"]
    client.post("/api/logout")
    return {"Authorization": f"Bearer {token}"}


def test_token_scopes_und_widerruf(client, conn, einstellungen):
    kopf = _setup(client, conn, einstellungen)
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    nur_lesen = client.post(
        "/api/admin/agent-tokens", json={"name": "leser", "scopes": ["read"]}
    ).json()
    assert (
        client.post("/api/admin/agent-tokens", json={"name": "leser", "scopes": ["read"]}).status_code
        == 409
    )
    assert (
        client.post("/api/admin/agent-tokens", json={"name": "x", "scopes": ["root"]}).status_code
        == 409
    )
    client.post("/api/logout")

    lese_kopf = {"Authorization": f"Bearer {nur_lesen['token']}"}
    assert client.get("/api/export/spiele", headers=lese_kopf).status_code == 200
    # read darf nicht schreiben (SPEC 9)
    antwort = client.post(
        "/api/analysen",
        headers=lese_kopf,
        json={"spiel_id": 1, "typ": "prognose", "inhalt_markdown": "x"},
    )
    assert antwort.status_code == 403
    # Ohne/mit kaputtem Token
    assert client.get("/api/export/spiele").status_code == 401
    assert (
        client.get("/api/export/spiele", headers={"Authorization": "Bearer falsch"}).status_code
        == 403
    )
    # Widerruf
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    tokens = client.get("/api/admin/agent-tokens").json()
    leser_id = next(t["id"] for t in tokens if t["name"] == "leser")
    assert client.delete(f"/api/admin/agent-tokens/{leser_id}").status_code == 204
    client.post("/api/logout")
    assert client.get("/api/export/spiele", headers=lese_kopf).status_code == 403


def test_export_tipps_pseudonymisiert(client, conn, einstellungen):
    kopf = _setup(client, conn, einstellungen)
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    from app.services import tippspiel

    mia = conn.execute("SELECT id FROM nutzer WHERE anzeigename = 'Mia'").fetchone()["id"]
    tippspiel.tipp_abgeben(conn, nutzer_id=mia, spiel_id=spiel_id, tipp_heim=2, tipp_gast=1)

    tipps = client.get("/api/export/tipps", headers=kopf).json()
    assert len(tipps) == 1
    assert tipps[0]["tipper"].startswith("Spieler ")
    daten = str(tipps)
    assert "Mia" not in daten and "Chef" not in daten  # SPEC 8.3


def test_ki_tipp_und_analyse_fluss(client, conn, einstellungen):
    kopf = _setup(client, conn, einstellungen)
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]

    # KI-Tipp setzen
    antwort = client.post(
        "/api/tipps/ki",
        headers=kopf,
        json={"spiel_id": spiel_id, "tipp_heim": 2, "tipp_gast": 1},
    )
    assert antwort.status_code == 200
    ki_id = conn.execute("SELECT id FROM nutzer WHERE rolle = 'ki'").fetchone()["id"]
    tipp = conn.execute("SELECT * FROM tipp WHERE nutzer_id = ?", (ki_id,)).fetchone()
    assert (tipp["tipp_heim"], tipp["tipp_gast"]) == (2, 1)

    # Prognose anlegen, zweite Version überschreibt nicht, sondern versioniert
    for _ in range(2):
        antwort = client.post(
            "/api/analysen",
            headers=kopf,
            json={
                "spiel_id": spiel_id,
                "typ": "prognose",
                "inhalt_markdown": "## Prognose\nHeimsieg wahrscheinlich.",
                "struktur_json": {"wahrscheinlichkeiten": {"heim": 0.5, "remis": 0.3, "gast": 0.2}},
            },
        )
        assert antwort.status_code == 201
    assert antwort.json()["version"] == 2

    # Mia (Mitglied, nicht freigeschaltet) sieht die KI-Wertung NICHT
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    detail = client.get(f"/api/spiele/{spiel_id}").json()
    assert detail["analysen"] == {}
    client.post("/api/logout")

    # Nach Freischaltung durch den Admin zeigt die Lupe die neueste Version
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    mia_id = conn.execute("SELECT id FROM nutzer WHERE anzeigename = 'Mia'").fetchone()["id"]
    assert (
        client.patch(f"/api/admin/nutzer/{mia_id}", json={"ki_freigeschaltet": True}).status_code
        == 200
    )
    client.post("/api/logout")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    detail = client.get(f"/api/spiele/{spiel_id}").json()
    assert detail["analysen"]["prognose"]["version"] == 2
    assert detail["analysen"]["nachanalyse"] is None


def test_ki_tipp_ohne_ki_nutzer(client, conn, einstellungen):
    kopf = _setup(client, conn, einstellungen, mit_ki=False)
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    antwort = client.post(
        "/api/tipps/ki", headers=kopf, json={"spiel_id": spiel_id, "tipp_heim": 1, "tipp_gast": 0}
    )
    assert antwort.status_code == 409


def test_beitrag_sichtung_uebernimmt_taktik(client, conn, einstellungen):
    kopf = _setup(client, conn, einstellungen)
    team_id = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    antwort = client.post(
        "/api/beitraege",
        headers=kopf,
        json={
            "typ": "taktik",
            "team_id": team_id,
            "inhalt": {"formation": "4-2-3-1", "staerken": "Ballbesitz", "schwaechen": "Konter"},
        },
    )
    assert antwort.status_code == 201

    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    beitraege = client.get("/api/admin/beitraege").json()
    assert len(beitraege) == 1
    entschieden = client.post(f"/api/admin/beitraege/{beitraege[0]['id']}/uebernehmen").json()
    assert entschieden["status"] == "uebernommen"

    team = client.get(f"/api/teams/{team_id}").json()
    assert team["taktik"]["formation"] == "4-2-3-1"
    assert team["taktik"]["quelle"] == "agent"
    assert client.get("/api/admin/beitraege").json() == []


def test_admin_taktik_und_verletzung(client, conn, einstellungen):
    _setup(client, conn, einstellungen)
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    team_id = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    spieler_id = conn.execute(
        "SELECT id FROM spieler WHERE team_id = ? LIMIT 1", (team_id,)
    ).fetchone()["id"]

    assert (
        client.put(
            f"/api/admin/teams/{team_id}/taktik",
            json={"formation": "4-3-3", "beschreibung": "Hohes Pressing"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/admin/verletzungen",
            json={"spieler_id": spieler_id, "beschreibung": "Muskelfaserriss", "status": "faellt aus"},
        ).status_code
        == 201
    )
    team = client.get(f"/api/teams/{team_id}").json()
    assert team["taktik"]["formation"] == "4-3-3"
    assert team["verletzungen"][0]["status"] == "faellt aus"


def test_ausgeschiedene_teams(conn, einstellungen):
    ko = {
        **_api_match(9100),
        "stage": "LAST_16",
        "group": None,
        "status": "FINISHED",
        "score": {"winner": "HOME_TEAM", "duration": "REGULAR", "fullTime": {"home": 2, "away": 0}},
    }
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [ko]))
    raus = turnier.ausgeschiedene_teams(conn)
    verlierer = conn.execute("SELECT id FROM team WHERE fifa_code = 'SCO'").fetchone()["id"]
    sieger = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    assert verlierer in raus
    assert sieger not in raus


def test_news_tags():
    assert "Verletzung" in turnier.news_tags("Musiala fällt mit Muskelfaserriss aus")
    assert "Wechsel" in turnier.news_tags("Transfer perfekt: Stürmer verlässt den Verein")
    assert "Aufstellung" in turnier.news_tags("Die Startelf gegen Mexiko")
    assert turnier.news_tags("Stadionführer Toronto") == []
