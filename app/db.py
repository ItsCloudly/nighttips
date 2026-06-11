"""SQLite-Zugriff: WAL-Modus, ein Prozess-weiter Schreib-Lock (Lock-Disziplin, SPEC 8.4).

Lesezugriffe nutzen je Request eine eigene Verbindung (WAL erlaubt parallele Leser).
Schreibzugriffe laufen über `schreib_transaktion`, die in-process serialisiert und
die Transaktion gesammelt committet oder zurückrollt.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_PFAD = Path(__file__).with_name("schema.sql")

_schreib_lock = threading.Lock()


def verbinden(db_pfad: Path | str) -> sqlite3.Connection:
    pfad = Path(db_pfad)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI führt Sync-Dependencies und Endpunkte
    # bei parallelen Requests in unterschiedlichen Threadpool-Threads aus.
    # Jede Verbindung gehört trotzdem genau einem Request (kein paralleler
    # Zugriff auf dieselbe Verbindung); Schreiben serialisiert der Lock unten.
    conn = sqlite3.connect(pfad, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def schema_anlegen(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PFAD.read_text(encoding="utf-8"))
    _migrationen(conn)
    conn.commit()


def _migrationen(conn: sqlite3.Connection) -> None:
    """Nachträgliche Spalten für Bestands-Datenbanken (CREATE IF NOT EXISTS greift dort nicht)."""
    nutzer_spalten = {zeile["name"] for zeile in conn.execute("PRAGMA table_info(nutzer)")}
    if "ki_freigeschaltet" not in nutzer_spalten:
        conn.execute("ALTER TABLE nutzer ADD COLUMN ki_freigeschaltet INTEGER NOT NULL DEFAULT 0")
    if "rangliste_sichtbar" not in nutzer_spalten:
        conn.execute("ALTER TABLE nutzer ADD COLUMN rangliste_sichtbar INTEGER NOT NULL DEFAULT 1")
    if "tipp_erinnerung_minuten" not in nutzer_spalten:
        conn.execute("ALTER TABLE nutzer ADD COLUMN tipp_erinnerung_minuten INTEGER")


@contextmanager
def schreib_transaktion(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Serialisierte Schreib-Transaktion: commit bei Erfolg, rollback bei Fehler."""
    with _schreib_lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def change_log_eintrag(
    conn: sqlite3.Connection,
    *,
    entitaet: str,
    entitaet_id: int,
    feld: str,
    alt_wert: object,
    neu_wert: object,
    quelle: str,
    akteur: str,
    zeitpunkt_utc: str,
) -> None:
    """Fügt einen Historieneintrag hinzu (innerhalb einer offenen Transaktion aufrufen)."""
    conn.execute(
        "INSERT INTO change_log (entitaet, entitaet_id, feld, alt_wert, neu_wert, quelle, akteur, zeitpunkt_utc)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entitaet,
            entitaet_id,
            feld,
            None if alt_wert is None else str(alt_wert),
            None if neu_wert is None else str(neu_wert),
            quelle,
            akteur,
            zeitpunkt_utc,
        ),
    )
