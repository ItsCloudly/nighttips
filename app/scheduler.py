"""Hintergrund-Scheduler: Zustandsmaschine pro Spieltag (SPEC 4.1).

- Stammdaten-Sync 1x täglich (inkl. Tabellen + Torschützen).
- Spieltags-Sync im Intervall (WM26_SYNC_INTERVALL_MINUTEN, Standard 60).
- Live-Poll ~60 Sekunden, sobald ein Spiel im Vorlauf (T-75 Min.) oder
  live/halbzeit ist — der Spielplan-Abruf ist ein einziger API-Call, das
  Free-Tier-Limit (10/Min.) bleibt damit weit unterschritten.

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
from .services import news, push, sync
from .zeit import iso_utc, jetzt_utc

logger = logging.getLogger("wm26.scheduler")

_STAMMDATEN_ABSTAND = timedelta(hours=20)
_LIVE_INTERVALL_SEKUNDEN = 60
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


def _live_phase(conn: sqlite3.Connection) -> bool:
    """Live-Poll nötig? Spiel läuft, oder Anpfiff innerhalb des Vorlaufs.

    Die untere Schranke für 'geplant' liegt bewusst Stunden in der
    Vergangenheit: hinkt die API beim Statuswechsel hinterher (Anstoß vorbei,
    Status noch geplant), muss der Poll weiterlaufen, sonst verpasst die App
    das komplette Spiel. Das Nachlauf-Fenster begrenzt beide Fälle.
    """
    jetzt = jetzt_utc()
    zeile = conn.execute(
        "SELECT 1 FROM spiel WHERE"
        " (status IN ('live', 'halbzeit') AND anstoss_utc >= ?)"
        " OR (status = 'geplant' AND anstoss_utc BETWEEN ? AND ?)"
        " LIMIT 1",
        (
            iso_utc(jetzt - timedelta(hours=_LIVE_NACHLAUF_STUNDEN)),
            iso_utc(jetzt - timedelta(hours=_LIVE_NACHLAUF_STUNDEN)),
            iso_utc(jetzt + timedelta(minutes=_VORLAUF_MINUTEN)),
        ),
    ).fetchone()
    return zeile is not None


def _sync_lauf(einstellungen: Einstellungen) -> bool:
    """Ein Scheduler-Tick; liefert True, wenn die Live-Phase aktiv ist."""
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
        live_phase = _live_phase(conn)
        if not live_phase:
            # Ruhige Phasen nutzen: fehlende direkte Vergleiche der nächsten
            # Tage nachladen (z. B. sobald K.o.-Paarungen feststehen).
            vergleiche = sync.vergleiche_sync(
                conn, einstellungen, max_abrufe=8, nur_naechste_tage=4
            )
            if vergleiche.vergleiche:
                logger.info("Sync %s: %s", vergleiche.job, vergleiche.zusammenfassung())
        return live_phase
    finally:
        conn.close()


async def sync_schleife(einstellungen: Einstellungen, stop_ereignis: asyncio.Event) -> None:
    normal_intervall = einstellungen.sync_intervall_minuten * 60
    while not stop_ereignis.is_set():
        live_phase = False
        try:
            live_phase = await asyncio.to_thread(_sync_lauf, einstellungen)
        except Exception:
            # Fehler sind bereits in sync_status protokolliert; Schleife läuft weiter.
            logger.exception("Sync-Lauf fehlgeschlagen")
        intervall = _LIVE_INTERVALL_SEKUNDEN if live_phase else normal_intervall
        try:
            await asyncio.wait_for(stop_ereignis.wait(), timeout=intervall)
        except asyncio.TimeoutError:
            pass
