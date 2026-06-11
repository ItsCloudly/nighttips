"""Aufstellungen von der inoffiziellen ESPN-API (v0.1.1).

ESPN veröffentlicht die offiziellen Startaufstellungen ~60 Minuten vor
Anpfiff (`summary`-Endpoint: rosters mit starter-Flag, Trikotnummer und
Formation). Inoffizielle Quelle ohne Key — Fehler sind nie kritisch, die
App funktioniert ohne Aufstellungen unverändert (Fallback: kompletter
Kader auf dem Feld). Aus den gespeicherten Startelfs leitet sich die
„übliche Startelf" je Team ab (Team-Lupe).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

import httpx

from .. import db
from ..config import Einstellungen
from ..zeit import iso_utc, jetzt_iso, jetzt_utc
from . import sync
from .laender import deutscher_name_extern

JOB_AUFSTELLUNGEN = "aufstellungen"

# Fenster wie der Aufstellungs-Poll der SPEC (4.1): ab T-75 bis in die
# Live-Phase hinein (falls ESPN spät dran ist), höchstens alle 5 Minuten.
_VORLAUF_MINUTEN = 75
_NACHLAUF_STUNDEN = 3
_VERSUCHS_ABSTAND_MINUTEN = 5


class AufstellungsFehler(Exception):
    pass


@dataclass
class AufstellungsBericht:
    job: str = JOB_AUFSTELLUNGEN
    geprueft: int = 0
    gespeichert: int = 0
    unvollstaendig: int = 0
    spieler_unbekannt: int = 0

    def zusammenfassung(self) -> str:
        return (
            f"{self.geprueft} Spiele geprüft, {self.gespeichert} Team-Aufstellungen "
            f"gespeichert, {self.unvollstaendig} noch unvollständig, "
            f"{self.spieler_unbekannt} Spieler ohne Zuordnung"
        )


def abruf_faellig(conn: sqlite3.Connection, *, minuten: int = _VERSUCHS_ABSTAND_MINUTEN) -> bool:
    zeile = conn.execute(
        "SELECT letzter_versuch_utc FROM sync_status WHERE job = ?", (JOB_AUFSTELLUNGEN,)
    ).fetchone()
    if zeile is None or not zeile["letzter_versuch_utc"]:
        return True
    return zeile["letzter_versuch_utc"] < iso_utc(jetzt_utc() - timedelta(minutes=minuten))


def _kandidaten(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Spiele im Aufstellungs-Fenster, denen noch Startelf-Daten fehlen."""
    jetzt = jetzt_utc()
    return conn.execute(
        """
        SELECT s.id, s.anstoss_utc, s.espn_ref,
               s.heim_team_id, s.gast_team_id,
               th.name AS heim_name, tg.name AS gast_name
        FROM spiel s
        JOIN team th ON th.id = s.heim_team_id
        JOIN team tg ON tg.id = s.gast_team_id
        WHERE s.status IN ('geplant', 'live', 'halbzeit')
          AND s.anstoss_utc BETWEEN ? AND ?
          AND (
            SELECT COUNT(DISTINCT a.team_id) FROM aufstellung a
            WHERE a.spiel_id = s.id AND a.rolle = 'startelf'
          ) < 2
        ORDER BY s.anstoss_utc
        """,
        (
            iso_utc(jetzt - timedelta(hours=_NACHLAUF_STUNDEN)),
            iso_utc(jetzt + timedelta(minutes=_VORLAUF_MINUTEN)),
        ),
    ).fetchall()


def _iso_normalisieren(wert: str) -> str:
    """ESPN liefert teils '2026-06-11T19:00Z' — auf Sekunden-Format bringen."""
    if len(wert) == 17 and wert.endswith("Z"):
        return f"{wert[:-1]}:00Z"
    return wert


def _http_json(url: str, parameter: dict | None = None) -> Any:
    try:
        antwort = httpx.get(url, params=parameter, timeout=20)
        antwort.raise_for_status()
        return antwort.json()
    except httpx.HTTPError as fehler:
        raise AufstellungsFehler(f"ESPN-Abruf fehlgeschlagen: {fehler}") from fehler
    except ValueError as fehler:
        raise AufstellungsFehler(f"ESPN-Antwort ist kein JSON: {fehler}") from fehler


