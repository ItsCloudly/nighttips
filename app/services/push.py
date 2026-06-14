"""Web-Push-Versand (SPEC 5.5): VAPID-signierte Nachrichten an Abos.

Anlässe für Lieblings-Teams (v0.2): Anpfiff in N Minuten, Tor, Endstand.
Für alle Nutzer: Tipp-Erinnerung, wenn ein ungetipptes Spiel bald beginnt.
Doppelversand verhindert die Tabelle push_versand (Anlass + Bezug + Nutzer).

Ohne konfigurierte VAPID-Schlüssel ist der Versand ein No-op — die App
funktioniert vollständig ohne Push.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import timedelta

from .. import db
from ..config import Einstellungen
from ..zeit import iso_utc, jetzt_iso, jetzt_utc

logger = logging.getLogger("wm26.push")

ANLASS_ANPFIFF = "anpfiff"
ANLASS_TOR = "tor"
ANLASS_ENDSTAND = "endstand"
ANLASS_TIPP_ERINNERUNG = "tipp_erinnerung"
ANLASS_CHAT = "chat"  # v0.3: neue Gruppenchat-Nachricht


def aktiv(einstellungen: Einstellungen) -> bool:
    return bool(einstellungen.vapid_private_key and einstellungen.vapid_public_key)


def subscription_speichern(
    conn: sqlite3.Connection, *, nutzer_id: int, endpoint: str, p256dh: str, auth: str
) -> None:
    with db.schreib_transaktion(conn):
        conn.execute(
            "INSERT INTO push_subscription (nutzer_id, endpoint, p256dh, auth, erstellt_utc)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(endpoint) DO UPDATE SET"
            " nutzer_id = excluded.nutzer_id, p256dh = excluded.p256dh, auth = excluded.auth",
            (nutzer_id, endpoint, p256dh, auth, jetzt_iso()),
        )


def subscription_loeschen(conn: sqlite3.Connection, *, nutzer_id: int, endpoint: str) -> int:
    with db.schreib_transaktion(conn):
        return conn.execute(
            "DELETE FROM push_subscription WHERE nutzer_id = ? AND endpoint = ?",
            (nutzer_id, endpoint),
        ).rowcount


def _senden_an_abo(einstellungen: Einstellungen, abo: sqlite3.Row, payload: str) -> bool:
    """Sendet an ein Abo; False, wenn das Abo tot ist (404/410) und weg soll."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info={
                "endpoint": abo["endpoint"],
                "keys": {"p256dh": abo["p256dh"], "auth": abo["auth"]},
            },
            data=payload,
            vapid_private_key=einstellungen.vapid_private_key,
            vapid_claims={"sub": einstellungen.vapid_subject},
            timeout=10,
        )
        return True
    except WebPushException as fehler:
        status = getattr(fehler.response, "status_code", None)
        if status in (404, 410):
            return False
        logger.warning("Push an %s fehlgeschlagen: %s", abo["endpoint"][:40], fehler)
        return True
    except Exception:
        logger.exception("Push-Versand unerwartet fehlgeschlagen")
        return True


def senden(
    conn: sqlite3.Connection,
    einstellungen: Einstellungen,
    *,
    nutzer_ids: list[int],
    anlass: str,
    ref_id: int,
    titel: str,
    text: str,
    url: str = "/",
) -> int:
    """Versendet eine Nachricht an alle Geräte der Nutzer, höchstens 1x je Anlass+Bezug."""
    if not aktiv(einstellungen) or not nutzer_ids:
        return 0
    jetzt = jetzt_iso()
    empfaenger: list[int] = []
    with db.schreib_transaktion(conn):
        for nutzer_id in set(nutzer_ids):
            eingefuegt = conn.execute(
                "INSERT OR IGNORE INTO push_versand (anlass, ref_id, nutzer_id, gesendet_utc)"
                " VALUES (?, ?, ?, ?)",
                (anlass, ref_id, nutzer_id, jetzt),
            ).rowcount
            if eingefuegt:
                empfaenger.append(nutzer_id)
    if not empfaenger:
        return 0
    platzhalter = ",".join("?" for _ in empfaenger)
    abos = conn.execute(
        f"SELECT id, endpoint, p256dh, auth FROM push_subscription WHERE nutzer_id IN ({platzhalter})",
        empfaenger,
    ).fetchall()
    payload = json.dumps({"titel": titel, "text": text, "url": url}, ensure_ascii=False)
    gesendet = 0
    tote_abos: list[int] = []
    for abo in abos:
        if _senden_an_abo(einstellungen, abo, payload):
            gesendet += 1
        else:
            tote_abos.append(abo["id"])
    if tote_abos:
        with db.schreib_transaktion(conn):
            conn.executemany(
                "DELETE FROM push_subscription WHERE id = ?", [(i,) for i in tote_abos]
            )
    return gesendet


