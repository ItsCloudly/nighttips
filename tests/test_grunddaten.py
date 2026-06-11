from __future__ import annotations

from app.services import sync
from app.services.fussball_api import (
    mappe_spieler,
    mappe_trainer,
    mappe_vergleich,
    runde_aus_match,
)

API_TEAMS = [
    {
        "id": 1,
        "name": "Germany",
        "tla": "GER",
        "crest": "https://x/ger.png",
        "coach": {"id": 9, "name": "Julian Nagelsmann", "nationality": "Germany"},
        "squad": [
            {"id": 100, "name": "Marc-André ter Stegen", "position": "Goalkeeper", "dateOfBirth": "1992-04-30"},
            {"id": 101, "name": "Joshua Kimmich", "position": "Right-Back", "dateOfBirth": "1995-02-08"},
            {"id": 102, "name": "Florian Wirtz", "position": "Attacking Midfield", "dateOfBirth": "2003-05-03"},
        ],
    },
    {
        "id": 2,
        "name": "Scotland",
        "tla": "SCO",
        "crest": "https://x/sco.png",
        "coach": {"id": None, "name": None},
        "squad": [],
    },
]


def _api_match(match_id: int = 9001) -> dict:
    return {
        "id": match_id,
        "utcDate": "2030-06-15T18:00:00Z",
        "status": "TIMED",
        "stage": "GROUP_STAGE",
        "group": "GROUP_E",
        "homeTeam": {"id": 1, "name": "Germany", "tla": "GER"},
        "awayTeam": {"id": 2, "name": "Scotland", "tla": "SCO"},
        "score": {"winner": None, "duration": "REGULAR", "fullTime": {"home": None, "away": None}},
        "venue": "BMO Field",
    }


class ApiAttrappe:
    def __init__(self, teams, matches, duelle=None, aggregate=None, tabellen=None, torjaeger=None):
        self._teams = teams
        self._matches = matches
        self._duelle = duelle or []
        self._aggregate = aggregate or {}
        self._tabellen = tabellen or []
        self._torjaeger = torjaeger or []
        self.h2h_abrufe = []

    def teams(self):
        return self._teams

    def matches(self):
        return self._matches

    def head2head(self, match_api_ref, limit=5):
        self.h2h_abrufe.append(match_api_ref)
        # Antwortform der echten API: aggregates + matches (im Free Tier ist matches leer)
        return {"aggregates": self._aggregate, "matches": self._duelle}

    def standings(self):
        return self._tabellen

    def scorers(self, limit=30):
        return self._torjaeger


def test_runde_aus_group_underscore_format():
    # Die echte API liefert "GROUP_E" (nicht "Group E" wie in der Doku)
    assert runde_aus_match({"stage": "GROUP_STAGE", "group": "GROUP_E"}) == "Gruppe E"
    assert runde_aus_match({"stage": "GROUP_STAGE", "group": "Group E"}) == "Gruppe E"


def test_mappe_team_deutsche_namen():
    from app.services.fussball_api import mappe_team

    assert mappe_team({"id": 1, "name": "Germany", "tla": "GER"})["name"] == "Deutschland"
    assert mappe_team({"id": 2, "name": "Atlantis", "tla": "ATL"})["name"] == "Atlantis"


def test_mappe_spieler_und_trainer():
    spieler = mappe_spieler({"id": 101, "name": "Joshua Kimmich", "position": "Right-Back"})
    assert spieler["position"] == "Abwehr"
    assert mappe_spieler({"id": 1, "name": "X", "position": "Goalkeeper"})["position"] == "Torwart"
    assert mappe_spieler({"name": None}) is None
    trainer = mappe_trainer(API_TEAMS[0])
    assert trainer["name"] == "Julian Nagelsmann"
    assert mappe_trainer(API_TEAMS[1]) is None


