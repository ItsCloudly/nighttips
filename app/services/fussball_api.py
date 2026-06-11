"""Client und Feld-Mapping für football-data.org v4 (SPEC 12.1, Kandidat A).

Der Client ist bewusst dünn; das Mapping in eigene Funktionen ausgelagert,
damit Tests mit aufgezeichneten API-Antworten arbeiten können (SPEC 9).
Free Tier: 10 Anfragen pro Minute — der Client drosselt selbst und behandelt 429.
"""
from __future__ import annotations

import time
from typing import Any, Protocol

import httpx

from ..config import Einstellungen
from ..zeit import iso_utc, parse_utc
from .laender import deutscher_teamname

STATUS_MAP = {
    "SCHEDULED": "geplant",
    "TIMED": "geplant",
    "POSTPONED": "geplant",
    "IN_PLAY": "live",
    "PAUSED": "halbzeit",
    "SUSPENDED": "live",
    "FINISHED": "beendet",
    "AWARDED": "beendet",
    "CANCELLED": "abgesagt",
}

RUNDEN_MAP = {
    "LAST_32": "Sechzehntelfinale",
    "LAST_16": "Achtelfinale",
    "QUARTER_FINALS": "Viertelfinale",
    "SEMI_FINALS": "Halbfinale",
    "THIRD_PLACE": "Spiel um Platz 3",
    "FINAL": "Finale",
}

DAUER_MAP = {
    "REGULAR": "90",
    "EXTRA_TIME": "120",
    "PENALTY_SHOOTOUT": "elfmeterschiessen",
}

# football-data nutzt teils grobe ("Defence"), teils feine Positionsnamen ("Left-Back").
POSITION_MAP = {
    "goalkeeper": "Torwart",
    "defence": "Abwehr",
    "defender": "Abwehr",
    "centre-back": "Abwehr",
    "left-back": "Abwehr",
    "right-back": "Abwehr",
    "midfield": "Mittelfeld",
    "midfielder": "Mittelfeld",
    "defensive midfield": "Mittelfeld",
    "central midfield": "Mittelfeld",
    "attacking midfield": "Mittelfeld",
    "offence": "Sturm",
    "forward": "Sturm",
    "attacker": "Sturm",
    "centre-forward": "Sturm",
    "left winger": "Sturm",
    "right winger": "Sturm",
}

_MAX_VERSUCHE = 3


class FussballApiFehler(Exception):
    pass


class ApiQuelle(Protocol):
    """Schnittstelle für den Sync — Tests können eine Attrappe einsetzen."""

    def teams(self) -> list[dict]: ...

    def matches(self) -> list[dict]: ...

    def head2head(self, match_api_ref: str, limit: int = 5) -> dict: ...

    def standings(self) -> list[dict]: ...

    def scorers(self, limit: int = 30) -> list[dict]: ...