def _spiel_info(conn: sqlite3.Connection, spiel_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT s.id, s.anstoss_utc, s.status, s.tore_heim, s.tore_gast,"
        " s.heim_team_id, s.gast_team_id,"
        " COALESCE(th.name, '?') AS heim_name, COALESCE(tg.name, '?') AS gast_name"
        " FROM spiel s LEFT JOIN team th ON th.id = s.heim_team_id"
        " LEFT JOIN team tg ON tg.id = s.gast_team_id WHERE s.id = ?",
        (spiel_id,),
    ).fetchone()


def _pin_nutzer(
    conn: sqlite3.Connection, spiel: sqlite3.Row, *, pref: str | None = None
) -> list[int]:
    """Nutzer, die eines der beteiligten Teams als Favorit markiert haben.

    pref: optionaler nutzer-Schalter (feste Whitelist), der auf 1 stehen muss
    — z. B. 'push_team_tore' (v0.3). v0.2: Favoriten sind Teams; alte Spiel-Pins
    liegen zwar noch in der Tabelle, lösen aber bewusst keine Pushes mehr aus.
    """
    sql = (
        "SELECT DISTINCT n.id FROM pin p JOIN nutzer n ON n.id = p.nutzer_id"
        " WHERE p.typ = 'team' AND p.ref_id IN (?, ?)"
    )
    if pref == "push_team_tore":  # feste Whitelist, kein dynamischer Spaltenname
        sql += " AND n.push_team_tore = 1"
    zeilen = conn.execute(
        sql, (spiel["heim_team_id"] or -1, spiel["gast_team_id"] or -1)
    ).fetchall()
    return [zeile["id"] for zeile in zeilen]


def ereignis_pushen(
    conn: sqlite3.Connection,
    einstellungen: Einstellungen,
    *,
    spiel_id: int,
    deltas: dict[str, tuple],
) -> int:
    """Tor- und Endstand-Pushes für gepinnte Inhalte aus einem Sync-Delta."""
    if not aktiv(einstellungen):
        return 0
    spiel = _spiel_info(conn, spiel_id)
    if spiel is None:
        return 0
    # Tore & Endstand nur an Lieblingsteam-Pinner, die diese Pushes anhaben (v0.3)
    nutzer = _pin_nutzer(conn, spiel, pref="push_team_tore")
    if not nutzer:
        return 0
    paarung = f"{spiel['heim_name']} – {spiel['gast_name']}"
    stand = f"{spiel['tore_heim']}:{spiel['tore_gast']}"
    gesendet = 0
    status_delta = deltas.get("status")
    if status_delta and status_delta[1] == "beendet":
        gesendet += senden(
            conn, einstellungen, nutzer_ids=nutzer, anlass=ANLASS_ENDSTAND, ref_id=spiel_id,
            titel="Endstand", text=f"{paarung} {stand}", url=f"/#spiel-{spiel_id}",
        )
        return gesendet
    tor_delta = deltas.get("tore_heim") or deltas.get("tore_gast")
    if tor_delta and tor_delta[0] is not None and spiel["status"] in ("live", "halbzeit"):
        # ref_id mit Spielstand kodieren, damit jedes Tor genau einmal rausgeht.
        ref = spiel_id * 1000 + (spiel["tore_heim"] or 0) * 10 + (spiel["tore_gast"] or 0)
        gesendet += senden(
            conn, einstellungen, nutzer_ids=nutzer, anlass=ANLASS_TOR, ref_id=ref,
            titel=f"Tor! {stand}", text=paarung, url=f"/#spiel-{spiel_id}",
        )
    return gesendet


# Größte wählbare Vorlaufzeit der Tipp-Erinnerung (12 h) — bestimmt zugleich,
# wie weit erinnerungen_pruefen() nach vorn schaut.
MAX_TIPP_VORLAUF_MINUTEN = 720


