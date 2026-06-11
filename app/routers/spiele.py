"""Spielplan-Ansichten: Liste fürs Hauptmenü und Spiel-Detail (SPEC 5.1, 5.2-Teilmenge)."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ..abhaengigkeiten import aktueller_nutzer, get_db, get_einstellungen, ki_sichtbar
from ..config import Einstellungen
from ..services import agenten, turnier
from ..zeit import jetzt_iso

router = APIRouter(prefix="/api", tags=["spiele"])

_SPIEL_SELECT = """
SELECT s.id, s.runde, s.anstoss_utc, s.status, s.tore_heim, s.tore_gast,
       s.ergebnis_nach, s.elfmeter_sieger_team_id,
       th.id AS heim_id, th.name AS heim_name, th.fifa_code AS heim_code,
       th.flagge_url AS heim_flagge,
       tg.id AS gast_id, tg.name AS gast_name, tg.fifa_code AS gast_code,
       tg.flagge_url AS gast_flagge,
       o.stadion_name, o.stadt,
       mt.tipp_heim AS mein_tipp_heim, mt.tipp_gast AS mein_tipp_gast,
       mt.punkte AS mein_tipp_punkte
FROM spiel s
LEFT JOIN team th ON th.id = s.heim_team_id
LEFT JOIN team tg ON tg.id = s.gast_team_id
LEFT JOIN spielort o ON o.id = s.spielort_id
LEFT JOIN tipp mt ON mt.spiel_id = s.id AND mt.nutzer_id = ?
"""


def _spiel_json(zeile: sqlite3.Row) -> dict[str, Any]:
    mein_tipp = None
    if zeile["mein_tipp_heim"] is not None:
        mein_tipp = {
            "tipp_heim": zeile["mein_tipp_heim"],
            "tipp_gast": zeile["mein_tipp_gast"],
            "punkte": zeile["mein_tipp_punkte"],
        }
    return {
        "id": zeile["id"],
        "runde": zeile["runde"],
        "anstoss_utc": zeile["anstoss_utc"],
        "status": zeile["status"],
        "tore_heim": zeile["tore_heim"],
        "tore_gast": zeile["tore_gast"],
        "ergebnis_nach": zeile["ergebnis_nach"],
        "heim": _team_json(zeile, "heim"),
        "gast": _team_json(zeile, "gast"),
        "stadion": zeile["stadion_name"],
        "stadt": zeile["stadt"],
        "tippbar": zeile["status"] == "geplant" and zeile["anstoss_utc"] > jetzt_iso(),
        "mein_tipp": mein_tipp,
    }


def _team_json(zeile: sqlite3.Row, seite: str) -> dict[str, Any] | None:
    if zeile[f"{seite}_id"] is None:
        return None
    return {
        "id": zeile[f"{seite}_id"],
        "name": zeile[f"{seite}_name"],
        "fifa_code": zeile[f"{seite}_code"],
        "flagge_url": zeile[f"{seite}_flagge"],
    }


@router.get("/spiele")
def spiele_liste(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    gruppe: str | None = None,
    team_id: int | None = None,
) -> list[dict[str, Any]]:
    sql = _SPIEL_SELECT
    parameter: list[Any] = [nutzer["id"]]
    bedingungen = []
    if gruppe:
        bedingungen.append("s.runde = ?")
        parameter.append(f"Gruppe {gruppe}" if len(gruppe) == 1 else gruppe)
    if team_id is not None:
        bedingungen.append("(s.heim_team_id = ? OR s.gast_team_id = ?)")
        parameter.extend([team_id, team_id])
    if bedingungen:
        sql += " WHERE " + " AND ".join(bedingungen)
    sql += " ORDER BY s.anstoss_utc, s.id"
    zeilen = conn.execute(sql, parameter).fetchall()
    pins = conn.execute(
        "SELECT typ, ref_id FROM pin WHERE nutzer_id = ?", (nutzer["id"],)
    ).fetchall()
    spiel_pins = {p["ref_id"] for p in pins if p["typ"] == "spiel"}
    team_pins = {p["ref_id"] for p in pins if p["typ"] == "team"}
    notiz_spiele = {
        zeile["spiel_id"]
        for zeile in conn.execute(
            "SELECT spiel_id FROM notiz WHERE nutzer_id = ?", (nutzer["id"],)
        ).fetchall()
    }
    spiele = []
    for zeile in zeilen:
        spiel = _spiel_json(zeile)
        spiel["gepinnt"] = zeile["id"] in spiel_pins
        spiel["team_gepinnt"] = zeile["heim_id"] in team_pins or zeile["gast_id"] in team_pins
        spiel["hat_notiz"] = zeile["id"] in notiz_spiele
        spiele.append(spiel)
    return spiele


@router.get("/spiele/{spiel_id}")
def spiel_detail(
    spiel_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    zeile = conn.execute(_SPIEL_SELECT + " WHERE s.id = ?", (nutzer["id"], spiel_id)).fetchone()
    if zeile is None:
        raise HTTPException(status_code=404, detail="Spiel nicht gefunden")
    spiel = _spiel_json(zeile)
    # Sichtbarkeitsregeln (SPEC 5.2/5.4): fremde Tipps erst ab Anpfiff; der
    # KI-Tipp erscheint ausschließlich für KI-freigeschaltete Profile.
    vor_anpfiff = zeile["anstoss_utc"] > jetzt_iso()
    zeige_ki = ki_sichtbar(nutzer)
    sql = (
        "SELECT t.tipp_heim, t.tipp_gast, t.punkte, n.id AS nutzer_id,"
        " n.anzeigename, n.rolle, n.rangliste_sichtbar"
        " FROM tipp t JOIN nutzer n ON n.id = t.nutzer_id WHERE t.spiel_id = ?"
    )
    parameter: list[Any] = [spiel_id]
    if vor_anpfiff:
        sql += " AND (n.id = ? OR n.rolle = 'ki')" if zeige_ki else " AND n.id = ?"
        parameter.append(nutzer["id"])
    elif not zeige_ki:
        sql += " AND n.rolle != 'ki'"
    sql += " ORDER BY n.anzeigename COLLATE NOCASE"
    spiel["tipps"] = [dict(tipp) for tipp in conn.execute(sql, parameter).fetchall()]
    # Private Notiz des angemeldeten Nutzers zu diesem Spiel (v0.1.1)
    notiz = conn.execute(
        "SELECT text, erstellt_utc, geaendert_utc FROM notiz"
        " WHERE nutzer_id = ? AND spiel_id = ?",
        (nutzer["id"], spiel_id),
    ).fetchone()
    spiel["notiz"] = dict(notiz) if notiz else None
    spiel["trainer"] = {
        "heim": _trainer_name(conn, zeile["heim_id"]),
        "gast": _trainer_name(conn, zeile["gast_id"]),
    }
    spiel["vergleiche"] = [
        dict(duell)
        for duell in conn.execute(
            "SELECT datum_utc, wettbewerb, heim_name, gast_name, tore_heim, tore_gast"
            " FROM direktvergleich WHERE spiel_id = ? ORDER BY datum_utc DESC",
            (spiel_id,),
        ).fetchall()
    ]
    bilanz = conn.execute(
        "SELECT anzahl, heim_siege, gast_siege, remis, tore FROM duell_bilanz WHERE spiel_id = ?",
        (spiel_id,),
    ).fetchone()
    spiel["ereignisse"] = [
        dict(eintrag)
        for eintrag in conn.execute(
            "SELECT e.id, e.minute, e.typ, e.text, e.quelle, e.erstellt_utc,"
            " e.team_id, t.name AS team_name,"
            " sp.name AS spieler_name, sp2.name AS spieler2_name"
            " FROM ereignis e"
            " LEFT JOIN team t ON t.id = e.team_id"
            " LEFT JOIN spieler sp ON sp.id = e.spieler_id"
            " LEFT JOIN spieler sp2 ON sp2.id = e.spieler2_id"
            " WHERE e.spiel_id = ? ORDER BY e.erstellt_utc DESC, e.id DESC",
            (spiel_id,),
        ).fetchall()
    ]
    spiel["bilanz"] = dict(bilanz) if bilanz else None
    spiel["tabelle"] = None
    if spiel["runde"].startswith("Gruppe "):
        gruppe = spiel["runde"].removeprefix("Gruppe ")
        tabelle = conn.execute(
            "SELECT g.platz, g.spiele, g.tordifferenz, g.punkte,"
            " t.id AS team_id, t.name, t.fifa_code"
            " FROM gruppen_tabelle g JOIN team t ON t.id = g.team_id"
            " WHERE g.gruppe = ? ORDER BY g.platz",
            (gruppe,),
        ).fetchall()
        if tabelle:
            spiel["tabelle"] = [dict(z) for z in tabelle]
    # Tipp-Verteilung nach Tendenz (Heim/Remis/Gast).
    # Aggregiert über alle Tipps — verrät keine Einzeltipps, daher auch vor
    # Anpfiff unbedenklich; der KI-Tipp zählt nur für freigeschaltete Profile.
    verteilung = {"heim": 0, "remis": 0, "gast": 0}
    vert_sql = (
        "SELECT CASE WHEN t.tipp_heim > t.tipp_gast THEN 'heim'"
        " WHEN t.tipp_heim < t.tipp_gast THEN 'gast' ELSE 'remis' END AS tendenz,"
        " COUNT(*) AS anzahl FROM tipp t JOIN nutzer n ON n.id = t.nutzer_id"
        " WHERE t.spiel_id = ?"
    )
    if not zeige_ki:
        vert_sql += " AND n.rolle != 'ki'"
    vert_sql += " GROUP BY tendenz"
    for vert_zeile in conn.execute(vert_sql, (spiel_id,)).fetchall():
        verteilung[vert_zeile["tendenz"]] = vert_zeile["anzahl"]
    spiel["tipp_verteilung"] = {**verteilung, "gesamt": sum(verteilung.values())}
    # Formkette der letzten 5 Turnierspiele je Team
    spiel["form"] = {
        "heim": _formkette(conn, zeile["heim_id"]),
        "gast": _formkette(conn, zeile["gast_id"]),
    }
    # Neueste KI-Prognose/Nachanalyse — nur für freigeschaltete Nutzer
    # (Admin und KI-Tipper immer; Mitglieder nach Freischaltung durch den Admin).
    spiel["analysen"] = agenten.neueste_analysen(conn, spiel_id) if zeige_ki else {}
    return spiel


def _formkette(conn: sqlite3.Connection, team_id: int | None) -> list[str]:
    """Letzte 5 beendete Turnierspiele eines Teams als S/U/N, neueste zuerst."""
    if team_id is None:
        return []
    zeilen = conn.execute(
        "SELECT heim_team_id, tore_heim, tore_gast FROM spiel"
        " WHERE status = 'beendet' AND tore_heim IS NOT NULL AND tore_gast IS NOT NULL"
        " AND (heim_team_id = ? OR gast_team_id = ?)"
        " ORDER BY anstoss_utc DESC LIMIT 5",
        (team_id, team_id),
    ).fetchall()
    kette = []
    for zeile in zeilen:
        ist_heim = zeile["heim_team_id"] == team_id
        eigene = zeile["tore_heim"] if ist_heim else zeile["tore_gast"]
        fremde = zeile["tore_gast"] if ist_heim else zeile["tore_heim"]
        kette.append("S" if eigene > fremde else "N" if eigene < fremde else "U")
    return kette


def _trainer_name(conn: sqlite3.Connection, team_id: int | None) -> str | None:
    if team_id is None:
        return None
    zeile = conn.execute("SELECT name FROM trainer WHERE team_id = ?", (team_id,)).fetchone()
    return zeile["name"] if zeile else None


@router.get("/wettbewerbe")
def wettbewerbe_liste(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> list[dict[str, Any]]:
    """Verfügbare Wettbewerbe/Saisons fürs Auswahlmenü.

    Aktiv ist der konfigurierte Wettbewerb (WM26_API_WETTBEWERB); die Bundesliga
    2026/27 ist als nächste Saison vorbereitet und wird nach dem Turnier über
    dieselbe Konfiguration freigeschaltet (Layouts/Logik sind wettbewerbsneutral).
    """
    return [
        {
            "code": "WC",
            "name": "WM",
            "saison": "2026",
            "aktiv": einstellungen.api_wettbewerb == "WC",
            "beschreibung": "USA · Mexiko · Kanada — 48 Teams",
            "hinweis": None,
            "hinweis_lang": None,
        },
        {
            "code": "BL1",
            "name": "Bundesliga",
            "saison": "2026/27",
            "aktiv": einstellungen.api_wettbewerb == "BL1",
            "beschreibung": "Deutschland — 18 Teams",
            "hinweis": "ab August",
            "hinweis_lang": "Die Bundesliga 2026/27 ist vorbereitet und wird nach der WM freigeschaltet.",
        },
    ]


@router.get("/tabellen")
def gruppen_tabellen(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, list[dict[str, Any]]]:
    """Offizielle Gruppentabellen, gruppiert nach Gruppenbuchstabe."""
    zeilen = conn.execute(
        "SELECT g.gruppe, g.platz, g.spiele, g.siege, g.remis, g.niederlagen,"
        " g.tore, g.gegentore, g.tordifferenz, g.punkte,"
        " t.id AS team_id, t.name, t.fifa_code, t.flagge_url"
        " FROM gruppen_tabelle g JOIN team t ON t.id = g.team_id"
        " ORDER BY g.gruppe, g.platz"
    ).fetchall()
    tabellen: dict[str, list[dict[str, Any]]] = {}
    for zeile in zeilen:
        tabellen.setdefault(zeile["gruppe"], []).append(dict(zeile))
    return tabellen


@router.get("/torschuetzen")
def torschuetzen_liste(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT ts.name, ts.spiele, ts.tore, ts.vorlagen, ts.elfmeter,"
        " t.id AS team_id, t.name AS team_name, t.fifa_code, t.flagge_url"
        " FROM torschuetze ts LEFT JOIN team t ON t.id = ts.team_id"
        " ORDER BY ts.tore DESC, ts.vorlagen DESC, ts.name COLLATE NOCASE"
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]


@router.get("/teams")
def teams_liste(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT id, fifa_code, name, gruppe, flagge_url FROM team ORDER BY name COLLATE NOCASE"
    ).fetchall()
    raus = turnier.ausgeschiedene_teams(conn)
    return [{**dict(zeile), "ausgeschieden": zeile["id"] in raus} for zeile in zeilen]


@router.get("/spieler")
def spieler_suche(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    suche: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Spieler-Suche (z. B. Antwort auf die Torschützenkönig-Bonusfrage)."""
    sql = (
        "SELECT s.id, s.name, s.position, s.trikotnummer, t.name AS team_name,"
        " t.fifa_code FROM spieler s JOIN team t ON t.id = s.team_id"
    )
    parameter: list[Any] = []
    if suche.strip():
        sql += " WHERE s.name LIKE ? ESCAPE '\\'"
        muster = suche.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        parameter.append(f"%{muster}%")
    sql += " ORDER BY s.name COLLATE NOCASE LIMIT ?"
    parameter.append(max(1, min(limit, 50)))
    return [dict(zeile) for zeile in conn.execute(sql, parameter).fetchall()]