def test_stammdaten_sync_legt_kader_und_trainer_an(conn, einstellungen):
    api = ApiAttrappe(API_TEAMS, [_api_match()])
    bericht = sync.stammdaten_sync(conn, einstellungen, api=api)
    assert bericht.spieler == 3
    kader = conn.execute(
        "SELECT s.name, s.position FROM spieler s JOIN team t ON t.id = s.team_id"
        " WHERE t.fifa_code = 'GER' ORDER BY s.name"
    ).fetchall()
    assert len(kader) == 3
    trainer = conn.execute(
        "SELECT tr.name FROM trainer tr JOIN team t ON t.id = tr.team_id WHERE t.fifa_code = 'GER'"
    ).fetchone()
    assert trainer["name"] == "Julian Nagelsmann"
    # Gruppe wird aus den Gruppenspielen abgeleitet
    gruppe = conn.execute("SELECT gruppe FROM team WHERE fifa_code = 'GER'").fetchone()
    assert gruppe["gruppe"] == "E"

    # Kader-Abgleich: Spieler verschwindet aus dem Squad -> wird entfernt
    api2 = ApiAttrappe(
        [{**API_TEAMS[0], "squad": API_TEAMS[0]["squad"][:2]}, API_TEAMS[1]], [_api_match()]
    )
    sync.stammdaten_sync(conn, einstellungen, api=api2)
    rest = conn.execute("SELECT COUNT(*) AS n FROM spieler").fetchone()["n"]
    assert rest == 2


DUELLE = [
    {
        "id": 7777,
        "utcDate": "2024-06-14T19:00:00Z",
        "competition": {"name": "European Championship"},
        "homeTeam": {"id": 1, "name": "Germany"},
        "awayTeam": {"id": 2, "name": "Scotland"},
        "score": {"duration": "REGULAR", "fullTime": {"home": 5, "away": 1}},
    }
]


AGGREGATE = {
    "numberOfMatches": 4,
    "totalGoals": 11,
    "homeTeam": {"id": 1, "wins": 2, "draws": 1, "losses": 1},
    "awayTeam": {"id": 2, "wins": 1, "draws": 1, "losses": 2},
}


def test_vergleiche_sync(conn, einstellungen):
    api = ApiAttrappe(API_TEAMS, [_api_match()], duelle=DUELLE, aggregate=AGGREGATE)
    sync.stammdaten_sync(conn, einstellungen, api=api)
    bericht = sync.vergleiche_sync(conn, einstellungen, api=api)
    assert bericht.vergleiche == 1
    duell = conn.execute("SELECT * FROM direktvergleich").fetchone()
    assert duell["heim_name"] == "Deutschland"
    assert duell["tore_heim"] == 5
    bilanz = conn.execute("SELECT * FROM duell_bilanz").fetchone()
    assert bilanz["anzahl"] == 4
    assert bilanz["heim_siege"] == 2
    assert bilanz["remis"] == 1
    assert bilanz["tore"] == 11
    # Zweiter Lauf überspringt bereits geladene Spiele
    bericht2 = sync.vergleiche_sync(conn, einstellungen, api=api)
    assert bericht2.vergleiche == 0
    assert api.h2h_abrufe == ["9001"]


TABELLEN = [
    {
        "stage": "ALL",
        "type": "TOTAL",
        "group": "Group E",
        "table": [
            {
                "position": 1,
                "team": {"id": 1, "name": "Germany", "tla": "GER"},
                "playedGames": 1, "won": 1, "draw": 0, "lost": 0,
                "points": 3, "goalsFor": 5, "goalsAgainst": 1, "goalDifference": 4,
            },
            {
                "position": 2,
                "team": {"id": 2, "name": "Scotland", "tla": "SCO"},
                "playedGames": 1, "won": 0, "draw": 0, "lost": 1,
                "points": 0, "goalsFor": 1, "goalsAgainst": 5, "goalDifference": -4,
            },
        ],
    }
]

TORJAEGER = [
    {
        "player": {"id": 102, "name": "Florian Wirtz"},
        "team": {"id": 1, "name": "Germany"},
        "playedMatches": 1,
        "goals": 2,
        "assists": 1,
        "penalties": 0,
    }
]


