"""Hintergrund-Scheduler: Zustandsmaschine pro Spieltag (SPEC 4.1).

- Stammdaten-Sync 1x täglich (inkl. Tabellen + Torschützen).
- Spieltags-Sync im Intervall (WM26_SYNC_INTERVALL_MINUTEN, Standard 60).
- Vorlauf-Poll ~60 Sekunden ab T-75 Min. vor dem Anpfiff.
- Läuft ein Spiel wirklich (live/halbzeit), zieht der Poll auf ~8 Abrufe
  pro Minute an — der Spielplan-Abruf ist EIN API-Call für alle parallelen
  Spiele zugleich, und der API-Client hält ohnehin 6,5 s Mindestabstand,
  das Free-Tier-Limit (10/Min.) bleibt also bewusst unausgereizt.

Läuft als asyncio-Task im FastAPI-Lifespan; die eigentlichen Sync-Läufe
laufen in Worker-Threads (SQLite + httpx sind blockierend).
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import timedelta

from . import db
from .config import Einstellungen
from .services import aufstellungen, news, push, quoten, sync
from .zeit import iso_utc, jetzt_utc

logger = logging.getLogger("wm26.scheduler")

_STAMMDATEN_ABSTAND = timedelta(hours=20)
_VORLAUF_INTERVALL_SEKUNDEN = 60
_VORLAUF_MINUTEN = 75
# Sicherheitsnetz: hängt ein Spiel im Status live fest (API-Aussetzer),
# fällt der Poll nach dieser Zeit auf das normale Intervall zurück.
_LIVE_NACHLAUF_STUNDEN = 4


def _stammdaten_faellig(conn: sqlite3.Connection) -> bool:
    zeile = conn.execute(
        "SELECT letzter_erfolg_utc FROM sync_status WHERE job = ?", (sync.JOB_STAMMDATEN,)
    ).fetchone()
    if zeile is None or not zeile["letzter_erfolg_utc"]:
        return True
    schwelle = iso_utc(jetzt_utc() - _STAMMDATEN_ABSTAND)
    return zeile["letzter_erfolg_utc"] < schwelle


def _poll_phase(conn: sqlite3.Connection) -> str:
    """Wie eng muss gepollt werden? 'live' > 'vorlauf' > 'normal'.

    'live': ein Spiel läuft wirklich (Status live/halbzeit) — engster Takt.
    'vorlauf': Anpfiff steht bevor (T-75) ODER der Anstoß ist vorbei, aber
    die API hinkt beim Statuswechsel hinterher (Status noch 'geplant') —
    ohne diesen Fall würde die App ein komplettes Spiel verpassen. Das
    Nachlauf-Fenster begrenzt beide Fälle gegen hängengebliebene Spiele.
    """
    jetzt = jetzt_utc()
    nachlauf = iso_utc(jetzt - timedelta(hours=_LIVE_NACHLAUF_STUNDEN))
    laeuft = conn.execute(
        "SELECT 1 FROM spiel WHERE status IN ('live', 'halbzeit')"
        " AND anstoss_utc >= ? LIMIT 1",
        (nachlauf,),
    ).fetchone()
    if laeuft:
        return "live"
    vorlauf = conn.execute(
        "SELECT 1 FROM spiel WHERE status = 'geplant'"
        " AND anstoss_utc BETWEEN ? AND ? LIMIT 1",
        (nachlauf, iso_utc(jetzt + timedelta(minutes=_VORLAUF_MINUTEN))),
    ).fetchone()
    return "vorlauf" if vorlauf else "normal"


def _sync_lauf(einstellungen: Einstellungen) -> str:
    """Ein Scheduler-Tick; liefert die Poll-Phase ('live'/'vorlauf'/'normal')."""
    conn = db.verbinden(einstellungen.db_pfad)
    try:
        if _stammdaten_faellig(conn):
            bericht = sync.stammdaten_sync(conn, einstellungen)
        else:
            bericht = sync.ergebnis_sync(conn, einstellungen)
        logger.info("Sync %s: %s", bericht.job, bericht.zusammenfassung())
        try:
            gesendet = push.erinnerungen_pruefen(conn, einstellungen)
            if gesendet:
                logger.info("Push: %s Erinnerungen versendet", gesendet)
        except Exception:
            logger.exception("Erinnerungs-Push fehlgeschlagen")
        try:
            if news.abruf_faellig(conn, minuten=30):
                rss = news.alle_feeds_abrufen(conn)
                if rss.neu or rss.fehler:
                    logger.info("RSS: %s neue Einträge, %s Fehler", rss.neu, rss.fehler)
        except Exception:
            logger.exception("RSS-Abruf fehlgeschlagen")
        try:
            if quoten.aktiv(einstellungen) and quoten.abruf_faellig(conn):
                quoten_bericht = quoten.quoten_sync(conn, einstellungen)
                logger.info("Sync %s: %s", quoten_bericht.job, quoten_bericht.zusammenfassung())
        except Exception:
            # Fehlerdetails stehen in sync_status; Quoten sind nie deploy-kritisch.
            logger.exception("Quoten-Sync fehlgeschlagen")
        try:
            # Aufstellungs-Poll (SPEC 4.1): nur wenn ein Spiel im Fenster steht,
            # höchstens alle 5 Minuten (Drossel über sync_status).
            if einstellungen.aufstellungen_aktiv and aufstellungen.abruf_faellig(conn):
                auf_bericht = aufstellungen.aufstellungen_sync(conn, einstellungen)
                if auf_bericht.geprueft:
                    logger.info("Sync %s: %s", auf_bericht.job, auf_bericht.zusammenfassung())
        except Exception:
            logger.exception("Aufstellungs-Sync fehlgeschlagen")
        phase = _poll_phase(conn)
        if phase == "normal":
            # Ruhige Phasen nutzen: fehlende direkte Vergleiche der nächsten
            # Tage nachladen (z. B. sobald K.o.-Paarungen feststehen).
            vergleiche = sync.vergleiche_sync(
                conn, einstellungen, max_abrufe=8, nur_naechste_tage=4
            )
            if vergleiche.vergleiche:
                logger.info("Sync %s: %s", vergleiche.job, vergleiche.zusammenfassung())
        return phase
    finally:
        conn.close()


async def sync_schleife(einstellungen: Einstellungen, stop_ereignis: asyncio.Event) -> None:
    normal_intervall = einstellungen.sync_intervall_minuten * 60
    # Harte Untergrenze: nie schneller ticken, als der Tarif Calls erlaubt —
    # die Drossel des API-Clients lebt pro Instanz und schützt nicht über
    # Scheduler-Ticks hinweg (jeder Tick baut einen frischen Client).
    tarif_abstand = 60.0 / max(einstellungen.api_rate_pro_minute, 1)
    intervalle = {
        "live": max(einstellungen.live_poll_sekunden, tarif_abstand, 1.0),
        "vorlauf": _VORLAUF_INTERVALL_SEKUNDEN,
        "normal": normal_intervall,
    }
    while not stop_ereignis.is_set():
        phase = "normal"
        try:
            phase = await asyncio.to_thread(_sync_lauf, einstellungen)
        except Exception:
            # Fehler sind bereits in sync_status protokolliert; Schleife läuft weiter.
            logger.exception("Sync-Lauf fehlgeschlagen")
        try:
            await asyncio.wait_for(stop_ereignis.wait(), timeout=intervalle[phase])
        except asyncio.TimeoutError:
            pass
