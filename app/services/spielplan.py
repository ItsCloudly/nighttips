"""Upsert-Bausteine für Stammdaten (Teams, Spielorte, Spiele).

Gemeinsame Grundlage für den JSON-Import und den API-Sync. Alle Funktionen
erwarten eine bereits offene Schreib-Transaktion (db.schreib_transaktion ist
nicht reentrant) und schreiben für relevante Spiel-Felder change_log-Einträge.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .. import db
from ..zeit import jetzt_iso

# Spiel-Felder, deren Änderung im change_log nachvollziehbar sein soll.
_SPIEL_LOG_FELDER = ("anstoss_utc", "status", "tore_heim", "tore_gast", "ergebnis_nach")


@dataclass
class SpielUpsertErgebnis:
    spiel_id: int
    neu: bool = False
    geaendert: bool = False
    neu_beendet: bool = False
    # Feld -> (alt, neu) für die im change_log protokollierten Felder;
    # Grundlage der Live-Ereignisse (Tor, Anpfiff, Abpfiff) im Sync.
    deltas: dict[str, tuple] | None = None


def team_upsert(
    conn: sqlite3.Connection,
    *,
    name: str,
    fifa_code: str | None = None,
    gruppe: str | None = None,
    flagge_url: str | None = None,
    api_ref: str | None = None,
) -> int:
    zeile = None
    if api_ref is not None:
        zeile = conn.execute("SELECT id FROM team WHERE api_ref = ?", (api_ref,)).fetchone()
    if zeile is None and fifa_code:
        zeile = conn.execute("SELECT id FROM team WHERE fifa_code = ?", (fifa_code,)).fetchone()
    if zeile is None:
        zeile = conn.execute("SELECT id FROM team WHERE name = ?", (name,)).fetchone()
    if zeile is None:
        cursor = conn.execute(
            "INSERT INTO team (fifa_code, name, gruppe, flagge_url, api_ref) VALUES (?, ?, ?, ?, ?)",
            (fifa_code, name, gruppe, flagge_url, api_ref),
        )
        return cursor.lastrowid
    conn.execute(
        "UPDATE team SET"
        " fifa_code = COALESCE(?, fifa_code),"
        " name = ?,"
        " gruppe = COALESCE(?, gruppe),"
        " flagge_url = COALESCE(?, flagge_url),"
        " api_ref = COALESCE(?, api_ref)"
        " WHERE id = ?",
        (fifa_code, name, gruppe, flagge_url, api_ref, zeile["id"]),
    )
    return zeile["id"]


def trainer_upsert(
    conn: sqlite3.Connection,
    *,
    team_id: int,
    name: str,
    nationalitaet: str | None = None,
    api_ref: str | None = None,
) -> int:
    zeile = conn.execute("SELECT id FROM trainer WHERE team_id = ?", (team_id,)).fetchone()
    if zeile is None:
        cursor = conn.execute(
            "INSERT INTO trainer (team_id, name, nationalitaet, api_ref) VALUES (?, ?, ?, ?)",
            (team_id, name, nationalitaet, api_ref),
        )
        return cursor.lastrowid
    conn.execute(
        "UPDATE trainer SET name = ?, nationalitaet = COALESCE(?, nationalitaet),"
        " api_ref = COALESCE(?, api_ref) WHERE id = ?",
        (name, nationalitaet, api_ref, zeile["id"]),
    )
    return zeile["id"]


def kader_ersetzen(conn: sqlite3.Connection, *, team_id: int, spieler_liste: list[dict]) -> int:
    """Gleicht den Kader eines Teams mit der API-Liste ab (Upsert + Entfernen)."""
    behalten: list[int] = []
    for spieler in spieler_liste:
        zeile = None
        if spieler.get("api_ref") is not None:
            zeile = conn.execute(
                "SELECT id FROM spieler WHERE api_ref = ?", (spieler["api_ref"],)
            ).fetchone()
        if zeile is None:
            zeile = conn.execute(
                "SELECT id FROM spieler WHERE team_id = ? AND name = ?",
                (team_id, spieler["name"]),
            ).fetchone()
        if zeile is None:
            cursor = conn.execute(
                "INSERT INTO spieler (team_id, name, trikotnummer, position, geburtsdatum, api_ref)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    team_id,
                    spieler["name"],
                    spieler.get("trikotnummer"),
                    spieler.get("position"),
                    spieler.get("geburtsdatum"),
                    spieler.get("api_ref"),
                ),
            )
            behalten.append(cursor.lastrowid)
        else:
            conn.execute(
                "UPDATE spieler SET team_id = ?, name = ?,"
                " trikotnummer = COALESCE(?, trikotnummer),"
                " position = COALESCE(?, position),"
                " geburtsdatum = COALESCE(?, geburtsdatum),"
                " api_ref = COALESCE(?, api_ref) WHERE id = ?",
                (
                    team_id,
                    spieler["name"],
                    spieler.get("trikotnummer"),
                    spieler.get("position"),
                    spieler.get("geburtsdatum"),
                    spieler.get("api_ref"),
                    zeile["id"],
                ),
            )
            behalten.append(zeile["id"])
    if behalten:
        platzhalter = ",".join("?" for _ in behalten)
        conn.execute(
            f"DELETE FROM spieler WHERE team_id = ? AND id NOT IN ({platzhalter})",
            (team_id, *behalten),
        )
    else:
        conn.execute("DELETE FROM spieler WHERE team_id = ?", (team_id,))
    return len(behalten)


def team_gruppe_setzen(conn: sqlite3.Connection, team_id: int | None, gruppe: str) -> None:
    if team_id is None:
        return
    conn.execute(
        "UPDATE team SET gruppe = ? WHERE id = ? AND (gruppe IS NULL OR gruppe <> ?)",
        (gruppe, team_id, gruppe),
    )


def spielort_upsert(
    conn: sqlite3.Connection,
    *,
    stadion_name: str,
    stadt: str | None = None,
    land: str | None = None,
    kapazitaet: int | None = None,
    zeitzone: str | None = None,
    api_ref: str | None = None,
) -> int:
    zeile = conn.execute(
        "SELECT id FROM spielort WHERE stadion_name = ?", (stadion_name,)
    ).fetchone()
    if zeile is None:
        cursor = conn.execute(
            "INSERT INTO spielort (stadion_name, stadt, land, kapazitaet, zeitzone, api_ref)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (stadion_name, stadt, land, kapazitaet, zeitzone, api_ref),
        )
        return cursor.lastrowid
    conn.execute(
        "UPDATE spielort SET"
        " stadt = COALESCE(?, stadt),"
        " land = COALESCE(?, land),"
        " kapazitaet = COALESCE(?, kapazitaet),"
        " zeitzone = COALESCE(?, zeitzone),"
        " api_ref = COALESCE(?, api_ref)"
        " WHERE id = ?",
        (stadt, land, kapazitaet, zeitzone, api_ref, zeile["id"]),
    )
    return zeile["id"]


def spiel_upsert(
    conn: sqlite3.Connection,
    *,
    runde: str,
    anstoss_utc: str,
    heim_team_id: int | None,
    gast_team_id: int | None,
    spielort_id: int | None,
    status: str,
    tore_heim: int | None,
    tore_gast: int | None,
    ergebnis_nach: str | None,
    elfmeter_sieger_team_id: int | None,
    api_ref: str | None,
    quelle: str,
    akteur: str,
) -> SpielUpsertErgebnis:
    """Legt ein Spiel an oder aktualisiert es (Schlüssel: api_ref, sonst Paarung+Runde)."""
    zeile = None
    if api_ref is not None:
        zeile = conn.execute("SELECT * FROM spiel WHERE api_ref = ?", (api_ref,)).fetchone()
    if zeile is None and heim_team_id is not None and gast_team_id is not None:
        zeile = conn.execute(
            "SELECT * FROM spiel WHERE runde = ? AND heim_team_id = ? AND gast_team_id = ?",
            (runde, heim_team_id, gast_team_id),
        ).fetchone()

    if zeile is None:
        cursor = conn.execute(
            "INSERT INTO spiel (runde, anstoss_utc, spielort_id, heim_team_id, gast_team_id,"
            " status, tore_heim, tore_gast, ergebnis_nach, elfmeter_sieger_team_id, api_ref)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                runde,
                anstoss_utc,
                spielort_id,
                heim_team_id,
                gast_team_id,
                status,
                tore_heim,
                tore_gast,
                ergebnis_nach,
                elfmeter_sieger_team_id,
                api_ref,
            ),
        )
        return SpielUpsertErgebnis(
            spiel_id=cursor.lastrowid, neu=True, neu_beendet=(status == "beendet")
        )

    neue_werte = {
        "runde": runde,
        "anstoss_utc": anstoss_utc,
        "spielort_id": spielort_id if spielort_id is not None else zeile["spielort_id"],
        "heim_team_id": heim_team_id if heim_team_id is not None else zeile["heim_team_id"],
        "gast_team_id": gast_team_id if gast_team_id is not None else zeile["gast_team_id"],
        "status": status,
        "tore_heim": tore_heim,
        "tore_gast": tore_gast,
        "ergebnis_nach": ergebnis_nach,
        "elfmeter_sieger_team_id": elfmeter_sieger_team_id,
        "api_ref": api_ref if api_ref is not None else zeile["api_ref"],
    }
    if quelle == "api":
        # Felder mit aktivem Admin-Override behalten den manuellen Wert
        # (Priorität admin > api, SPEC 3.5) — Import nur hier, Zyklus vermeiden.
        from . import overrides

        for feld in overrides.aktive_felder(conn, "spiel", zeile["id"]):
            if feld in neue_werte:
                neue_werte[feld] = zeile[feld]
    geaendert = any(neue_werte[feld] != zeile[feld] for feld in neue_werte)
    if not geaendert:
        return SpielUpsertErgebnis(spiel_id=zeile["id"])

    jetzt = jetzt_iso()
    deltas: dict[str, tuple] = {}
    for feld in _SPIEL_LOG_FELDER:
        if neue_werte[feld] != zeile[feld]:
            deltas[feld] = (zeile[feld], neue_werte[feld])
            db.change_log_eintrag(
                conn,
                entitaet="spiel",
                entitaet_id=zeile["id"],
                feld=feld,
                alt_wert=zeile[feld],
                neu_wert=neue_werte[feld],
                quelle=quelle,
                akteur=akteur,
                zeitpunkt_utc=jetzt,
            )
    conn.execute(
        "UPDATE spiel SET runde = ?, anstoss_utc = ?, spielort_id = ?, heim_team_id = ?,"
        " gast_team_id = ?, status = ?, tore_heim = ?, tore_gast = ?, ergebnis_nach = ?,"
        " elfmeter_sieger_team_id = ?, api_ref = ? WHERE id = ?",
        (*neue_werte.values(), zeile["id"]),
    )
    return SpielUpsertErgebnis(
        spiel_id=zeile["id"],
        geaendert=True,
        neu_beendet=(status == "beendet" and zeile["status"] != "beendet"),
        deltas=deltas or None,
    )
