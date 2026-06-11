"""REST-Schnittstelle für Claude-Agenten (SPEC 7.2), Bearer-Token-geschützt.

Lese-Endpunkte (Scope read): Exporte für Prognose-Sessions — Spielplan mit
Ereignissen, Teams mit Kader/Taktik/Verletzungen, pseudonymisierte Tipps,
News, bisherige Analysen. Schreib-Endpunkte (Scope write_analysis): Analysen,
KI-Tipp, Sichtungs-Vorschläge. Niemals exportiert: Namen, Hashes, Tokens.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..abhaengigkeiten import get_db
from ..services import agenten, tippspiel
from ..zeit import jetzt_iso

router = APIRouter(prefix="/api", tags=["agenten"])


def _token_aus_header(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer-Token erforderlich")
    return authorization.removeprefix("Bearer ").strip()


def _agent(conn: sqlite3.Connection, authorization: str | None, scope: str) -> sqlite3.Row:
    try:
        return agenten.token_pruefen(conn, _token_aus_header(authorization), scope=scope)
    except agenten.AgentFehler as fehler:
        status = 429 if "Rate-Limit" in str(fehler) else 403
        raise HTTPException(status_code=status, detail=str(fehler)) from None


def agent_read(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> sqlite3.Row:
    return _agent(conn, authorization, "read")


def agent_write(
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> sqlite3.Row:
    return _agent(conn, authorization, "write_analysis")


# ---------- Exporte (Scope read) ----------


@router.get("/export/spiele")
def export_spiele(
    agent: Annotated[sqlite3.Row, Depends(agent_read)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    spiel_id: int | None = None,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT s.id, s.runde, s.anstoss_utc, s.status, s.tore_heim, s.tore_gast,"
        " s.ergebnis_nach, th.name AS heim, tg.name AS gast, o.stadion_name, o.stadt"
        " FROM spiel s LEFT JOIN team th ON th.id = s.heim_team_id"
        " LEFT JOIN team tg ON tg.id = s.gast_team_id"
        " LEFT JOIN spielort o ON o.id = s.spielort_id"
    )
    parameter: list[Any] = []
    if spiel_id is not None:
        sql += " WHERE s.id = ?"
        parameter.append(spiel_id)
    sql += " ORDER BY s.anstoss_utc, s.id"
    spiele = [dict(zeile) for zeile in conn.execute(sql, parameter).fetchall()]
    for spiel in spiele:
        spiel["ereignisse"] = [
            dict(e)
            for e in conn.execute(
                "SELECT e.minute, e.typ, e.text, t.name AS team, sp.name AS spieler"
                " FROM ereignis e LEFT JOIN team t ON t.id = e.team_id"
                " LEFT JOIN spieler sp ON sp.id = e.spieler_id"
                " WHERE e.spiel_id = ? ORDER BY e.id",
                (spiel["id"],),
            ).fetchall()
        ]
        bilanz = conn.execute(
            "SELECT anzahl, heim_siege, gast_siege, remis, tore FROM duell_bilanz"
            " WHERE spiel_id = ?",
            (spiel["id"],),
        ).fetchone()
        spiel["duell_bilanz"] = dict(bilanz) if bilanz else None
    return spiele


@router.get("/export/teams")
def export_teams(
    agent: Annotated[sqlite3.Row, Depends(agent_read)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    teams = [
        dict(zeile)
        for zeile in conn.execute(
            "SELECT id, name, fifa_code, gruppe FROM team ORDER BY name COLLATE NOCASE"
        ).fetchall()
    ]
    for team in teams:
        team["kader"] = [
            dict(s)
            for s in conn.execute(
                "SELECT name, position, trikotnummer FROM spieler WHERE team_id = ?",
                (team["id"],),
            ).fetchall()
        ]
        taktik = conn.execute(
            "SELECT formation, beschreibung, staerken, schwaechen, quelle, stand_utc"
            " FROM taktik WHERE team_id = ?",
            (team["id"],),
        ).fetchone()
        team["taktik"] = dict(taktik) if taktik else None
        team["verletzungen"] = [
            dict(v)
            for v in conn.execute(
                "SELECT sp.name AS spieler, v.beschreibung, v.status, v.geprueft"
                " FROM verletzung v JOIN spieler sp ON sp.id = v.spieler_id"
                " WHERE sp.team_id = ? AND v.status != 'wieder fit'",
                (team["id"],),
            ).fetchall()
        ]
        tabelle = conn.execute(
            "SELECT platz, spiele, punkte, tordifferenz FROM gruppen_tabelle WHERE team_id = ?",
            (team["id"],),
        ).fetchone()
        team["tabellenstand"] = dict(tabelle) if tabelle else None
    return teams


@router.get("/export/tipps")
def export_tipps(
    agent: Annotated[sqlite3.Row, Depends(agent_read)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    """Pseudonymisiert (SPEC 8.3): keine Anzeigenamen, stabile 'Spieler n'-Kürzel."""
    namen = agenten.pseudonyme(conn)
    zeilen = conn.execute(
        "SELECT t.nutzer_id, t.spiel_id, t.tipp_heim, t.tipp_gast, t.punkte"
        " FROM tipp t ORDER BY t.spiel_id, t.nutzer_id"
    ).fetchall()
    return [
        {
            "tipper": namen.get(zeile["nutzer_id"], "?"),
            "spiel_id": zeile["spiel_id"],
            "tipp_heim": zeile["tipp_heim"],
            "tipp_gast": zeile["tipp_gast"],
            "punkte": zeile["punkte"],
        }
        for zeile in zeilen
    ]


@router.get("/export/news")
def export_news(
    agent: Annotated[sqlite3.Row, Depends(agent_read)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    limit: int = 100,
) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT n.titel, n.link, n.zusammenfassung, n.veroeffentlicht_utc, t.name AS team"
        " FROM news_item n LEFT JOIN team t ON t.id = n.team_id"
        " ORDER BY n.veroeffentlicht_utc DESC, n.id DESC LIMIT ?",
        (max(1, min(limit, 500)),),
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]


@router.get("/export/analysen")
def export_analysen(
    agent: Annotated[sqlite3.Row, Depends(agent_read)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    """Bisherige Analysen inkl. Trefferbilanz der zugehörigen KI-Tipps."""
    zeilen = conn.execute(
        "SELECT a.spiel_id, a.typ, a.inhalt_markdown, a.struktur_json, a.agent_name,"
        " a.erstellt_utc, a.version, s.tore_heim, s.tore_gast, s.status"
        " FROM ki_analyse a JOIN spiel s ON s.id = a.spiel_id"
        " ORDER BY a.spiel_id, a.typ, a.version"
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]


# ---------- Schreiben (Scope write_analysis) ----------


class AnalyseEingabe(BaseModel):
    spiel_id: int
    typ: str = Field(pattern="^(prognose|nachanalyse)$")
    inhalt_markdown: str = Field(min_length=1, max_length=20000)
    struktur_json: dict | None = None


class KiTippEingabe(BaseModel):
    spiel_id: int
    tipp_heim: int = Field(ge=0, le=99)
    tipp_gast: int = Field(ge=0, le=99)


class BeitragEingabe(BaseModel):
    typ: str = Field(pattern="^(taktik|verletzung)$")
    team_id: int | None = None
    spieler_id: int | None = None
    inhalt: dict


@router.post("/analysen", status_code=201)
def analyse_anlegen(
    daten: AnalyseEingabe,
    agent: Annotated[sqlite3.Row, Depends(agent_write)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return agenten.analyse_anlegen(
            conn,
            spiel_id=daten.spiel_id,
            typ=daten.typ,
            inhalt_markdown=daten.inhalt_markdown,
            struktur_json=json.dumps(daten.struktur_json, ensure_ascii=False)
            if daten.struktur_json is not None
            else None,
            agent_name=agent["name"],
        )
    except agenten.AgentFehler as fehler:
        raise HTTPException(status_code=404, detail=str(fehler)) from None


@router.post("/tipps/ki")
def ki_tipp_setzen(
    daten: KiTippEingabe,
    agent: Annotated[sqlite3.Row, Depends(agent_write)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    """Setzt den Tipp des KI-Tippers (nur vor Anpfiff, wie bei allen)."""
    try:
        ki = agenten.ki_nutzer(conn)
        return tippspiel.tipp_abgeben(
            conn,
            nutzer_id=ki["id"],
            spiel_id=daten.spiel_id,
            tipp_heim=daten.tipp_heim,
            tipp_gast=daten.tipp_gast,
        )
    except agenten.AgentFehler as fehler:
        raise HTTPException(status_code=409, detail=str(fehler)) from None
    except tippspiel.SpielNichtGefunden:
        raise HTTPException(status_code=404, detail="Spiel nicht gefunden") from None
    except tippspiel.TippGesperrt as fehler:
        raise HTTPException(status_code=409, detail=str(fehler)) from None


@router.post("/beitraege", status_code=201)
def beitrag_anlegen(
    daten: BeitragEingabe,
    agent: Annotated[sqlite3.Row, Depends(agent_write)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    """Vorschlag für Taktik/Verletzung — landet zur Sichtung beim Admin."""
    with db.schreib_transaktion(conn):
        cursor = conn.execute(
            "INSERT INTO beitrag (typ, team_id, spieler_id, inhalt_json, agent_name,"
            " erstellt_utc) VALUES (?, ?, ?, ?, ?, ?)",
            (
                daten.typ,
                daten.team_id,
                daten.spieler_id,
                json.dumps(daten.inhalt, ensure_ascii=False),
                agent["name"],
                jetzt_iso(),
            ),
        )
    return {"id": cursor.lastrowid, "status": "offen"}