class FussballApi:
    def __init__(self, einstellungen: Einstellungen):
        if not einstellungen.api_token:
            raise FussballApiFehler("Kein API-Token konfiguriert (WM26_API_TOKEN)")
        self._basis_url = einstellungen.api_basis_url.rstrip("/")
        self._wettbewerb = einstellungen.api_wettbewerb
        self._headers = {"X-Auth-Token": einstellungen.api_token}
        self._letzter_abruf = 0.0
        # Mindestabstand aus dem Tarif-Limit ableiten (8 % Puffer gegen
        # Uhren-Drift; Free 10/Min. → 6,48 s, Livescores 20/Min. → 3,24 s).
        rate = max(einstellungen.api_rate_pro_minute, 1)
        self._min_abstand = 60.0 / rate * 1.08

    def _drosseln(self) -> None:
        wartezeit = self._letzter_abruf + self._min_abstand - time.monotonic()
        if wartezeit > 0:
            time.sleep(wartezeit)

    def _get(self, pfad: str) -> dict:
        for versuch in range(1, _MAX_VERSUCHE + 1):
            self._drosseln()
            try:
                antwort = httpx.get(
                    f"{self._basis_url}{pfad}", headers=self._headers, timeout=30.0
                )
                self._letzter_abruf = time.monotonic()
                if antwort.status_code == 429 and versuch < _MAX_VERSUCHE:
                    try:
                        pause = int(antwort.headers.get("Retry-After", "60") or "60")
                    except ValueError:
                        # Retry-After darf laut RFC auch ein HTTP-Datum sein
                        pause = 60
                    time.sleep(min(pause, 120))
                    continue
                antwort.raise_for_status()
                return antwort.json()
            except httpx.HTTPError as fehler:
                self._letzter_abruf = time.monotonic()
                if versuch >= _MAX_VERSUCHE:
                    raise FussballApiFehler(
                        f"API-Abruf {pfad} fehlgeschlagen: {fehler}"
                    ) from fehler
                time.sleep(5)
        raise FussballApiFehler(f"API-Abruf {pfad} fehlgeschlagen")

    def teams(self) -> list[dict]:
        return self._get(f"/competitions/{self._wettbewerb}/teams").get("teams", [])

    def matches(self) -> list[dict]:
        return self._get(f"/competitions/{self._wettbewerb}/matches").get("matches", [])

    def head2head(self, match_api_ref: str, limit: int = 5) -> dict:
        """Liefert die rohe head2head-Antwort (aggregates + matches).

        Im Free Tier (TIER_ONE) ist `matches` leer, nur `aggregates` ist gefüllt.
        """
        return self._get(f"/matches/{match_api_ref}/head2head?limit={limit}")

    def standings(self) -> list[dict]:
        """Offizielle Gruppentabellen — eine Antwort enthält alle zwölf Gruppen."""
        return self._get(f"/competitions/{self._wettbewerb}/standings").get("standings", [])

    def scorers(self, limit: int = 30) -> list[dict]:
        return self._get(f"/competitions/{self._wettbewerb}/scorers?limit={limit}").get(
            "scorers", []
        )


def runde_aus_match(match: dict) -> str:
    stage = match.get("stage") or ""
    if stage == "GROUP_STAGE":
        gruppe = (match.get("group") or "").replace("GROUP_", "").replace("Group ", "").strip()
        return f"Gruppe {gruppe}" if gruppe else "Gruppenphase"
    return RUNDEN_MAP.get(stage, stage or "Unbekannt")


def mappe_team(team: dict) -> dict[str, Any]:
    api_name = team.get("name") or team.get("shortName") or f"Team {team.get('id')}"
    return {
        "name": deutscher_teamname(api_name),
        "fifa_code": team.get("tla"),
        "flagge_url": team.get("crest"),
        "api_ref": str(team["id"]),
    }


def mappe_trainer(team: dict) -> dict[str, Any] | None:
    coach = team.get("coach") or {}
    if not coach.get("name"):
        return None
    return {
        "name": coach["name"],
        "nationalitaet": coach.get("nationality"),
        "api_ref": str(coach["id"]) if coach.get("id") is not None else None,
    }


def mappe_spieler(spieler: dict) -> dict[str, Any] | None:
    if not spieler.get("name"):
        return None
    position_roh = (spieler.get("position") or "").strip().lower()
    return {
        "name": spieler["name"],
        "position": POSITION_MAP.get(position_roh),
        "geburtsdatum": spieler.get("dateOfBirth"),
        "trikotnummer": spieler.get("shirtNumber"),
        "api_ref": str(spieler["id"]) if spieler.get("id") is not None else None,
    }


def mappe_match(match: dict) -> dict[str, Any]:
    """Übersetzt ein API-Match in unser Spiel-Schema (Team-Referenzen als api_ref)."""
    api_status = match.get("status") or "SCHEDULED"
    if api_status not in STATUS_MAP:
        raise FussballApiFehler(f"Unbekannter Spielstatus aus der API: {api_status}")
    score = match.get("score") or {}
    voll = score.get("fullTime") or {}
    dauer = score.get("duration") or "REGULAR"
    heim = match.get("homeTeam") or {}
    gast = match.get("awayTeam") or {}
    elfmeter_sieger_api_ref = None
    if dauer == "PENALTY_SHOOTOUT":
        if score.get("winner") == "HOME_TEAM" and heim.get("id") is not None:
            elfmeter_sieger_api_ref = str(heim["id"])
        elif score.get("winner") == "AWAY_TEAM" and gast.get("id") is not None:
            elfmeter_sieger_api_ref = str(gast["id"])
    return {
        "api_ref": str(match["id"]),
        "runde": runde_aus_match(match),
        # Normalisieren auf unser festes UTC-Format
        "anstoss_utc": iso_utc(parse_utc(match["utcDate"])),
        "status": STATUS_MAP[api_status],
        "heim_api_ref": str(heim["id"]) if heim.get("id") is not None else None,
        "gast_api_ref": str(gast["id"]) if gast.get("id") is not None else None,
        "tore_heim": voll.get("home"),
        "tore_gast": voll.get("away"),
        "ergebnis_nach": DAUER_MAP.get(dauer, "90") if api_status in ("FINISHED", "AWARDED") else None,
        "elfmeter_sieger_api_ref": elfmeter_sieger_api_ref,
        "stadion": match.get("venue"),
        # Spielminute liefert die API nur bei Live-Spielen (und nicht in jedem Tarif).
        "minute": match.get("minute") if isinstance(match.get("minute"), int) else None,
    }


