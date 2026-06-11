"""Live-Schicht: SSE-Broker und Ereignis-Ableitung aus Sync-Deltas (SPEC 4.2).

Der Broker verteilt Events an alle offenen SSE-Verbindungen. Sync-Jobs laufen
in Worker-Threads (asyncio.to_thread), deshalb ist publish() threadsicher und
reicht Events über call_soon_threadsafe in die Event-Loop. Ohne laufende Loop
(CLI, Tests ohne Stream) ist publish() ein No-op — die Ereignisse landen
trotzdem in der ereignis-Tabelle.

Die Free-Tier-API liefert keine Einzel-Events (Torschütze, Minute); die App
leitet Ticker-Einträge deshalb aus Score-/Status-Deltas ab. Der Admin kann
Details (Minute, Schütze) nachtragen, Quelle admin.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from typing import Any

from datetime import timedelta

from ..zeit import iso_utc, jetzt_iso, jetzt_utc, parse_utc

logger = logging.getLogger("wm26.live")

# Sinnvolle Obergrenze offener SSE-Verbindungen (Freundeskreis, SPEC 1).
_MAX_CLIENTS = 100


class SseBroker:
    """Verteilt Server-Sent-Events an alle verbundenen Clients (in-process)."""

    def __init__(self) -> None:
        self._clients: set[asyncio.Queue[str]] = set()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def loop_setzen(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    @property
    def verbindungen(self) -> int:
        with self._lock:
            return len(self._clients)

    def anmelden(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        with self._lock:
            if len(self._clients) >= _MAX_CLIENTS:
                raise RuntimeError("Zu viele offene Live-Verbindungen")
            self._clients.add(queue)
        return queue

    def abmelden(self, queue: asyncio.Queue[str]) -> None:
        with self._lock:
            self._clients.discard(queue)

    def publish(self, event: str, daten: dict[str, Any]) -> None:
        """Threadsicher: aus Sync-Threads und aus der Loop heraus aufrufbar."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        nachricht = f"event: {event}\ndata: {json.dumps(daten, ensure_ascii=False)}\n\n"
        with self._lock:
            clients = list(self._clients)
        for queue in clients:
            try:
                loop.call_soon_threadsafe(self._einreihen, queue, nachricht)
            except RuntimeError:
                # Loop wird gerade beendet — Event verfällt, DB bleibt Quelle der Wahrheit.
                return

    @staticmethod
    def _einreihen(queue: asyncio.Queue[str], nachricht: str) -> None:
        try:
            queue.put_nowait(nachricht)
        except asyncio.QueueFull:
            # Langsamer Client: Event verwerfen, der Reload holt den Stand aus der DB.
            logger.debug("SSE-Queue voll, Event verworfen")


broker = SseBroker()


def ereignis_anlegen(
    conn: sqlite3.Connection,
    *,
    spiel_id: int,
    typ: str,
    minute: int | None = None,
    team_id: int | None = None,
    spieler_id: int | None = None,
    spieler2_id: int | None = None,
    text: str | None = None,
    quelle: str = "api",
) -> int:
    """Schreibt einen Ticker-Eintrag (innerhalb einer offenen Schreib-Transaktion)."""
    cursor = conn.execute(
        "INSERT INTO ereignis (spiel_id, minute, typ, team_id, spieler_id, spieler2_id,"
        " text, quelle, erstellt_utc) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (spiel_id, minute, typ, team_id, spieler_id, spieler2_id, text, quelle, jetzt_iso()),
    )
    return cursor.lastrowid


def ereignis_json(conn: sqlite3.Connection, ereignis_id: int) -> dict[str, Any] | None:
    zeile = conn.execute(
        "SELECT e.id, e.spiel_id, e.minute, e.typ, e.text, e.quelle, e.erstellt_utc,"
        " e.team_id, t.name AS team_name, sp.name AS spieler_name, sp2.name AS spieler2_name"
        " FROM ereignis e"
        " LEFT JOIN team t ON t.id = e.team_id"
        " LEFT JOIN spieler sp ON sp.id = e.spieler_id"
        " LEFT JOIN spieler sp2 ON sp2.id = e.spieler2_id"
        " WHERE e.id = ?",
        (ereignis_id,),
    ).fetchone()
    return dict(zeile) if zeile else None


def spielzeit(
    conn: sqlite3.Connection, *, spiel_id: int, anstoss_utc: str, status: str
) -> dict[str, Any] | None:
    """Bezugszeitpunkte für die ungefähre Spielminute laufender Spiele.

    Die API liefert keine verlässliche Minute — der Client schreibt sie ab dem
    Anstoß fort. Der Anpfiff-Ticker-Eintrag ist der genauere Bezug, sofern er
    nah an der geplanten Zeit liegt (sonst war der Sync zu spät dran); der
    Wiederanpfiff-Eintrag bestimmt die Minuten der 2. Halbzeit.
    """
    if status not in ("live", "halbzeit"):
        return None
    anpfiff = conn.execute(
        "SELECT erstellt_utc FROM ereignis WHERE spiel_id = ? AND typ = 'anpfiff'"
        " ORDER BY id DESC LIMIT 1",
        (spiel_id,),
    ).fetchone()
    zweite = conn.execute(
        "SELECT erstellt_utc FROM ereignis WHERE spiel_id = ? AND typ = 'freitext'"
        " AND text = 'Anpfiff 2. Halbzeit' ORDER BY id DESC LIMIT 1",
        (spiel_id,),
    ).fetchone()
    basis = anstoss_utc
    if anpfiff is not None:
        versatz = (parse_utc(anpfiff["erstellt_utc"]) - parse_utc(anstoss_utc)).total_seconds()
        if 0 <= versatz <= 15 * 60:
            basis = anpfiff["erstellt_utc"]
    return {
        "anpfiff_utc": basis,
        "zweite_hz_utc": zweite["erstellt_utc"] if zweite else None,
    }


