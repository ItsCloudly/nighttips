from __future__ import annotations

import pytest

from app.services import importer, sync
from app.services.fussball_api import mappe_match, runde_aus_match
from app.zeit import jetzt_iso

SPIELPLAN_JSON = {
    "teams": [
        {"fifa_code": "GER", "name": "Deutschland", "gruppe": "E"},
        {"fifa_code": "SCO", "name": "Schottland", "gruppe": "E"},
    ],
    "spielorte": [
        {
            "stadion_name": "MetLife Stadium",
            "stadt": "East Rutherford",
            "land": "USA",
            "kapazitaet": 82500,
            "zeitzone": "America/New_York",
        }
    ],
    "spiele": [
        {
            "runde": "Gruppe E",
            "anstoss_utc": "2030-06-15T18:00:00Z",
            "stadion": "MetLife Stadium",
            "heim": "GER",
            "gast": "SCO",
        }
    ],
}


def test_json_import_und_idempotenz(conn):
    ergebnis = importer.spielplan_importieren(conn, SPIELPLAN_JSON, akteur="test")
    assert ergebnis.teams == 2
    assert ergebnis.spielorte == 1
    assert ergebnis.spiele_neu == 1

    nochmal = importer.spielplan_importieren(conn, SPIELPLAN_JSON, akteur="test")
    assert nochmal.spiele_neu == 0
    assert nochmal.spiele_aktualisiert == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM spiel").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM team").fetchone()["n"] == 2


def test_json_import_unbekanntes_team(conn):
    daten = {"spiele": [{"runde": "Gruppe A", "anstoss_utc": "2030-06-15T18:00:00Z", "heim": "XXX", "gast": "YYY"}]}
    with pytest.raises(ValueError, match="Unbekanntes Team"):
        importer.spielplan_importieren(conn, daten, akteur="test")


def _api_match(
    match_id: int = 1001,
    status: str = "TIMED",
    tore: tuple[int, int] | None = None,
    dauer: str = "REGULAR",
    utc_date: str = "2030-06-15T18:00:00Z",
) -> dict:
    score: dict = {"winner": None, "duration": dauer, "fullTime": {"home": None, "away": None}}
    if tore is not None:
        score["fullTime"] = {"home": tore[0], "away": tore[1]}
        if tore[0] > tore[1]:
            score["winner"] = "HOME_TEAM"
        elif tore[0] < tore[1]:
            score["winner"] = "AWAY_TEAM"
        else:
            score["winner"] = "DRAW"
    return {
        "id": match_id,
        "utcDate": utc_date,
        "status": status,
        "stage": "GROUP_STAGE",
        "group": "Group E",
        "homeTeam": {"id": 1, "name": "Deutschland", "tla": "GER", "crest": "https://x/ger.png"},
        "awayTeam": {"id": 2, "name": "Schottland", "tla": "SCO", "crest": "https://x/sco.png"},
        "score": score,
        "venue": "MetLife Stadium",
    }


class ApiAttrappe:
    def __init__(self, teams: list[dict], matches: list[dict]):
        self._teams = teams
        self._matches = matches

    def teams(self) -> list[dict]:
        return self._teams

    def matches(self) -> list[dict]:
        return self._matches

    def standings(self) -> list[dict]:
        return []

    def scorers(self, limit: int = 30) -> list[dict]:
        return []


API_TEAMS = [
    {"id": 1, "name": "Deutschland", "tla": "GER", "crest": "https://x/ger.png"},
    {"id": 2, "name": "Schottland", "tla": "SCO", "crest": "https://x/sco.png"},
]


def test_mappe_match_und_runde():
    daten = mappe_match(_api_match(status="FINISHED", tore=(2, 1)))
    assert daten["runde"] == "Gruppe E"
    assert daten["status"] == "beendet"
    assert daten["tore_heim"] == 2
    assert daten["ergebnis_nach"] == "90"
    assert runde_aus_match({"stage": "FINAL"}) == "Finale"
    assert runde_aus_match({"stage": "LAST_16"}) == "Achtelfinale"


def test_stammdaten_sync_legt_alles_an(conn, einstellungen):
    api = ApiAttrappe(API_TEAMS, [_api_match()])
    bericht = sync.stammdaten_sync(conn, einstellungen, api=api)
    assert bericht.teams == 2
    assert bericht.spiele_neu == 1
    status = conn.execute(
        "SELECT * FROM sync_status WHERE job = ?", (sync.JOB_STAMMDATEN,)
    ).fetchone()
    assert status["status"] == "ok"

    # Idempotenz: zweiter Lauf ändert nichts
    bericht2 = sync.stammdaten_sync(conn, einstellungen, api=api)
    assert bericht2.spiele_neu == 0
    assert bericht2.spiele_aktualisiert == 0


def test_ergebnis_sync_wertet_tipps_aus(conn, einstellungen):
    sync.stammdaten_sync(conn, einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match()]))
    spiel = conn.execute("SELECT id FROM spiel").fetchone()
    nutzer_id = conn.execute(
        "INSERT INTO nutzer (anzeigename, pin_hash, rolle, erstellt_utc) VALUES ('T', 'x', 'mitglied', ?)",
        (jetzt_iso(),),
    ).lastrowid
    conn.execute(
        "INSERT INTO tipp (nutzer_id, spiel_id, tipp_heim, tipp_gast, abgegeben_utc) VALUES (?, ?, 2, 1, ?)",
        (nutzer_id, spiel["id"], jetzt_iso()),
    )
    conn.commit()

    bericht = sync.ergebnis_sync(
        conn, einstellungen, api=ApiAttrappe(API_TEAMS, [_api_match(status="FINISHED", tore=(2, 1))])
    )
    assert bericht.spiele_aktualisiert == 1
    assert bericht.spiele_ausgewertet == 1

    tipp = conn.execute("SELECT punkte FROM tipp WHERE nutzer_id = ?", (nutzer_id,)).fetchone()
    assert tipp["punkte"] == 4

    log = conn.execute(
        "SELECT * FROM change_log WHERE entitaet = 'spiel' AND feld = 'status' AND quelle = 'api'"
    ).fetchone()
    assert log is not None
    assert log["neu_wert"] == "beendet"


def test_sync_fehler_landet_im_status(conn, einstellungen):
    class KaputteApi:
        def teams(self):
            from app.services.fussball_api import FussballApiFehler

            raise FussballApiFehler("Testfehler")

        def matches(self):
            return []

    from app.services.fussball_api import FussballApiFehler

    with pytest.raises(FussballApiFehler):
        sync.stammdaten_sync(conn, einstellungen, api=KaputteApi())
    status = conn.execute(
        "SELECT * FROM sync_status WHERE job = ?", (sync.JOB_STAMMDATEN,)
    ).fetchone()
    assert status["status"] == "fehler"
    assert "Testfehler" in status["detail"]
