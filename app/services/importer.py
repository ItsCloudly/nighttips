"""JSON-Import des Spielplans (Teams, Spielorte, Spiele) für Bootstrap und Notbetrieb.

Erwartetes Format (alle Abschnitte optional):
{
  "teams":     [{"fifa_code": "GER", "name": "Deutschland", "gruppe": "E", "flagge_url": null}],
  "spielorte": [{"stadion_name": "MetLife Stadium", "stadt": "East Rutherford", "land": "USA",
                 "kapazitaet": 82500, "zeitzone": "America/New_York"}],
  "spiele":    [{"runde": "Gruppe E", "anstoss_utc": "2026-06-15T18:00:00Z",
                 "stadion": "MetLife Stadium", "heim": "GER", "gast": "SCO",
                 "status": "geplant", "tore_heim": null, "tore_gast": null}]
}
Teams in Spielen werden über den fifa_code referenziert, Spielorte über den Namen.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .. import db
from . import spielplan


@dataclass
class ImportErgebnis:
    teams: int = 0
    spielorte: int = 0
    spiele_neu: int = 0
    spiele_aktualisiert: int = 0
    neu_beendete_spiel_ids: list[int] | None = None

    def __post_init__(self) -> None:
        if self.neu_beendete_spiel_ids is None:
            self.neu_beendete_spiel_ids = []


def spielplan_importieren(
    conn: sqlite3.Connection, daten: dict, *, akteur: str = "import"
) -> ImportErgebnis:
    if not isinstance(daten, dict):
        raise ValueError("Spielplan-Daten müssen ein JSON-Objekt sein")
    ergebnis = ImportErgebnis()
    with db.schreib_transaktion(conn):
        for team in daten.get("teams", []):
            spielplan.team_upsert(
                conn,
                name=team["name"],
                fifa_code=team.get("fifa_code"),
                gruppe=team.get("gruppe"),
                flagge_url=team.get("flagge_url"),
            )
            ergebnis.teams += 1
        for ort in daten.get("spielorte", []):
            spielplan.spielort_upsert(
                conn,
                stadion_name=ort["stadion_name"],
                stadt=ort.get("stadt"),
                land=ort.get("land"),
                kapazitaet=ort.get("kapazitaet"),
                zeitzone=ort.get("zeitzone"),
            )
            ergebnis.spielorte += 1
        for spiel in daten.get("spiele", []):
            heim_id = _team_id_aus_code(conn, spiel.get("heim"))
            gast_id = _team_id_aus_code(conn, spiel.get("gast"))
            ort_id = None
            if spiel.get("stadion"):
                ort_id = spielplan.spielort_upsert(conn, stadion_name=spiel["stadion"])
            upsert = spielplan.spiel_upsert(
                conn,
                runde=spiel["runde"],
                anstoss_utc=spiel["anstoss_utc"],
                heim_team_id=heim_id,
                gast_team_id=gast_id,
                spielort_id=ort_id,
                status=spiel.get("status", "geplant"),
                tore_heim=spiel.get("tore_heim"),
                tore_gast=spiel.get("tore_gast"),
                ergebnis_nach=spiel.get("ergebnis_nach"),
                elfmeter_sieger_team_id=None,
                api_ref=spiel.get("api_ref"),
                quelle="admin",
                akteur=akteur,
            )
            if upsert.neu:
                ergebnis.spiele_neu += 1
            elif upsert.geaendert:
                ergebnis.spiele_aktualisiert += 1
            if upsert.neu_beendet:
                ergebnis.neu_beendete_spiel_ids.append(upsert.spiel_id)
    return ergebnis


def _team_id_aus_code(conn: sqlite3.Connection, fifa_code: str | None) -> int | None:
    if not fifa_code:
        return None
    zeile = conn.execute("SELECT id FROM team WHERE fifa_code = ?", (fifa_code,)).fetchone()
    if zeile is None:
        raise ValueError(f"Unbekanntes Team im Spielplan: {fifa_code}")
    return zeile["id"]