def mappe_bilanz(h2h: dict) -> dict[str, Any] | None:
    """Übersetzt die head2head-Aggregate in eine duell_bilanz-Zeile."""
    aggregate = h2h.get("aggregates") or {}
    heim = aggregate.get("homeTeam") or {}
    gast = aggregate.get("awayTeam") or {}
    anzahl = aggregate.get("numberOfMatches")
    if anzahl is None:
        return None
    return {
        "anzahl": anzahl,
        "heim_siege": heim.get("wins") or 0,
        "gast_siege": gast.get("wins") or 0,
        "remis": heim.get("draws") or 0,
        "tore": aggregate.get("totalGoals") or 0,
    }


def gruppe_aus_standing(standing: dict) -> str | None:
    """Normalisiert "Group A"/"GROUP_A" auf den Gruppenbuchstaben."""
    gruppe = (standing.get("group") or "").replace("GROUP_", "").replace("Group ", "").strip()
    return gruppe or None


def mappe_tabellenzeile(eintrag: dict) -> dict[str, Any] | None:
    """Übersetzt einen standings-Tabelleneintrag (Team-Referenz als api_ref)."""
    team = eintrag.get("team") or {}
    if team.get("id") is None:
        return None
    return {
        "team_api_ref": str(team["id"]),
        "platz": eintrag.get("position"),
        "spiele": eintrag.get("playedGames") or 0,
        "siege": eintrag.get("won") or 0,
        "remis": eintrag.get("draw") or 0,
        "niederlagen": eintrag.get("lost") or 0,
        "tore": eintrag.get("goalsFor") or 0,
        "gegentore": eintrag.get("goalsAgainst") or 0,
        "tordifferenz": eintrag.get("goalDifference") or 0,
        "punkte": eintrag.get("points") or 0,
    }


def mappe_torschuetze(eintrag: dict) -> dict[str, Any] | None:
    """Übersetzt einen scorers-Eintrag (Team-Referenz als api_ref)."""
    spieler = eintrag.get("player") or {}
    team = eintrag.get("team") or {}
    if spieler.get("id") is None or not spieler.get("name"):
        return None
    return {
        "api_ref": str(spieler["id"]),
        "name": spieler["name"],
        "team_api_ref": str(team["id"]) if team.get("id") is not None else None,
        "spiele": eintrag.get("playedMatches"),
        "tore": eintrag.get("goals") or 0,
        "vorlagen": eintrag.get("assists"),
        "elfmeter": eintrag.get("penalties"),
    }


def mappe_vergleich(match: dict) -> dict[str, Any]:
    """Übersetzt ein head2head-Match in eine direktvergleich-Zeile."""
    score = match.get("score") or {}
    voll = score.get("fullTime") or {}
    heim = match.get("homeTeam") or {}
    gast = match.get("awayTeam") or {}
    wettbewerb = (match.get("competition") or {}).get("name")
    return {
        "api_ref": str(match["id"]) if match.get("id") is not None else None,
        "datum_utc": iso_utc(parse_utc(match["utcDate"])) if match.get("utcDate") else None,
        "wettbewerb": wettbewerb,
        "heim_name": deutscher_teamname(heim.get("name") or "?"),
        "gast_name": deutscher_teamname(gast.get("name") or "?"),
        "tore_heim": voll.get("home"),
        "tore_gast": voll.get("away"),
    }