def test_stammdaten_sync_tabelle_und_torschuetzen(client, conn, einstellungen):
    from app.services import nutzer as nutzer_service

    api = ApiAttrappe(API_TEAMS, [_api_match()], tabellen=TABELLEN, torjaeger=TORJAEGER)
    bericht = sync.stammdaten_sync(conn, einstellungen, api=api)
    assert bericht.tabellenzeilen == 2
    assert bericht.torschuetzen == 1

    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="test")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})

    tabellen = client.get("/api/tabellen").json()
    assert list(tabellen.keys()) == ["E"]
    assert tabellen["E"][0]["name"] == "Deutschland"
    assert tabellen["E"][0]["punkte"] == 3
    assert tabellen["E"][1]["tordifferenz"] == -4

    torschuetzen = client.get("/api/torschuetzen").json()
    assert torschuetzen[0]["name"] == "Florian Wirtz"
    assert torschuetzen[0]["tore"] == 2
    assert torschuetzen[0]["team_name"] == "Deutschland"

    # Spiel-Detail eines Gruppenspiels enthält die Mini-Tabelle der Gruppe
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    detail = client.get(f"/api/spiele/{spiel_id}").json()
    assert detail["tabelle"][0]["platz"] == 1
    assert detail["tabelle"][0]["punkte"] == 3


def test_ergebnis_sync_zieht_tabelle_nur_bei_neuem_endstand(conn, einstellungen):
    api = ApiAttrappe(API_TEAMS, [_api_match()], tabellen=TABELLEN, torjaeger=TORJAEGER)
    sync.stammdaten_sync(conn, einstellungen, api=api)

    # Kein neu beendetes Spiel -> Tabelle wird nicht angefasst
    bericht = sync.ergebnis_sync(conn, einstellungen, api=api)
    assert bericht.tabellenzeilen == 0

    beendet = {
        **_api_match(),
        "status": "FINISHED",
        "score": {"winner": "HOME_TEAM", "duration": "REGULAR", "fullTime": {"home": 5, "away": 1}},
    }
    api2 = ApiAttrappe(API_TEAMS, [beendet], tabellen=TABELLEN, torjaeger=TORJAEGER)
    bericht2 = sync.ergebnis_sync(conn, einstellungen, api=api2)
    assert bericht2.tabellenzeilen == 2
    assert bericht2.torschuetzen == 1


def test_vergleiche_sync_wartet_auf_ko_paarungen(conn, einstellungen):
    ko_match = {
        **_api_match(9002),
        "stage": "FINAL",
        "group": None,
        "homeTeam": {"id": None, "name": None},
        "awayTeam": {"id": None, "name": None},
    }
    api = ApiAttrappe(API_TEAMS, [ko_match], duelle=DUELLE)
    sync.stammdaten_sync(conn, einstellungen, api=api)
    bericht = sync.vergleiche_sync(conn, einstellungen, api=api)
    assert bericht.vergleiche == 0  # Teams stehen noch nicht fest


def test_spiel_detail_mit_trainer_und_duellen(client, conn, einstellungen):
    from app.services import nutzer as nutzer_service

    api = ApiAttrappe(API_TEAMS, [_api_match()], duelle=DUELLE, aggregate=AGGREGATE)
    sync.stammdaten_sync(conn, einstellungen, api=api)
    sync.vergleiche_sync(conn, einstellungen, api=api)
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="test")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})

    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    detail = client.get(f"/api/spiele/{spiel_id}").json()
    assert detail["trainer"]["heim"] == "Julian Nagelsmann"
    assert detail["vergleiche"][0]["tore_heim"] == 5
    assert detail["bilanz"]["anzahl"] == 4
    assert detail["bilanz"]["heim_siege"] == 2

    team_id = conn.execute("SELECT id FROM team WHERE fifa_code = 'GER'").fetchone()["id"]
    team = client.get(f"/api/teams/{team_id}").json()
    assert team["trainer"]["name"] == "Julian Nagelsmann"
    assert len(team["kader"]) == 3
    assert team["kader"][0]["position"] == "Torwart"  # Sortierung: Torwart zuerst