def erinnerungen_pruefen(conn: sqlite3.Connection, einstellungen: Einstellungen) -> int:
    """Anpfiff-Erinnerung (gepinnt) und Tipp-Erinnerung für bald startende Spiele.

    Die Tipp-Erinnerung respektiert die persönliche Vorlaufzeit je Nutzer
    (nutzer.tipp_erinnerung_minuten): NULL = Server-Standard, 0 = abbestellt.
    Der Dedup über push_versand sorgt dafür, dass es bei einem Spiel trotzdem
    bei genau einer Erinnerung pro Nutzer bleibt.
    """
    if not aktiv(einstellungen):
        return 0
    jetzt = jetzt_utc()
    gesendet = 0
    max_vorlauf = max(einstellungen.tipp_erinnerung_minuten, MAX_TIPP_VORLAUF_MINUTEN)
    bald = conn.execute(
        "SELECT id FROM spiel WHERE status = 'geplant' AND anstoss_utc BETWEEN ? AND ?",
        (iso_utc(jetzt), iso_utc(jetzt + timedelta(minutes=max_vorlauf))),
    ).fetchall()
    for zeile in bald:
        spiel = _spiel_info(conn, zeile["id"])
        if spiel is None:
            continue
        paarung = f"{spiel['heim_name']} – {spiel['gast_name']}"
        anstoss = spiel["anstoss_utc"]
        # Anpfiff-Erinnerung für Lieblingsteams mit persönlichem Vorlauf (v0.3):
        # nutzer.anpfiff_erinnerung_minuten — NULL = Server-Standard, 0 = aus.
        anpfiff_kandidaten = conn.execute(
            "SELECT DISTINCT n.id, n.anpfiff_erinnerung_minuten FROM pin p"
            " JOIN nutzer n ON n.id = p.nutzer_id"
            " WHERE p.typ = 'team' AND p.ref_id IN (?, ?)",
            (spiel["heim_team_id"] or -1, spiel["gast_team_id"] or -1),
        ).fetchall()
        faellige_anpfiff = []
        for person in anpfiff_kandidaten:
            vorlauf = person["anpfiff_erinnerung_minuten"]
            if vorlauf is None:
                vorlauf = einstellungen.anpfiff_erinnerung_minuten
            if vorlauf <= 0:
                continue
            if anstoss <= iso_utc(jetzt + timedelta(minutes=vorlauf)):
                faellige_anpfiff.append(person["id"])
        if faellige_anpfiff:
            gesendet += senden(
                conn, einstellungen, nutzer_ids=faellige_anpfiff, anlass=ANLASS_ANPFIFF,
                ref_id=spiel["id"], titel="Gleich Anpfiff",
                text=paarung, url=f"/#spiel-{spiel['id']}",
            )
        # Tipp-Erinnerung an alle ohne Tipp (Nicht-KI), deren persönliches
        # Fenster den Anstoß schon erreicht hat
        ohne_tipp = conn.execute(
            "SELECT n.id, n.tipp_erinnerung_minuten FROM nutzer n"
            " WHERE n.rolle != 'ki' AND NOT EXISTS"
            " (SELECT 1 FROM tipp t WHERE t.nutzer_id = n.id AND t.spiel_id = ?)",
            (spiel["id"],),
        ).fetchall()
        faellig = []
        for person in ohne_tipp:
            vorlauf = person["tipp_erinnerung_minuten"]
            if vorlauf is None:
                vorlauf = einstellungen.tipp_erinnerung_minuten
            if vorlauf <= 0:
                continue
            if anstoss <= iso_utc(jetzt + timedelta(minutes=vorlauf)):
                faellig.append(person["id"])
        if faellig:
            gesendet += senden(
                conn, einstellungen,
                nutzer_ids=faellig,
                anlass=ANLASS_TIPP_ERINNERUNG, ref_id=spiel["id"],
                titel="Noch nicht getippt!",
                text=f"{paarung} startet bald — jetzt noch tippen.",
                url=f"/#spiel-{spiel['id']}",
            )
    return gesendet


def chat_benachrichtigen(
    einstellungen: Einstellungen, *, nachricht_id: int, autor_id: int
) -> int:
    """Push für eine neue Gruppenchat-Nachricht (v0.3) an alle mit aktiviertem
    Chat-Push — außer den Autor selbst.

    Läuft als Hintergrund-Task mit EIGENER DB-Verbindung (der Request-Connection
    ist nach der Antwort längst zu); Fehler bleiben für den Chat folgenlos.
    Dedup über push_versand (Anlass 'chat' + nachricht_id) ⇒ je Nachricht
    höchstens eine Meldung pro Empfänger.
    """
    if not aktiv(einstellungen):
        return 0
    try:
        conn = db.verbinden(einstellungen.db_pfad)
    except Exception:
        logger.exception("Chat-Push: DB nicht erreichbar")
        return 0
    try:
        nachricht = conn.execute(
            "SELECT n.inhalt, nu.anzeigename FROM nachricht n"
            " JOIN nutzer nu ON nu.id = n.nutzer_id WHERE n.id = ?",
            (nachricht_id,),
        ).fetchone()
        if nachricht is None:
            return 0
        empfaenger = [
            zeile["id"]
            for zeile in conn.execute(
                "SELECT id FROM nutzer WHERE push_chat = 1 AND id != ?", (autor_id,)
            ).fetchall()
        ]
        if not empfaenger:
            return 0
        text = nachricht["inhalt"]
        if len(text) > 120:
            text = text[:119] + "…"
        return senden(
            conn,
            einstellungen,
            nutzer_ids=empfaenger,
            anlass=ANLASS_CHAT,
            ref_id=nachricht_id,
            titel=f"💬 {nachricht['anzeigename']}",
            text=text,
            url="/#chat",
        )
    except Exception:
        logger.exception("Chat-Push fehlgeschlagen")
        return 0
    finally:
        conn.close()
