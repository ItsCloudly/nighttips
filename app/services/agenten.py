"""Agenten-Schnittstelle (SPEC 7.2): Token-Verwaltung, Scopes, Rate-Limit.

Tokens werden nur als SHA-256-Hash gespeichert; der Klartext erscheint genau
einmal beim Erzeugen. Scopes: `read` (Exporte) und `write_analysis` (Analysen,
KI-Tipp, Sichtungs-Vorschläge). Das Rate-Limit ist in-process (ein Prozess auf
dem Rock64) und drosselt je Token.
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque

from .. import db
from ..security import token_hashen
from ..zeit import jetzt_iso

SCOPES = ("read", "write_analysis")

# Export-Endpunkte: großzügig für manuelle Agenten-Sessions, eng genug gegen Amok.
_RATE_LIMIT_ANFRAGEN = 60
_RATE_LIMIT_FENSTER_SEKUNDEN = 60

_rate_lock = threading.Lock()
_rate_fenster: dict[int, deque[float]] = defaultdict(deque)


class AgentFehler(Exception):
    pass


def token_erzeugen(conn: sqlite3.Connection, *, name: str, scopes: list[str]) -> str:
    """Legt ein Token an und liefert den Klartext (einmalige Anzeige)."""
    unbekannt = [scope for scope in scopes if scope not in SCOPES]
    if unbekannt or not scopes:
        raise AgentFehler(f"Unbekannte Scopes: {unbekannt or 'keine angegeben'}")
    klartext = f"wm26_{secrets.token_urlsafe(32)}"
    with db.schreib_transaktion(conn):
        try:
            conn.execute(
                "INSERT INTO agent_token (name, token_hash, scopes, erstellt_utc)"
                " VALUES (?, ?, ?, ?)",
                (name.strip(), token_hashen(klartext), ",".join(scopes), jetzt_iso()),
            )
        except sqlite3.IntegrityError:
            raise AgentFehler(f"Token-Name '{name}' existiert bereits") from None
    return klartext


def token_widerrufen(conn: sqlite3.Connection, token_id: int) -> bool:
    with db.schreib_transaktion(conn):
        return (
            conn.execute(
                "UPDATE agent_token SET widerrufen_utc = ? WHERE id = ? AND widerrufen_utc IS NULL",
                (jetzt_iso(), token_id),
            ).rowcount
            > 0
        )


def token_pruefen(conn: sqlite3.Connection, klartext: str, *, scope: str) -> sqlite3.Row:
    """Liefert die Token-Zeile oder wirft AgentFehler (ungültig/widerrufen/Scope/Rate)."""
    zeile = conn.execute(
        "SELECT * FROM agent_token WHERE token_hash = ? AND widerrufen_utc IS NULL",
        (token_hashen(klartext),),
    ).fetchone()
    if zeile is None:
        raise AgentFehler("Token ungültig oder widerrufen")
    if scope not in zeile["scopes"].split(","):
        raise AgentFehler(f"Token hat den Scope '{scope}' nicht")
    _rate_limit_pruefen(zeile["id"])
    return zeile


def _rate_limit_pruefen(token_id: int) -> None:
    jetzt = time.monotonic()
    with _rate_lock:
        fenster = _rate_fenster[token_id]
        while fenster and fenster[0] < jetzt - _RATE_LIMIT_FENSTER_SEKUNDEN:
            fenster.popleft()
        if len(fenster) >= _RATE_LIMIT_ANFRAGEN:
            raise AgentFehler("Rate-Limit erreicht — bitte kurz warten")
        fenster.append(jetzt)


def pseudonyme(conn: sqlite3.Connection) -> dict[int, str]:
    """Stabile Pseudonyme je Nutzer (SPEC 8.3): 'Spieler 1..n' nach Anlage-Reihenfolge."""
    zeilen = conn.execute("SELECT id, rolle FROM nutzer ORDER BY id").fetchall()
    ergebnis: dict[int, str] = {}
    laufnummer = 0
    for zeile in zeilen:
        if zeile["rolle"] == "ki":
            ergebnis[zeile["id"]] = "KI-Tipper"
        else:
            laufnummer += 1
            ergebnis[zeile["id"]] = f"Spieler {laufnummer}"
    return ergebnis


def ki_nutzer(conn: sqlite3.Connection) -> sqlite3.Row:
    zeile = conn.execute("SELECT * FROM nutzer WHERE rolle = 'ki' ORDER BY id LIMIT 1").fetchone()
    if zeile is None:
        raise AgentFehler("Kein KI-Tipper-Nutzer angelegt (Rolle 'ki')")
    return zeile


def analyse_anlegen(
    conn: sqlite3.Connection,
    *,
    spiel_id: int,
    typ: str,
    inhalt_markdown: str,
    struktur_json: str | None,
    agent_name: str,
) -> dict:
    spiel = conn.execute("SELECT id FROM spiel WHERE id = ?", (spiel_id,)).fetchone()
    if spiel is None:
        raise AgentFehler(f"Spiel {spiel_id} existiert nicht")
    with db.schreib_transaktion(conn):
        version = (
            conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM ki_analyse"
                " WHERE spiel_id = ? AND typ = ?",
                (spiel_id, typ),
            ).fetchone()["v"]
        )
        cursor = conn.execute(
            "INSERT INTO ki_analyse (spiel_id, typ, inhalt_markdown, struktur_json,"
            " agent_name, erstellt_utc, version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (spiel_id, typ, inhalt_markdown, struktur_json, agent_name, jetzt_iso(), version),
        )
        db.change_log_eintrag(
            conn,
            entitaet="ki_analyse",
            entitaet_id=cursor.lastrowid,
            feld="erstellt",
            alt_wert=None,
            neu_wert=f"{typ} v{version} für Spiel {spiel_id}",
            quelle="agent",
            akteur=agent_name,
            zeitpunkt_utc=jetzt_iso(),
        )
    return {"id": cursor.lastrowid, "spiel_id": spiel_id, "typ": typ, "version": version}


def neueste_analysen(conn: sqlite3.Connection, spiel_id: int) -> dict[str, dict | None]:
    """Jeweils die neueste Prognose und Nachanalyse eines Spiels (für die Lupe)."""
    ergebnis: dict[str, dict | None] = {"prognose": None, "nachanalyse": None}
    for typ in ergebnis:
        zeile = conn.execute(
            "SELECT typ, inhalt_markdown, struktur_json, agent_name, erstellt_utc, version"
            " FROM ki_analyse WHERE spiel_id = ? AND typ = ?"
            " ORDER BY version DESC LIMIT 1",
            (spiel_id, typ),
        ).fetchone()
        ergebnis[typ] = dict(zeile) if zeile else None
    return ergebnis