_STATUS_EREIGNIS = {
    ("geplant", "live"): ("anpfiff", "Anpfiff"),
    ("halbzeit", "live"): ("freitext", "Anpfiff 2. Halbzeit"),
    ("live", "halbzeit"): ("halbzeit", "Halbzeit"),
    ("live", "beendet"): ("abpfiff", "Abpfiff"),
    ("halbzeit", "beendet"): ("abpfiff", "Abpfiff"),
    ("geplant", "beendet"): ("abpfiff", "Abpfiff"),
}


# Dedup-Fenster je Ereignistyp (Minuten): identische API-Einträge innerhalb
# des Fensters sind Artefakte (z. B. Prozess-Überlappung beim Deploy mitten im
# Spiel), keine neuen Ereignisse. Tor/VAR kurz halten — ein echtes Tor zum
# gleichen Stand nach VAR-Rücknahme braucht Minuten, kein Sync-Race.
_DEDUP_FENSTER_MINUTEN = {"tor": 1, "var": 1}
_DEDUP_STANDARD_MINUTEN = 30


def _kuerzlich_vorhanden(conn: sqlite3.Connection, spiel_id: int, typ: str, text: str) -> bool:
    fenster = _DEDUP_FENSTER_MINUTEN.get(typ, _DEDUP_STANDARD_MINUTEN)
    grenze = iso_utc(jetzt_utc() - timedelta(minutes=fenster))
    zeile = conn.execute(
        "SELECT 1 FROM ereignis WHERE spiel_id = ? AND typ = ? AND text = ?"
        " AND quelle = 'api' AND erstellt_utc >= ? LIMIT 1",
        (spiel_id, typ, text, grenze),
    ).fetchone()
    return zeile is not None


def deltas_verarbeiten(
    conn: sqlite3.Connection,
    *,
    spiel_id: int,
    deltas: dict[str, tuple],
    minute: int | None = None,
) -> list[int]:
    """Leitet Ticker-Einträge aus einem Spiel-Upsert ab; liefert neue ereignis-IDs.

    Muss innerhalb der offenen Schreib-Transaktion des Syncs laufen, damit
    Spielstand und Ereignis konsistent sichtbar werden.
    """
    spiel = conn.execute(
        "SELECT s.tore_heim, s.tore_gast, s.status, s.heim_team_id, s.gast_team_id,"
        " th.name AS heim_name, tg.name AS gast_name"
        " FROM spiel s LEFT JOIN team th ON th.id = s.heim_team_id"
        " LEFT JOIN team tg ON tg.id = s.gast_team_id WHERE s.id = ?",
        (spiel_id,),
    ).fetchone()
    if spiel is None:
        return []
    neue_ids: list[int] = []

    status_delta = deltas.get("status")
    if status_delta:
        eintrag = _STATUS_EREIGNIS.get((status_delta[0], status_delta[1]))
        if eintrag and not _kuerzlich_vorhanden(conn, spiel_id, eintrag[0], eintrag[1]):
            typ, text = eintrag
            neue_ids.append(
                ereignis_anlegen(conn, spiel_id=spiel_id, typ=typ, minute=minute, text=text)
            )

    stand = f"{spiel['tore_heim']}:{spiel['tore_gast']}"
    for feld, team_feld, team_name in (
        ("tore_heim", "heim_team_id", spiel["heim_name"]),
        ("tore_gast", "gast_team_id", spiel["gast_name"]),
    ):
        delta = deltas.get(feld)
        if not delta:
            continue
        alt, neu = delta
        # Erstes Update nach Wiederanlauf (None -> n) ist kein Live-Tor.
        if alt is None or neu is None:
            continue
        if neu > alt:
            text = f"Tor für {team_name or '?'} — Stand {stand}"
            # Dedup-Prüfung VOR der Schleife: ein Doppelpack erzeugt bewusst
            # zwei gleichlautende Einträge in derselben Transaktion.
            if not _kuerzlich_vorhanden(conn, spiel_id, "tor", text):
                for _ in range(neu - alt):
                    neue_ids.append(
                        ereignis_anlegen(
                            conn,
                            spiel_id=spiel_id,
                            typ="tor",
                            minute=minute,
                            team_id=spiel[team_feld],
                            text=text,
                        )
                    )
        else:
            text = f"Korrektur: Stand {stand}"
            if not _kuerzlich_vorhanden(conn, spiel_id, "var", text):
                neue_ids.append(
                    ereignis_anlegen(
                        conn,
                        spiel_id=spiel_id,
                        typ="var",
                        minute=minute,
                        team_id=spiel[team_feld],
                        text=text,
                    )
                )
    return neue_ids


def sync_delta_publizieren(
    conn: sqlite3.Connection, spiel_id: int, deltas: dict[str, tuple], ereignis_ids: list[int]
) -> None:
    """Pusht Score/Status/Ereignisse eines Spiels an alle SSE-Clients."""
    spiel = conn.execute(
        "SELECT id, status, tore_heim, tore_gast, anstoss_utc FROM spiel WHERE id = ?",
        (spiel_id,),
    ).fetchone()
    if spiel is None:
        return
    if "status" in deltas:
        broker.publish("status", dict(spiel))
    if "tore_heim" in deltas or "tore_gast" in deltas:
        broker.publish("score", dict(spiel))
    for ereignis_id in ereignis_ids:
        daten = ereignis_json(conn, ereignis_id)
        if daten:
            broker.publish("ereignis", daten)