def _scoreboard_event_teams(event: dict) -> tuple[str, str] | None:
    wettkampf = (event.get("competitions") or [{}])[0]
    heim = gast = None
    for teilnehmer in wettkampf.get("competitors") or []:
        name = deutscher_name_extern(
            str((teilnehmer.get("team") or {}).get("displayName") or "")
        )
        if teilnehmer.get("homeAway") == "home":
            heim = name
        elif teilnehmer.get("homeAway") == "away":
            gast = name
    if heim and gast:
        return heim, gast
    return None


def _refs_zuordnen(
    conn: sqlite3.Connection, spiele: list[sqlite3.Row], scoreboard: dict
) -> None:
    """ESPN-Event-IDs über Anstoßzeit + Teamnamen an unsere Spiele heften."""
    events = []
    for event in scoreboard.get("events") or []:
        teams = _scoreboard_event_teams(event)
        if not event.get("id") or not event.get("date") or teams is None:
            continue
        events.append((_iso_normalisieren(str(event["date"])), teams, str(event["id"])))
    with db.schreib_transaktion(conn):
        for spiel in spiele:
            for datum, (heim, gast), event_id in events:
                if datum != spiel["anstoss_utc"]:
                    continue
                if heim == spiel["heim_name"] and gast == spiel["gast_name"]:
                    conn.execute(
                        "UPDATE spiel SET espn_ref = ? WHERE id = ?", (event_id, spiel["id"])
                    )
                    break
            else:
                # Eindeutiger Zeit-Treffer als Fallback (nur ein Event zur Anstoßzeit)
                zeitgleich = [e for e in events if e[0] == spiel["anstoss_utc"]]
                if len(zeitgleich) == 1:
                    conn.execute(
                        "UPDATE spiel SET espn_ref = ? WHERE id = ?",
                        (zeitgleich[0][2], spiel["id"]),
                    )


def _spieler_finden(
    conn: sqlite3.Connection, team_id: int, trikotnummer: int | None, name: str
) -> int | None:
    """Spieler-Matching: Trikotnummer zuerst, dann exakter Name, dann Nachname."""
    if trikotnummer is not None:
        zeile = conn.execute(
            "SELECT id FROM spieler WHERE team_id = ? AND trikotnummer = ?",
            (team_id, trikotnummer),
        ).fetchone()
        if zeile:
            return zeile["id"]
    kandidaten = conn.execute(
        "SELECT id, name FROM spieler WHERE team_id = ?", (team_id,)
    ).fetchall()
    gesucht = name.casefold().strip()
    for zeile in kandidaten:
        if zeile["name"].casefold().strip() == gesucht:
            return zeile["id"]
    nachname = gesucht.split()[-1] if gesucht else ""
    treffer = [
        zeile["id"]
        for zeile in kandidaten
        if nachname and zeile["name"].casefold().split()[-1] == nachname
    ]
    return treffer[0] if len(treffer) == 1 else None


def _team_aufstellung_speichern(
    conn: sqlite3.Connection,
    spiel: sqlite3.Row,
    team_id: int,
    roster: dict,
    bericht: AufstellungsBericht,
) -> bool:
    """Eine Team-Aufstellung übernehmen; nur wenn die komplette Startelf passt."""
    eintraege = roster.get("roster") or []
    formation = roster.get("formation")
    if isinstance(formation, dict):
        formation = formation.get("name") or formation.get("formation")
    zugeordnet: list[tuple[int, str, str | None]] = []
    starter = 0
    for eintrag in eintraege:
        athlet = eintrag.get("athlete") or {}
        name = str(athlet.get("displayName") or "")
        try:
            nummer = int(eintrag.get("jersey"))
        except (TypeError, ValueError):
            nummer = None
        rolle = "startelf" if eintrag.get("starter") else "bank"
        if rolle == "startelf":
            starter += 1
        spieler_id = _spieler_finden(conn, team_id, nummer, name)
        if spieler_id is None:
            bericht.spieler_unbekannt += 1
            continue
        position = (eintrag.get("position") or {}).get("abbreviation")
        zugeordnet.append((spieler_id, rolle, position))
    startelf = [eintrag for eintrag in zugeordnet if eintrag[1] == "startelf"]
    if starter < 11 or len(startelf) < 11:
        # Aufstellung noch nicht (vollständig) veröffentlicht oder zu viele
        # unbekannte Spieler — lieber gar nichts speichern als Lücken.
        bericht.unvollstaendig += 1
        return False
    jetzt = jetzt_iso()
    with db.schreib_transaktion(conn):
        conn.execute(
            "DELETE FROM aufstellung WHERE spiel_id = ? AND team_id = ? AND quelle = 'api'",
            (spiel["id"], team_id),
        )
        for spieler_id, rolle, position in zugeordnet:
            conn.execute(
                "INSERT INTO aufstellung (spiel_id, team_id, spieler_id, rolle,"
                " position_im_system, formation, quelle, erstellt_utc)"
                " VALUES (?, ?, ?, ?, ?, ?, 'api', ?)"
                " ON CONFLICT(spiel_id, spieler_id) DO UPDATE SET"
                " rolle = excluded.rolle, position_im_system = excluded.position_im_system,"
                " formation = excluded.formation, erstellt_utc = excluded.erstellt_utc",
                (
                    spiel["id"],
                    team_id,
                    spieler_id,
                    rolle,
                    position,
                    str(formation) if formation else None,
                    jetzt,
                ),
            )
    bericht.gespeichert += 1
    return True


