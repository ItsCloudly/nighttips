"""Wettquoten von The Odds API (v0.1.1): 1X2 je WM-Spiel, z. B. von Tipico.

Ein Abruf liefert alle anstehenden Spiele des Wettbewerbs auf einmal
(1 Credit je Markt × Buchmacher-Gruppe) — der tägliche Sync bleibt damit
weit unter dem Free-Tier (500 Credits/Monat). Ohne WM26_QUOTEN_TOKEN
bleibt das Feature komplett aus; Quoten sind reine Orientierung.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

import httpx

from .. import db
from ..config import Einstellungen
from ..zeit import iso_utc, jetzt_iso, jetzt_utc
from . import sync
from .laender import deutscher_teamname

JOB_QUOTEN = "quoten"

# The Odds API benennt manche Teams anders als football-data.org —
# direkte Zuordnung auf unsere deutschen Teamnamen.
ALIAS_DE = {
    "Bosnia and Herzegovina": "Bosnien-Herzegowina",
    "Cape Verde": "Kap Verde",
    "Curacao": "Curaçao",
    "Czech Republic": "Tschechien",
    "DR Congo": "DR Kongo",
    "Korea Republic": "Südkorea",
    "Türkiye": "Türkei",
    "USA": "USA",
}


class QuotenFehler(Exception):
    pass


@dataclass
class QuotenBericht:
    job: str = JOB_QUOTEN
    events: int = 0
    aktualisiert: int = 0
    ohne_quoten: int = 0
    ohne_zuordnung: int = 0

    def zusammenfassung(self) -> str:
        return (
            f"{self.events} Spiele von der API, {self.aktualisiert} Quoten aktualisiert, "
            f"{self.ohne_quoten} ohne Buchmacher-Quote, {self.ohne_zuordnung} ohne Zuordnung"
        )


def aktiv(einstellungen: Einstellungen) -> bool:
    return bool(einstellungen.quoten_token)


def abruf_faellig(conn: sqlite3.Connection, *, stunden: int = 20) -> bool:
    """1x täglich reicht (wie der Stammdaten-Sync)."""
    zeile = conn.execute(
        "SELECT letzter_erfolg_utc FROM sync_status WHERE job = ?", (JOB_QUOTEN,)
    ).fetchone()
    if zeile is None or not zeile["letzter_erfolg_utc"]:
        return True
    return zeile["letzter_erfolg_utc"] < iso_utc(jetzt_utc() - timedelta(hours=stunden))


def _name_de(api_name: str) -> str:
    return ALIAS_DE.get(api_name) or deutscher_teamname(api_name)


def _abrufen(einstellungen: Einstellungen) -> list[dict]:
    parameter = {
        "apiKey": einstellungen.quoten_token,
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    # bookmakers ersetzt regions (so zählt der Abruf nur 1 Credit);
    # ohne konfigurierten Buchmacher die ganze EU-Region holen.
    if einstellungen.quoten_buchmacher:
        parameter["bookmakers"] = einstellungen.quoten_buchmacher
    else:
        parameter["regions"] = "eu"
    try:
        antwort = httpx.get(
            f"{einstellungen.quoten_basis_url}/sports/{einstellungen.quoten_sport}/odds",
            params=parameter,
            timeout=20,
        )
        antwort.raise_for_status()
        daten = antwort.json()
    except httpx.HTTPError as fehler:
        raise QuotenFehler(f"Quoten-Abruf fehlgeschlagen: {fehler}") from fehler
    except ValueError as fehler:
        raise QuotenFehler(f"Quoten-Antwort ist kein JSON: {fehler}") from fehler
    if not isinstance(daten, list):
        raise QuotenFehler("Unerwartete Antwort der Quoten-API (keine Liste)")
    return daten


def _h2h_quoten(event: dict) -> tuple[str, float, float, float] | None:
    """Erster Buchmacher mit vollständigem 1X2-Markt: (Anbieter, 1, X, 2)."""
    for buchmacher in event.get("bookmakers") or []:
        for markt in buchmacher.get("markets") or []:
            if markt.get("key") != "h2h":
                continue
            preise = {
                eintrag.get("name"): eintrag.get("price")
                for eintrag in markt.get("outcomes") or []
            }
            heim = preise.get(event.get("home_team"))
            gast = preise.get(event.get("away_team"))
            remis = preise.get("Draw")
            if heim and remis and gast:
                anbieter = buchmacher.get("title") or buchmacher.get("key") or "?"
                return (str(anbieter), float(heim), float(remis), float(gast))
    return None


def _spiel_finden(conn: sqlite3.Connection, event: dict) -> int | None:
    """Zuordnung über Anstoßzeit + Teamnamen; eindeutige Anstoßzeit als Fallback."""
    anstoss = str(event.get("commence_time") or "")
    heim = _name_de(str(event.get("home_team") or ""))
    gast = _name_de(str(event.get("away_team") or ""))
    zeile = conn.execute(
        "SELECT s.id FROM spiel s"
        " JOIN team th ON th.id = s.heim_team_id"
        " JOIN team tg ON tg.id = s.gast_team_id"
        " WHERE s.anstoss_utc = ? AND th.name = ? AND tg.name = ?",
        (anstoss, heim, gast),
    ).fetchone()
    if zeile:
        return zeile["id"]
    kandidaten = conn.execute(
        "SELECT id FROM spiel WHERE anstoss_utc = ?", (anstoss,)
    ).fetchall()
    if len(kandidaten) == 1:
        return kandidaten[0]["id"]
    return None


def quoten_sync(
    conn: sqlite3.Connection,
    einstellungen: Einstellungen,
    *,
    daten: list[dict] | None = None,
) -> QuotenBericht:
    """Quoten abrufen und je Spiel upserten. `daten` erlaubt Tests ohne HTTP."""
    bericht = QuotenBericht()
    if daten is None:
        try:
            daten = _abrufen(einstellungen)
        except QuotenFehler as fehler:
            sync._status_schreiben(conn, JOB_QUOTEN, erfolg=False, detail=str(fehler))
            raise
    jetzt = jetzt_iso()
    with db.schreib_transaktion(conn):
        for event in daten:
            bericht.events += 1
            quoten = _h2h_quoten(event)
            if quoten is None:
                bericht.ohne_quoten += 1
                continue
            spiel_id = _spiel_finden(conn, event)
            if spiel_id is None:
                bericht.ohne_zuordnung += 1
                continue
            anbieter, heim, remis, gast = quoten
            conn.execute(
                "INSERT INTO quote (spiel_id, anbieter, heim, remis, gast, abruf_utc)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(spiel_id, anbieter) DO UPDATE SET"
                " heim = excluded.heim, remis = excluded.remis, gast = excluded.gast,"
                " abruf_utc = excluded.abruf_utc",
                (spiel_id, anbieter, heim, remis, gast, jetzt),
            )
            bericht.aktualisiert += 1
    sync._status_schreiben(conn, JOB_QUOTEN, erfolg=True, detail=bericht.zusammenfassung())
    return bericht