@router.get("/spieler/{spieler_id}")
def spieler_detail(
    spieler_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    """Spieler-Lupe (SPEC 5.3): Stammdaten + Turnierstatistik aus den Ereignissen."""
    spieler = conn.execute(
        "SELECT s.id, s.name, s.position, s.trikotnummer, s.geburtsdatum, s.verein,"
        " t.id AS team_id, t.name AS team_name, t.fifa_code, t.flagge_url"
        " FROM spieler s JOIN team t ON t.id = s.team_id WHERE s.id = ?",
        (spieler_id,),
    ).fetchone()
    if spieler is None:
        raise HTTPException(status_code=404, detail="Spieler nicht gefunden")
    statistik = conn.execute(
        "SELECT"
        " SUM(CASE WHEN typ IN ('tor', 'elfmeter') THEN 1 ELSE 0 END) AS tore,"
        " SUM(CASE WHEN typ = 'eigentor' THEN 1 ELSE 0 END) AS eigentore,"
        " SUM(CASE WHEN typ = 'gelb' THEN 1 ELSE 0 END) AS gelbe_karten,"
        " SUM(CASE WHEN typ IN ('gelbrot', 'rot') THEN 1 ELSE 0 END) AS platzverweise"
        " FROM ereignis WHERE spieler_id = ?",
        (spieler_id,),
    ).fetchone()
    torschuetze = conn.execute(
        "SELECT tore, vorlagen, spiele FROM torschuetze WHERE name = ?",
        (spieler["name"],),
    ).fetchone()
    return {
        **dict(spieler),
        "statistik": {
            # Explizit gegen None prüfen: 0 Tore aus der Torschützenliste sind gültig
            "tore": torschuetze["tore"]
            if torschuetze and torschuetze["tore"] is not None
            else (statistik["tore"] or 0),
            "vorlagen": torschuetze["vorlagen"] if torschuetze else None,
            "spiele": torschuetze["spiele"] if torschuetze else None,
            "eigentore": statistik["eigentore"] or 0,
            "gelbe_karten": statistik["gelbe_karten"] or 0,
            "platzverweise": statistik["platzverweise"] or 0,
        },
    }


@router.get("/teams/{team_id}")
def team_detail(
    team_id: int,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    """Teamprofil mit Trainer, Kader, Taktik und Verletzungsliste."""
    team = conn.execute(
        "SELECT id, fifa_code, name, gruppe, flagge_url FROM team WHERE id = ?", (team_id,)
    ).fetchone()
    if team is None:
        raise HTTPException(status_code=404, detail="Team nicht gefunden")
    trainer = conn.execute(
        "SELECT name, nationalitaet FROM trainer WHERE team_id = ?", (team_id,)
    ).fetchone()
    kader = conn.execute(
        "SELECT id, name, trikotnummer, position, geburtsdatum FROM spieler WHERE team_id = ?"
        " ORDER BY CASE position WHEN 'Torwart' THEN 1 WHEN 'Abwehr' THEN 2"
        " WHEN 'Mittelfeld' THEN 3 WHEN 'Sturm' THEN 4 ELSE 5 END, name COLLATE NOCASE",
        (team_id,),
    ).fetchall()
    taktik = conn.execute(
        "SELECT formation, beschreibung, staerken, schwaechen, quelle, stand_utc"
        " FROM taktik WHERE team_id = ?",
        (team_id,),
    ).fetchone()
    verletzungen = conn.execute(
        "SELECT v.id, sp.name AS spieler_name, v.beschreibung, v.status, v.quelle, v.geprueft"
        " FROM verletzung v JOIN spieler sp ON sp.id = v.spieler_id"
        " WHERE sp.team_id = ? AND v.status != 'wieder fit'"
        " ORDER BY v.gemeldet_utc DESC",
        (team_id,),
    ).fetchall()
    return {
        **dict(team),
        "ausgeschieden": team["id"] in turnier.ausgeschiedene_teams(conn),
        "trainer": dict(trainer) if trainer else None,
        "kader": [dict(spieler) for spieler in kader],
        "taktik": dict(taktik) if taktik else None,
        "verletzungen": [dict(zeile) for zeile in verletzungen],
    }