def aufstellungen_sync(
    conn: sqlite3.Connection,
    einstellungen: Einstellungen,
    *,
    scoreboard: dict | None = None,
    summary_lader: Callable[[str], dict] | None = None,
) -> AufstellungsBericht:
    """Aufstellungen für anstehende Spiele holen.

    `scoreboard`/`summary_lader` erlauben Tests ohne HTTP.
    """
    bericht = AufstellungsBericht()
    spiele = _kandidaten(conn)
    bericht.geprueft = len(spiele)
    if not spiele:
        return bericht
    try:
        ohne_ref = [spiel for spiel in spiele if not spiel["espn_ref"]]
        if ohne_ref:
            if scoreboard is None:
                scoreboard = _http_json(f"{einstellungen.aufstellungen_basis_url}/scoreboard")
            _refs_zuordnen(conn, ohne_ref, scoreboard)
            spiele = _kandidaten(conn)
        for spiel in spiele:
            if not spiel["espn_ref"]:
                continue
            if summary_lader is not None:
                summary = summary_lader(spiel["espn_ref"])
            else:
                summary = _http_json(
                    f"{einstellungen.aufstellungen_basis_url}/summary",
                    {"event": spiel["espn_ref"]},
                )
            seiten = {"home": spiel["heim_team_id"], "away": spiel["gast_team_id"]}
            for roster in summary.get("rosters") or []:
                team_id = seiten.get(roster.get("homeAway"))
                if team_id is None:
                    continue
                _team_aufstellung_speichern(conn, spiel, team_id, roster, bericht)
    except AufstellungsFehler as fehler:
        sync._status_schreiben(conn, JOB_AUFSTELLUNGEN, erfolg=False, detail=str(fehler))
        raise
    sync._status_schreiben(
        conn, JOB_AUFSTELLUNGEN, erfolg=True, detail=bericht.zusammenfassung()
    )
    return bericht


def uebliche_startelf(conn: sqlite3.Connection, team_id: int) -> dict[str, Any] | None:
    """Die „übliche Startelf": die 11 häufigsten Starter der bisherigen Spiele.

    None, solange es keine vollständige Datenbasis gibt — die Team-Lupe
    zeigt dann wie bisher den kompletten Kader.
    """
    basis = conn.execute(
        "SELECT COUNT(DISTINCT spiel_id) AS spiele FROM aufstellung"
        " WHERE team_id = ? AND rolle = 'startelf'",
        (team_id,),
    ).fetchone()["spiele"]
    if not basis:
        return None
    zeilen = conn.execute(
        """
        SELECT sp.id, sp.name, sp.trikotnummer, sp.position,
               COUNT(*) AS einsaetze
        FROM aufstellung a JOIN spieler sp ON sp.id = a.spieler_id
        WHERE a.team_id = ? AND a.rolle = 'startelf'
        GROUP BY sp.id
        ORDER BY einsaetze DESC,
                 CASE sp.position WHEN 'Torwart' THEN 0 WHEN 'Abwehr' THEN 1
                                  WHEN 'Mittelfeld' THEN 2 WHEN 'Sturm' THEN 3 ELSE 4 END,
                 sp.name COLLATE NOCASE
        LIMIT 11
        """,
        (team_id,),
    ).fetchall()
    if len(zeilen) < 11:
        return None
    formation = conn.execute(
        "SELECT formation, COUNT(*) AS anzahl FROM aufstellung"
        " WHERE team_id = ? AND rolle = 'startelf' AND formation IS NOT NULL"
        " GROUP BY formation ORDER BY anzahl DESC LIMIT 1",
        (team_id,),
    ).fetchone()
    return {
        "spieler": [dict(zeile) for zeile in zeilen],
        "formation": formation["formation"] if formation else None,
        "spiele_basis": basis,
    }
