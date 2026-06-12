"""Admin-Funktionen: Nutzer anlegen, Spielplan-Import, Ergebnis-Notfalleingabe,
manueller Sync und Sync-Status (SPEC 4.4, 5.7-Teilmenge)."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..abhaengigkeiten import admin_nutzer, get_db, get_einstellungen
from ..config import Einstellungen
from ..modelle import PIN_MIN_NEU, EreignisEingabe, ErgebnisEingabe, NeuerNutzer, NutzerInfo
from pydantic import BaseModel, Field

from ..services import (
    agenten,
    bonus,
    importer,
    live,
    news,
    nutzer as nutzer_service,
    overrides,
    profilbilder,
    sync,
    tippspiel,
)
from ..security import pin_hashen
from ..services.fussball_api import FussballApiFehler
from ..zeit import jetzt_iso

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/nutzer", response_model=NutzerInfo, status_code=201)
def nutzer_anlegen(
    daten: NeuerNutzer,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> NutzerInfo:
    try:
        nutzer_id = nutzer_service.nutzer_anlegen(
            conn,
            anzeigename=daten.anzeigename,
            pin=daten.pin,
            rolle=daten.rolle,
            akteur=admin["anzeigename"],
        )
    except ValueError as fehler:
        raise HTTPException(status_code=409, detail=str(fehler)) from None
    return NutzerInfo(id=nutzer_id, anzeigename=daten.anzeigename.strip(), rolle=daten.rolle)


@router.get("/nutzer")
def nutzer_liste(
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT id, anzeigename, rolle, ki_freigeschaltet, rangliste_sichtbar,"
        " profilbild, erstellt_utc"
        " FROM nutzer ORDER BY anzeigename COLLATE NOCASE"
    ).fetchall()
    # bool statt SQLite-0/1, konsistent mit /api/me und /api/login
    return [
        {
            **dict(zeile),
            "ki_freigeschaltet": bool(zeile["ki_freigeschaltet"]),
            "rangliste_sichtbar": bool(zeile["rangliste_sichtbar"]),
        }
        for zeile in zeilen
    ]


class NutzerAenderung(BaseModel):
    ki_freigeschaltet: bool | None = None
    rangliste_sichtbar: bool | None = None
    pin: str | None = Field(default=None, min_length=PIN_MIN_NEU, max_length=32)
    # Rolle umhängen (v0.2): nur admin/mitglied — KI-Konten entstehen
    # ausschließlich über die Nutzeranlage und bleiben KI.
    rolle: str | None = Field(default=None, pattern="^(admin|mitglied)$")


@router.patch("/nutzer/{nutzer_id}")
def nutzer_aendern(
    nutzer_id: int,
    daten: NutzerAenderung,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    """KI-Wertung/Sichtbarkeit/Rolle umschalten und/oder PIN zurücksetzen."""
    if daten.pin is not None:
        try:
            nutzer_service.pin_validieren(daten.pin)
        except ValueError as fehler:
            raise HTTPException(status_code=422, detail=str(fehler)) from None
    jetzt = jetzt_iso()
    with db.schreib_transaktion(conn):
        ziel = conn.execute(
            "SELECT id, anzeigename, rolle, ki_freigeschaltet, rangliste_sichtbar"
            " FROM nutzer WHERE id = ?",
            (nutzer_id,),
        ).fetchone()
        if ziel is None:
            raise HTTPException(status_code=404, detail="Nutzer nicht gefunden")
        if (
            daten.rangliste_sichtbar is not None
            and bool(ziel["rangliste_sichtbar"]) != daten.rangliste_sichtbar
        ):
            conn.execute(
                "UPDATE nutzer SET rangliste_sichtbar = ? WHERE id = ?",
                (1 if daten.rangliste_sichtbar else 0, nutzer_id),
            )
            db.change_log_eintrag(
                conn,
                entitaet="nutzer",
                entitaet_id=nutzer_id,
                feld="rangliste_sichtbar",
                alt_wert=ziel["rangliste_sichtbar"],
                neu_wert=1 if daten.rangliste_sichtbar else 0,
                quelle="admin",
                akteur=admin["anzeigename"],
                zeitpunkt_utc=jetzt,
            )
        if daten.rolle is not None and ziel["rolle"] != daten.rolle:
            if ziel["rolle"] == "ki":
                raise HTTPException(status_code=409, detail="KI-Konten behalten ihre Rolle.")
            if ziel["rolle"] == "admin":
                admins = conn.execute(
                    "SELECT COUNT(*) AS anzahl FROM nutzer WHERE rolle = 'admin'"
                ).fetchone()["anzahl"]
                if admins <= 1:
                    raise HTTPException(
                        status_code=409,
                        detail="Der letzte Admin kann nicht zurückgestuft werden.",
                    )
            conn.execute("UPDATE nutzer SET rolle = ? WHERE id = ?", (daten.rolle, nutzer_id))
            # Rollenwechsel beendet bestehende Sitzungen: ein zurückgestufter
            # Admin soll nicht mit alter Session weiter Admin-Endpunkte sehen
            # (das Frontend blendet nach Neuanmeldung den Admin-Tab aus).
            conn.execute("DELETE FROM sitzung WHERE nutzer_id = ?", (nutzer_id,))
            db.change_log_eintrag(
                conn,
                entitaet="nutzer",
                entitaet_id=nutzer_id,
                feld="rolle",
                alt_wert=ziel["rolle"],
                neu_wert=daten.rolle,
                quelle="admin",
                akteur=admin["anzeigename"],
                zeitpunkt_utc=jetzt,
            )
        if daten.ki_freigeschaltet is not None and bool(ziel["ki_freigeschaltet"]) != daten.ki_freigeschaltet:
            conn.execute(
                "UPDATE nutzer SET ki_freigeschaltet = ? WHERE id = ?",
                (1 if daten.ki_freigeschaltet else 0, nutzer_id),
            )
            db.change_log_eintrag(
                conn,
                entitaet="nutzer",
                entitaet_id=nutzer_id,
                feld="ki_freigeschaltet",
                alt_wert=ziel["ki_freigeschaltet"],
                neu_wert=1 if daten.ki_freigeschaltet else 0,
                quelle="admin",
                akteur=admin["anzeigename"],
                zeitpunkt_utc=jetzt,
            )
        if daten.pin is not None:
            conn.execute(
                "UPDATE nutzer SET pin_hash = ? WHERE id = ?",
                (pin_hashen(daten.pin), nutzer_id),
            )
            # Bestehende Sessions des Nutzers ungültig machen — eine geänderte PIN
            # soll alte Anmeldungen (z. B. auf einem verlorenen Gerät) beenden.
            conn.execute("DELETE FROM sitzung WHERE nutzer_id = ?", (nutzer_id,))
            db.change_log_eintrag(
                conn,
                entitaet="nutzer",
                entitaet_id=nutzer_id,
                feld="pin_hash",
                alt_wert="(geheim)",
                neu_wert="(geheim)",
                quelle="admin",
                akteur=admin["anzeigename"],
                zeitpunkt_utc=jetzt,
            )
        # Innerhalb der Transaktion lesen — sonst kann eine parallele Löschung
        # zwischen Commit und SELECT die Antwort aushebeln.
        zeile = conn.execute(
            "SELECT id, anzeigename, rolle, ki_freigeschaltet, rangliste_sichtbar"
            " FROM nutzer WHERE id = ?",
            (nutzer_id,),
        ).fetchone()
    return {
        **dict(zeile),
        "ki_freigeschaltet": bool(zeile["ki_freigeschaltet"]),
        "rangliste_sichtbar": bool(zeile["rangliste_sichtbar"]),
    }


@router.delete("/nutzer/{nutzer_id}", status_code=204)
def nutzer_loeschen(
    nutzer_id: int,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    """Nutzer samt Tipps/Pins/Sessions löschen (FK-Kaskaden im Schema)."""
    if nutzer_id == admin["id"]:
        raise HTTPException(status_code=409, detail="Du kannst dich nicht selbst löschen.")
    with db.schreib_transaktion(conn):
        ziel = conn.execute(
            "SELECT id, anzeigename, rolle FROM nutzer WHERE id = ?", (nutzer_id,)
        ).fetchone()
        if ziel is None:
            raise HTTPException(status_code=404, detail="Nutzer nicht gefunden")
        if ziel["rolle"] == "admin":
            weitere_admins = conn.execute(
                "SELECT COUNT(*) AS anzahl FROM nutzer WHERE rolle = 'admin' AND id != ?",
                (nutzer_id,),
            ).fetchone()["anzahl"]
            if not weitere_admins:
                raise HTTPException(
                    status_code=409, detail="Der letzte Admin kann nicht gelöscht werden."
                )
        # override.gesetzt_von hat keine Lösch-Kaskade — Verweis lösen, Override bleibt.
        conn.execute(
            "UPDATE override SET gesetzt_von = NULL WHERE gesetzt_von = ?", (nutzer_id,)
        )
        conn.execute("DELETE FROM nutzer WHERE id = ?", (nutzer_id,))
        db.change_log_eintrag(
            conn,
            entitaet="nutzer",
            entitaet_id=nutzer_id,
            feld="geloescht",
            alt_wert=ziel["anzeigename"],
            neu_wert=None,
            quelle="admin",
            akteur=admin["anzeigename"],
            zeitpunkt_utc=jetzt_iso(),
        )


@router.post("/spielplan-import")
def spielplan_import(
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
    daten: Annotated[dict, Body()],
) -> dict[str, Any]:
    try:
        ergebnis = importer.spielplan_importieren(conn, daten, akteur=admin["anzeigename"])
    except (ValueError, KeyError) as fehler:
        raise HTTPException(status_code=422, detail=f"Import fehlgeschlagen: {fehler}") from None
    for spiel_id in ergebnis.neu_beendete_spiel_ids:
        tippspiel.spiel_auswerten(conn, spiel_id, einstellungen, akteur=admin["anzeigename"])
    return {
        "teams": ergebnis.teams,
        "spielorte": ergebnis.spielorte,
        "spiele_neu": ergebnis.spiele_neu,
        "spiele_aktualisiert": ergebnis.spiele_aktualisiert,
    }


@router.post("/spiele/{spiel_id}/ergebnis")
def ergebnis_setzen(
    spiel_id: int,
    daten: ErgebnisEingabe,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> dict[str, Any]:
    """Notfalleingabe, falls die API ausfällt (SPEC 4.4): Ergebnis setzen + auswerten."""
    with db.schreib_transaktion(conn):
        # Lesen innerhalb der Transaktion, damit alt_wert im change_log nicht
        # durch parallele Änderungen veraltet (TOCTOU).
        spiel = conn.execute("SELECT * FROM spiel WHERE id = ?", (spiel_id,)).fetchone()
        if spiel is None:
            raise HTTPException(status_code=404, detail="Spiel nicht gefunden")
        jetzt = jetzt_iso()
        deltas: dict[str, tuple] = {}
        for feld, neu in (
            ("tore_heim", daten.tore_heim),
            ("tore_gast", daten.tore_gast),
            ("status", daten.status),
            ("ergebnis_nach", daten.ergebnis_nach),
            ("elfmeter_sieger_team_id", daten.elfmeter_sieger_team_id),
        ):
            if spiel[feld] != neu:
                deltas[feld] = (spiel[feld], neu)
                db.change_log_eintrag(
                    conn,
                    entitaet="spiel",
                    entitaet_id=spiel_id,
                    feld=feld,
                    alt_wert=spiel[feld],
                    neu_wert=neu,
                    quelle="admin",
                    akteur=admin["anzeigename"],
                    zeitpunkt_utc=jetzt,
                )
            # Manuell gesetzte Werte überdauern API-Syncs (SPEC 4.4),
            # bis der Admin den Override wieder aufhebt.
            overrides.setzen(
                conn,
                entitaet="spiel",
                entitaet_id=spiel_id,
                feld=feld,
                wert=neu,
                nutzer_id=admin["id"],
            )
        conn.execute(
            "UPDATE spiel SET tore_heim = ?, tore_gast = ?, status = ?, ergebnis_nach = ?,"
            " elfmeter_sieger_team_id = ? WHERE id = ?",
            (
                daten.tore_heim,
                daten.tore_gast,
                daten.status,
                daten.ergebnis_nach,
                daten.elfmeter_sieger_team_id,
                spiel_id,
            ),
        )
        ereignis_ids = live.deltas_verarbeiten(conn, spiel_id=spiel_id, deltas=deltas)
    if deltas:
        live.sync_delta_publizieren(conn, spiel_id, deltas, ereignis_ids)
    gewertet = 0
    if daten.status == "beendet":
        gewertet = tippspiel.spiel_auswerten(
            conn, spiel_id, einstellungen, akteur=admin["anzeigename"]
        )
    return {"spiel_id": spiel_id, "tipps_gewertet": gewertet}


@router.post("/spiele/{spiel_id}/ereignis", status_code=201)
def ereignis_nachtragen(
    spiel_id: int,
    daten: EreignisEingabe,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    """Ticker-Eintrag manuell anlegen (die Free-Tier-API liefert keine Details)."""
    with db.schreib_transaktion(conn):
        # Existenz-Check innerhalb der Transaktion (TOCTOU)
        spiel = conn.execute("SELECT id FROM spiel WHERE id = ?", (spiel_id,)).fetchone()
        if spiel is None:
            raise HTTPException(status_code=404, detail="Spiel nicht gefunden")
        ereignis_id = live.ereignis_anlegen(
            conn,
            spiel_id=spiel_id,
            typ=daten.typ,
            minute=daten.minute,
            team_id=daten.team_id,
            spieler_id=daten.spieler_id,
            spieler2_id=daten.spieler2_id,
            text=daten.text,
            quelle="admin",
        )
    eintrag = live.ereignis_json(conn, ereignis_id)
    if eintrag:
        live.broker.publish("ereignis", eintrag)
    return eintrag or {"id": ereignis_id}


@router.delete("/ereignisse/{ereignis_id}", status_code=204)
def ereignis_loeschen(
    ereignis_id: int,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    with db.schreib_transaktion(conn):
        geloescht = conn.execute(
            "DELETE FROM ereignis WHERE id = ?", (ereignis_id,)
        ).rowcount
    if not geloescht:
        raise HTTPException(status_code=404, detail="Ereignis nicht gefunden")


@router.post("/sync/{job}")
def sync_anstossen(
    job: str,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> dict[str, Any]:
    if job not in (sync.JOB_STAMMDATEN, sync.JOB_ERGEBNISSE):
        raise HTTPException(status_code=404, detail=f"Unbekannter Sync-Job: {job}")
    try:
        if job == sync.JOB_STAMMDATEN:
            bericht = sync.stammdaten_sync(conn, einstellungen)
        else:
            bericht = sync.ergebnis_sync(conn, einstellungen)
    except FussballApiFehler as fehler:
        raise HTTPException(status_code=502, detail=str(fehler)) from None
    return {"job": bericht.job, "detail": bericht.zusammenfassung()}


@router.get("/overrides")
def overrides_liste(
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    return overrides.aktive_liste(conn)


@router.delete("/overrides/{override_id}", status_code=204)
def override_aufheben(
    override_id: int,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    """Override deaktivieren — beim nächsten Sync gilt wieder der API-Stand."""
    with db.schreib_transaktion(conn):
        aufgehoben = overrides.aufheben(conn, override_id)
    if not aufgehoben:
        raise HTTPException(status_code=404, detail="Override nicht gefunden oder bereits aufgehoben")


class FeedEingabe(BaseModel):
    url: str = Field(min_length=10, max_length=500, pattern=r"^https?://")
    titel: str | None = Field(default=None, max_length=100)


class BonusfrageEingabe(BaseModel):
    frage: str = Field(min_length=3, max_length=200)
    typ: str = Field(pattern="^(team|spieler)$")
    punkte_wert: int = Field(default=10, ge=1, le=100)
    einsendeschluss_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class BonusAufloesung(BaseModel):
    aufloesung_ref: int


@router.get("/feeds")
def feeds_liste(
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT f.*, (SELECT COUNT(*) FROM news_item n WHERE n.feed_id = f.id) AS anzahl_news"
        " FROM feed f ORDER BY f.id"
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]


@router.post("/feeds", status_code=201)
def feed_anlegen(
    daten: FeedEingabe,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    with db.schreib_transaktion(conn):
        try:
            cursor = conn.execute(
                "INSERT INTO feed (url, titel, aktiv) VALUES (?, ?, 1)",
                (daten.url, daten.titel),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="Feed-URL existiert bereits") from None
    return {"id": cursor.lastrowid, "url": daten.url, "titel": daten.titel}


@router.post("/feeds/{feed_id}/umschalten")
def feed_umschalten(
    feed_id: int,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    with db.schreib_transaktion(conn):
        geaendert = conn.execute(
            "UPDATE feed SET aktiv = 1 - aktiv WHERE id = ?", (feed_id,)
        ).rowcount
    if not geaendert:
        raise HTTPException(status_code=404, detail="Feed nicht gefunden")
    zeile = conn.execute("SELECT id, aktiv FROM feed WHERE id = ?", (feed_id,)).fetchone()
    return dict(zeile)


@router.delete("/feeds/{feed_id}", status_code=204)
def feed_loeschen(
    feed_id: int,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    with db.schreib_transaktion(conn):
        geloescht = conn.execute("DELETE FROM feed WHERE id = ?", (feed_id,)).rowcount
    if not geloescht:
        raise HTTPException(status_code=404, detail="Feed nicht gefunden")


@router.post("/feeds/abrufen")
def feeds_abrufen(
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    bericht = news.alle_feeds_abrufen(conn)
    return {"feeds": bericht.feeds, "neu": bericht.neu, "fehler": bericht.fehler}


@router.post("/bonusfragen", status_code=201)
def bonusfrage_anlegen(
    daten: BonusfrageEingabe,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    frage_id = bonus.frage_anlegen(
        conn,
        frage=daten.frage,
        typ=daten.typ,
        punkte_wert=daten.punkte_wert,
        einsendeschluss_utc=daten.einsendeschluss_utc,
    )
    return {"id": frage_id, "frage": daten.frage}


@router.post("/bonusfragen/{frage_id}/aufloesen")
def bonusfrage_aufloesen(
    frage_id: int,
    daten: BonusAufloesung,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    try:
        gewertet = bonus.aufloesen(
            conn,
            bonusfrage_id=frage_id,
            aufloesung_ref=daten.aufloesung_ref,
            akteur=admin["anzeigename"],
        )
    except bonus.BonusFehler as fehler:
        raise HTTPException(status_code=409, detail=str(fehler)) from None
    return {"bonusfrage_id": frage_id, "tipps_gewertet": gewertet}


class TokenEingabe(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    scopes: list[str] = Field(min_length=1)


class TaktikEingabe(BaseModel):
    formation: str | None = Field(default=None, max_length=20)
    beschreibung: str | None = Field(default=None, max_length=2000)
    staerken: str | None = Field(default=None, max_length=1000)
    schwaechen: str | None = Field(default=None, max_length=1000)


class VerletzungEingabe(BaseModel):
    spieler_id: int
    beschreibung: str = Field(min_length=1, max_length=500)
    status: str = Field(default="fraglich", pattern="^(fraglich|faellt aus|wieder fit)$")


@router.get("/agent-tokens")
def agent_tokens(
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT id, name, scopes, erstellt_utc, widerrufen_utc FROM agent_token ORDER BY id"
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]


@router.post("/agent-tokens", status_code=201)
def agent_token_erzeugen(
    daten: TokenEingabe,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    try:
        klartext = agenten.token_erzeugen(conn, name=daten.name, scopes=daten.scopes)
    except agenten.AgentFehler as fehler:
        raise HTTPException(status_code=409, detail=str(fehler)) from None
    # Klartext erscheint genau einmal — danach existiert nur noch der Hash.
    return {"name": daten.name, "token": klartext, "scopes": daten.scopes}


@router.delete("/agent-tokens/{token_id}", status_code=204)
def agent_token_widerrufen(
    token_id: int,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    if not agenten.token_widerrufen(conn, token_id):
        raise HTTPException(status_code=404, detail="Token nicht gefunden oder bereits widerrufen")


@router.get("/beitraege")
def beitraege_liste(
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT b.*, t.name AS team_name, sp.name AS spieler_name"
        " FROM beitrag b LEFT JOIN team t ON t.id = b.team_id"
        " LEFT JOIN spieler sp ON sp.id = b.spieler_id"
        " WHERE b.status = 'offen' ORDER BY b.erstellt_utc"
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]


@router.post("/beitraege/{beitrag_id}/{entscheidung}")
def beitrag_entscheiden(
    beitrag_id: int,
    entscheidung: str,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    """Vorschlag übernehmen (schreibt taktik/verletzung) oder verwerfen."""
    if entscheidung not in ("uebernehmen", "verwerfen"):
        raise HTTPException(status_code=404, detail="Entscheidung unbekannt")
    import json as json_modul

    with db.schreib_transaktion(conn):
        beitrag = conn.execute(
            "SELECT * FROM beitrag WHERE id = ? AND status = 'offen'", (beitrag_id,)
        ).fetchone()
        if beitrag is None:
            raise HTTPException(status_code=404, detail="Beitrag nicht gefunden oder entschieden")
        neuer_status = "uebernommen" if entscheidung == "uebernehmen" else "verworfen"
        conn.execute("UPDATE beitrag SET status = ? WHERE id = ?", (neuer_status, beitrag_id))
        if entscheidung == "uebernehmen":
            try:
                inhalt = json_modul.loads(beitrag["inhalt_json"])
            except (json_modul.JSONDecodeError, TypeError) as fehler:
                raise HTTPException(
                    status_code=422, detail=f"Beitragsinhalt ist kein gültiges JSON: {fehler}"
                ) from None
            if beitrag["typ"] == "taktik" and beitrag["team_id"]:
                conn.execute(
                    "INSERT INTO taktik (team_id, formation, beschreibung, staerken,"
                    " schwaechen, quelle, stand_utc) VALUES (?, ?, ?, ?, ?, 'agent', ?)"
                    " ON CONFLICT(team_id) DO UPDATE SET formation = excluded.formation,"
                    " beschreibung = excluded.beschreibung, staerken = excluded.staerken,"
                    " schwaechen = excluded.schwaechen, quelle = 'agent',"
                    " stand_utc = excluded.stand_utc",
                    (
                        beitrag["team_id"],
                        inhalt.get("formation"),
                        inhalt.get("beschreibung"),
                        inhalt.get("staerken"),
                        inhalt.get("schwaechen"),
                        jetzt_iso(),
                    ),
                )
            elif beitrag["typ"] == "verletzung" and beitrag["spieler_id"]:
                conn.execute(
                    "INSERT INTO verletzung (spieler_id, beschreibung, status, quelle,"
                    " gemeldet_utc, geprueft) VALUES (?, ?, ?, 'agent', ?, 1)",
                    (
                        beitrag["spieler_id"],
                        inhalt.get("beschreibung", "?"),
                        inhalt.get("status", "fraglich"),
                        jetzt_iso(),
                    ),
                )
    return {"id": beitrag_id, "status": neuer_status}


@router.put("/teams/{team_id}/taktik")
def taktik_setzen(
    team_id: int,
    daten: TaktikEingabe,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    with db.schreib_transaktion(conn):
        team = conn.execute("SELECT id FROM team WHERE id = ?", (team_id,)).fetchone()
        if team is None:
            raise HTTPException(status_code=404, detail="Team nicht gefunden")
        conn.execute(
            "INSERT INTO taktik (team_id, formation, beschreibung, staerken, schwaechen,"
            " quelle, stand_utc) VALUES (?, ?, ?, ?, ?, 'admin', ?)"
            " ON CONFLICT(team_id) DO UPDATE SET formation = excluded.formation,"
            " beschreibung = excluded.beschreibung, staerken = excluded.staerken,"
            " schwaechen = excluded.schwaechen, quelle = 'admin', stand_utc = excluded.stand_utc",
            (
                team_id,
                daten.formation,
                daten.beschreibung,
                daten.staerken,
                daten.schwaechen,
                jetzt_iso(),
            ),
        )
    return {"team_id": team_id, "quelle": "admin"}


@router.post("/verletzungen", status_code=201)
def verletzung_anlegen(
    daten: VerletzungEingabe,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    with db.schreib_transaktion(conn):
        spieler = conn.execute(
            "SELECT id FROM spieler WHERE id = ?", (daten.spieler_id,)
        ).fetchone()
        if spieler is None:
            raise HTTPException(status_code=404, detail="Spieler nicht gefunden")
        cursor = conn.execute(
            "INSERT INTO verletzung (spieler_id, beschreibung, status, quelle, gemeldet_utc,"
            " geprueft) VALUES (?, ?, ?, 'admin', ?, 1)",
            (daten.spieler_id, daten.beschreibung, daten.status, jetzt_iso()),
        )
    return {"id": cursor.lastrowid}


@router.get("/sync-status")
def sync_status(
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    zeilen = conn.execute("SELECT * FROM sync_status ORDER BY job").fetchall()
    return [dict(zeile) for zeile in zeilen]


@router.delete("/nutzer/{nutzer_id}/profilbild", status_code=204)
def nutzer_profilbild_entfernen(
    nutzer_id: int,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> None:
    """Moderation: ein unpassendes Profilbild entfernen (v0.1.1)."""
    ziel = conn.execute(
        "SELECT profilbild FROM nutzer WHERE id = ?", (nutzer_id,)
    ).fetchone()
    if ziel is None:
        raise HTTPException(status_code=404, detail="Nutzer nicht gefunden")
    with db.schreib_transaktion(conn):
        conn.execute("UPDATE nutzer SET profilbild = NULL WHERE id = ?", (nutzer_id,))
        db.change_log_eintrag(
            conn,
            entitaet="nutzer",
            entitaet_id=nutzer_id,
            feld="profilbild",
            alt_wert=ziel["profilbild"],
            neu_wert=None,
            quelle="admin",
            akteur=admin["anzeigename"],
            zeitpunkt_utc=jetzt_iso(),
        )
    # Datei erst NACH dem erfolgreichen Commit wegräumen — schlägt die
    # Transaktion fehl, zeigt die DB nie auf eine schon gelöschte Datei.
    if ziel["profilbild"]:
        profilbilder.loeschen(einstellungen, ziel["profilbild"])


# ---------- Feedback-Posteingang (v0.1.1) ----------


@router.get("/feedback")
def feedback_liste(
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    """Posteingang: offene Meldungen zuerst, innerhalb dessen neueste oben."""
    zeilen = conn.execute(
        "SELECT f.id, f.kategorie, f.nachricht, f.status, f.erstellt_utc, n.anzeigename"
        " FROM feedback f JOIN nutzer n ON n.id = f.nutzer_id"
        " ORDER BY CASE f.status WHEN 'offen' THEN 0 ELSE 1 END, f.erstellt_utc DESC, f.id DESC"
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]


@router.post("/feedback/{feedback_id}/umschalten")
def feedback_umschalten(
    feedback_id: int,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    with db.schreib_transaktion(conn):
        zeile = conn.execute(
            "SELECT status FROM feedback WHERE id = ?", (feedback_id,)
        ).fetchone()
        if zeile is None:
            raise HTTPException(status_code=404, detail="Meldung nicht gefunden")
        neuer_status = "erledigt" if zeile["status"] == "offen" else "offen"
        conn.execute("UPDATE feedback SET status = ? WHERE id = ?", (neuer_status, feedback_id))
        db.change_log_eintrag(
            conn,
            entitaet="feedback",
            entitaet_id=feedback_id,
            feld="status",
            alt_wert=zeile["status"],
            neu_wert=neuer_status,
            quelle="admin",
            akteur=admin["anzeigename"],
            zeitpunkt_utc=jetzt_iso(),
        )
    return {"id": feedback_id, "status": neuer_status}


@router.delete("/feedback/{feedback_id}", status_code=204)
def feedback_loeschen(
    feedback_id: int,
    admin: Annotated[sqlite3.Row, Depends(admin_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> None:
    with db.schreib_transaktion(conn):
        zeile = conn.execute(
            "SELECT kategorie, nachricht FROM feedback WHERE id = ?", (feedback_id,)
        ).fetchone()
        if zeile is None:
            raise HTTPException(status_code=404, detail="Meldung nicht gefunden")
        conn.execute("DELETE FROM feedback WHERE id = ?", (feedback_id,))
        db.change_log_eintrag(
            conn,
            entitaet="feedback",
            entitaet_id=feedback_id,
            feld="geloescht",
            alt_wert=f"{zeile['kategorie']}: {zeile['nachricht'][:80]}",
            neu_wert=None,
            quelle="admin",
            akteur=admin["anzeigename"],
            zeitpunkt_utc=jetzt_iso(),
        )
