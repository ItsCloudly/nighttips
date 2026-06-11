"""Sync-Jobs (SPEC 4.1): Stammdaten-Sync und Spieltags-Sync.

Beide Jobs holen den kompletten Spielplan (eine Anfrage, 104 Spiele) und
aktualisieren Spiele per Upsert; der Stammdaten-Sync zieht zusätzlich die
Teams. Neu beendete Spiele stoßen die Tipp-Auswertung an.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from .. import db
from ..config import Einstellungen
from ..zeit import iso_utc, jetzt_iso, jetzt_utc
from . import live, push, spielplan, tippspiel
from .fussball_api import (
    ApiQuelle,
    FussballApi,
    FussballApiFehler,
    gruppe_aus_standing,
    mappe_bilanz,
    mappe_match,
    mappe_spieler,
    mappe_tabellenzeile,
    mappe_team,
    mappe_torschuetze,
    mappe_trainer,
    mappe_vergleich,
)

logger = logging.getLogger("wm26.sync")

JOB_STAMMDATEN = "stammdaten"
JOB_ERGEBNISSE = "ergebnisse"
JOB_VERGLEICHE = "vergleiche"

# Unbestätigte Tor-Rücknahmen je api_ref: Die Quelle liefert bei Live-Spielen
# gelegentlich kurz wieder einen alten Stand (Cache-Flattern, z. B. 1:0 → 0:0
# → 1:0), was Geistertore und falsche VAR-Einträge in den Ticker schreibt.
# Ein niedrigerer Stand wird deshalb erst übernommen, wenn ihn der nächste
# Lauf bestätigt; Erhöhungen (Tore) laufen ungebremst durch.
_unbestaetigte_korrekturen: dict[str, tuple[int | None, int | None]] = {}


def _korrektur_unbestaetigt(conn: sqlite3.Connection, daten: dict) -> bool:
    """True: Tor-Rücknahme bei laufendem Spiel, die erst bestätigt werden muss."""
    api_ref = daten.get("api_ref")
    if api_ref is None:
        return False
    zeile = conn.execute(
        "SELECT status, tore_heim, tore_gast FROM spiel WHERE api_ref = ?", (api_ref,)
    ).fetchone()
    if zeile is None or zeile["status"] not in ("live", "halbzeit"):
        _unbestaetigte_korrekturen.pop(api_ref, None)
        return False
    neu = (daten["tore_heim"], daten["tore_gast"])
    alt = (zeile["tore_heim"], zeile["tore_gast"])
    ruecklaeufig = any(
        n is not None and a is not None and n < a for n, a in zip(neu, alt)
    )
    if not ruecklaeufig or _unbestaetigte_korrekturen.get(api_ref) == neu:
        _unbestaetigte_korrekturen.pop(api_ref, None)
        return False
    _unbestaetigte_korrekturen[api_ref] = neu
    return True


@dataclass
class SyncBericht:
    job: str
    teams: int = 0
    spieler: int = 0
    spiele_neu: int = 0
    spiele_aktualisiert: int = 0
    spiele_ausgewertet: int = 0
    vergleiche: int = 0
    tabellenzeilen: int = 0
    torschuetzen: int = 0
    ereignisse: int = 0

    def zusammenfassung(self) -> str:
        if self.job == JOB_VERGLEICHE:
            return f"{self.vergleiche} Spiele mit direkten Vergleichen aktualisiert"
        return (
            f"{self.teams} Teams, {self.spieler} Spieler, {self.spiele_neu} Spiele neu, "
            f"{self.spiele_aktualisiert} aktualisiert, {self.spiele_ausgewertet} ausgewertet, "
            f"{self.tabellenzeilen} Tabellenzeilen, {self.torschuetzen} Torschützen"
        )


def _status_schreiben(
    conn: sqlite3.Connection, job: str, *, erfolg: bool, detail: str
) -> None:
    jetzt = jetzt_iso()
    with db.schreib_transaktion(conn):
        conn.execute(
            "INSERT INTO sync_status (job, letzter_versuch_utc, letzter_erfolg_utc, status, detail)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(job) DO UPDATE SET"
            " letzter_versuch_utc = excluded.letzter_versuch_utc,"
            " letzter_erfolg_utc = COALESCE(excluded.letzter_erfolg_utc, sync_status.letzter_erfolg_utc),"
            " status = excluded.status,"
            " detail = excluded.detail",
            (job, jetzt, jetzt if erfolg else None, "ok" if erfolg else "fehler", detail),
        )


def _spiele_verarbeiten(
    conn: sqlite3.Connection,
    matches: list[dict],
    bericht: SyncBericht,
    *,
    akteur: str,
    einstellungen: Einstellungen | None = None,
) -> list[int]:
    """Upsert aller Spiele innerhalb einer Transaktion; liefert neu beendete Spiel-IDs."""
    neu_beendet: list[int] = []
    # (spiel_id, deltas, ereignis_ids) — nach dem Commit an die SSE-Clients pushen.
    publikationen: list[tuple[int, dict[str, tuple], list[int]]] = []
    with db.schreib_transaktion(conn):
        for match in matches:
            daten = mappe_match(match)
            if _korrektur_unbestaetigt(conn, daten):
                continue
            heim_id = _team_id_aus_api_ref(conn, daten["heim_api_ref"])
            gast_id = _team_id_aus_api_ref(conn, daten["gast_api_ref"])
            ort_id = None
            if daten["stadion"]:
                ort_id = spielplan.spielort_upsert(conn, stadion_name=daten["stadion"])
            elfmeter_sieger_id = _team_id_aus_api_ref(conn, daten["elfmeter_sieger_api_ref"])
            upsert = spielplan.spiel_upsert(
                conn,
                runde=daten["runde"],
                anstoss_utc=daten["anstoss_utc"],
                heim_team_id=heim_id,
                gast_team_id=gast_id,
                spielort_id=ort_id,
                status=daten["status"],
                tore_heim=daten["tore_heim"],
                tore_gast=daten["tore_gast"],
                ergebnis_nach=daten["ergebnis_nach"],
                elfmeter_sieger_team_id=elfmeter_sieger_id,
                api_ref=daten["api_ref"],
                quelle="api",
                akteur=akteur,
            )
            if upsert.neu:
                bericht.spiele_neu += 1
            elif upsert.geaendert:
                bericht.spiele_aktualisiert += 1
            if upsert.neu_beendet:
                neu_beendet.append(upsert.spiel_id)
            if upsert.deltas:
                ereignis_ids = live.deltas_verarbeiten(
                    conn,
                    spiel_id=upsert.spiel_id,
                    deltas=upsert.deltas,
                    minute=daten.get("minute"),
                )
                bericht.ereignisse += len(ereignis_ids)
                publikationen.append((upsert.spiel_id, upsert.deltas, ereignis_ids))
            # Gruppenzugehörigkeit der Teams aus den Gruppenspielen ableiten
            # (der Teams-Endpunkt liefert keine Gruppen).
            if daten["runde"].startswith("Gruppe "):
                gruppe = daten["runde"].removeprefix("Gruppe ")
                spielplan.team_gruppe_setzen(conn, heim_id, gruppe)
                spielplan.team_gruppe_setzen(conn, gast_id, gruppe)
    for spiel_id, deltas, ereignis_ids in publikationen:
        try:
            live.sync_delta_publizieren(conn, spiel_id, deltas, ereignis_ids)
        except Exception:
            # SSE ist Komfort: ein Fehler darf weder die übrigen Spiele
            # noch den Sync selbst stoppen (Clients laden beim Reload nach).
            logger.exception("SSE-Publikation für Spiel %s fehlgeschlagen", spiel_id)
        if einstellungen is not None:
            try:
                push.ereignis_pushen(conn, einstellungen, spiel_id=spiel_id, deltas=deltas)
            except Exception:
                logger.exception("Push-Versand für Spiel %s fehlgeschlagen", spiel_id)
    return neu_beendet


def _team_id_aus_api_ref(conn: sqlite3.Connection, api_ref: str | None) -> int | None:
    if api_ref is None:
        return None
    zeile = conn.execute("SELECT id FROM team WHERE api_ref = ?", (api_ref,)).fetchone()
    return zeile["id"] if zeile else None


def _teams_anlegen(conn: sqlite3.Connection, matches: list[dict]) -> None:
    """Legt Teams an, die nur im Spielplan vorkommen (Fallback ohne Teams-Abruf)."""
    with db.schreib_transaktion(conn):
        for match in matches:
            for seite in ("homeTeam", "awayTeam"):
                team = match.get(seite) or {}
                if team.get("id") is None or team.get("name") is None:
                    continue
                spielplan.team_upsert(conn, **mappe_team(team))


def _tabellen_aktualisieren(conn: sqlite3.Connection, api: ApiQuelle, bericht: SyncBericht) -> None:
    """Ersetzt die Gruppentabellen durch den offiziellen Stand (1 API-Call)."""
    standings = api.standings()
    with db.schreib_transaktion(conn):
        conn.execute("DELETE FROM gruppen_tabelle")
        for standing in standings:
            gruppe = gruppe_aus_standing(standing)
            if gruppe is None:
                continue
            for eintrag in standing.get("table") or []:
                zeile = mappe_tabellenzeile(eintrag)
                if zeile is None:
                    continue
                team_id = _team_id_aus_api_ref(conn, zeile.pop("team_api_ref"))
                if team_id is None:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO gruppen_tabelle (team_id, gruppe, platz, spiele,"
                    " siege, remis, niederlagen, tore, gegentore, tordifferenz, punkte)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        team_id,
                        gruppe,
                        zeile["platz"],
                        zeile["spiele"],
                        zeile["siege"],
                        zeile["remis"],
                        zeile["niederlagen"],
                        zeile["tore"],
                        zeile["gegentore"],
                        zeile["tordifferenz"],
                        zeile["punkte"],
                    ),
                )
                bericht.tabellenzeilen += 1


def _torschuetzen_aktualisieren(
    conn: sqlite3.Connection, api: ApiQuelle, bericht: SyncBericht
) -> None:
    """Ersetzt die Torschützenliste durch den aktuellen Stand (1 API-Call)."""
    scorers = api.scorers(limit=30)
    with db.schreib_transaktion(conn):
        conn.execute("DELETE FROM torschuetze")
        for eintrag in scorers:
            daten = mappe_torschuetze(eintrag)
            if daten is None:
                continue
            team_id = _team_id_aus_api_ref(conn, daten["team_api_ref"])
            conn.execute(
                "INSERT OR REPLACE INTO torschuetze"
                " (api_ref, name, team_id, spiele, tore, vorlagen, elfmeter)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    daten["api_ref"],
                    daten["name"],
                    team_id,
                    daten["spiele"],
                    daten["tore"],
                    daten["vorlagen"],
                    daten["elfmeter"],
                ),
            )
            bericht.torschuetzen += 1


def stammdaten_sync(
    conn: sqlite3.Connection, einstellungen: Einstellungen, api: ApiQuelle | None = None
) -> SyncBericht:
    """Täglicher Sync: Teams plus kompletter Spielplan."""
    bericht = SyncBericht(job=JOB_STAMMDATEN)
    try:
        if api is None:
            api = FussballApi(einstellungen)
        teams = api.teams()
        with db.schreib_transaktion(conn):
            for team in teams:
                team_id = spielplan.team_upsert(conn, **mappe_team(team))
                bericht.teams += 1
                # Trainer und Kader liefert derselbe Teams-Abruf gleich mit.
                trainer = mappe_trainer(team)
                if trainer is not None:
                    spielplan.trainer_upsert(conn, team_id=team_id, **trainer)
                kader = [
                    daten
                    for daten in (mappe_spieler(s) for s in team.get("squad") or [])
                    if daten is not None
                ]
                if kader:
                    bericht.spieler += spielplan.kader_ersetzen(
                        conn, team_id=team_id, spieler_liste=kader
                    )
        matches = api.matches()
        _teams_anlegen(conn, matches)
        neu_beendet = _spiele_verarbeiten(
            conn, matches, bericht, akteur=JOB_STAMMDATEN, einstellungen=einstellungen
        )
        _tabellen_aktualisieren(conn, api, bericht)
        _torschuetzen_aktualisieren(conn, api, bericht)
        bericht.spiele_ausgewertet = _auswerten(conn, einstellungen, neu_beendet)
    except Exception as fehler:
        # Auch Nicht-API-Fehler (z. B. DB-Probleme) im Status sichtbar machen.
        _status_schreiben(conn, JOB_STAMMDATEN, erfolg=False, detail=str(fehler))
        raise
    _status_schreiben(conn, JOB_STAMMDATEN, erfolg=True, detail=bericht.zusammenfassung())
    return bericht


def ergebnis_sync(
    conn: sqlite3.Connection, einstellungen: Einstellungen, api: ApiQuelle | None = None
) -> SyncBericht:
    """Spieltags-Sync: Status, Anstoßzeiten und Ergebnisse; wertet neu beendete Spiele aus."""
    bericht = SyncBericht(job=JOB_ERGEBNISSE)
    try:
        if api is None:
            api = FussballApi(einstellungen)
        matches = api.matches()
        _teams_anlegen(conn, matches)
        neu_beendet = _spiele_verarbeiten(
            conn, matches, bericht, akteur=JOB_ERGEBNISSE, einstellungen=einstellungen
        )
        # Nachzügler: beendete Spiele, deren Tipps noch keine Punkte haben
        # (z. B. nach einem Absturz zwischen Sync und Auswertung).
        offene = tippspiel.spiele_mit_offener_auswertung(conn)
        neu_beendet = sorted(set(neu_beendet) | set(offene))
        # Tabelle und Torschützen nur nachziehen, wenn sich etwas geändert haben kann
        # (spart 2 API-Calls pro Lauf im 60-Sekunden-Takt an Spieltagen).
        if neu_beendet:
            _tabellen_aktualisieren(conn, api, bericht)
            _torschuetzen_aktualisieren(conn, api, bericht)
        bericht.spiele_ausgewertet = _auswerten(conn, einstellungen, neu_beendet)
    except Exception as fehler:
        # Auch Nicht-API-Fehler (z. B. DB-Probleme) im Status sichtbar machen.
        _status_schreiben(conn, JOB_ERGEBNISSE, erfolg=False, detail=str(fehler))
        raise
    _status_schreiben(conn, JOB_ERGEBNISSE, erfolg=True, detail=bericht.zusammenfassung())
    return bericht


def vergleiche_sync(
    conn: sqlite3.Connection,
    einstellungen: Einstellungen,
    api: ApiQuelle | None = None,
    *,
    max_abrufe: int | None = None,
    nur_naechste_tage: int | None = None,
) -> SyncBericht:
    """Direkte Vergleiche (head2head) je Spiel laden — 1 API-Call pro Spiel.

    Wiederaufnehmbar: bereits abgerufene Spiele (h2h_abruf) werden übersprungen,
    der API-Client drosselt auf das Free-Tier-Limit. Für K.o.-Spiele passiert
    erst etwas, sobald beide Teams feststehen.
    """
    bericht = SyncBericht(job=JOB_VERGLEICHE)
    try:
        if api is None:
            api = FussballApi(einstellungen)
        sql = (
            "SELECT s.id, s.api_ref FROM spiel s"
            " LEFT JOIN h2h_abruf a ON a.spiel_id = s.id"
            " WHERE s.api_ref IS NOT NULL AND a.spiel_id IS NULL"
            " AND s.heim_team_id IS NOT NULL AND s.gast_team_id IS NOT NULL"
        )
        parameter: list[object] = []
        if nur_naechste_tage is not None:
            sql += " AND s.anstoss_utc <= ?"
            parameter.append(_iso_utc_in_tagen(nur_naechste_tage))
        sql += " ORDER BY s.anstoss_utc"
        if max_abrufe is not None:
            sql += " LIMIT ?"
            parameter.append(max_abrufe)
        offene = conn.execute(sql, parameter).fetchall()
        for spiel in offene:
            h2h = api.head2head(spiel["api_ref"], limit=5)
            duelle = h2h.get("matches") or []
            bilanz = mappe_bilanz(h2h)
            with db.schreib_transaktion(conn):
                if bilanz is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO duell_bilanz"
                        " (spiel_id, anzahl, heim_siege, gast_siege, remis, tore)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            spiel["id"],
                            bilanz["anzahl"],
                            bilanz["heim_siege"],
                            bilanz["gast_siege"],
                            bilanz["remis"],
                            bilanz["tore"],
                        ),
                    )
                conn.execute("DELETE FROM direktvergleich WHERE spiel_id = ?", (spiel["id"],))
                for duell in duelle:
                    daten = mappe_vergleich(duell)
                    conn.execute(
                        "INSERT INTO direktvergleich (spiel_id, datum_utc, wettbewerb,"
                        " heim_name, gast_name, tore_heim, tore_gast, api_ref)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            spiel["id"],
                            daten["datum_utc"],
                            daten["wettbewerb"],
                            daten["heim_name"],
                            daten["gast_name"],
                            daten["tore_heim"],
                            daten["tore_gast"],
                            daten["api_ref"],
                        ),
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO h2h_abruf (spiel_id, abgerufen_utc) VALUES (?, ?)",
                    (spiel["id"], jetzt_iso()),
                )
            bericht.vergleiche += 1
    except Exception as fehler:
        # Auch Nicht-API-Fehler (z. B. DB-Probleme) im Status sichtbar machen.
        detail = f"{bericht.vergleiche} geladen, dann Fehler: {fehler}"
        _status_schreiben(conn, JOB_VERGLEICHE, erfolg=False, detail=detail)
        raise
    _status_schreiben(conn, JOB_VERGLEICHE, erfolg=True, detail=bericht.zusammenfassung())
    return bericht


def _iso_utc_in_tagen(tage: int) -> str:
    return iso_utc(jetzt_utc() + timedelta(days=tage))


def _auswerten(
    conn: sqlite3.Connection, einstellungen: Einstellungen, spiel_ids: list[int]
) -> int:
    anzahl = 0
    for spiel_id in spiel_ids:
        try:
            tippspiel.spiel_auswerten(conn, spiel_id, einstellungen, akteur="sync")
            anzahl += 1
        except Exception:
            logger.exception("Tipp-Auswertung für Spiel %s fehlgeschlagen", spiel_id)
    return anzahl
