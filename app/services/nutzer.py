"""Nutzerverwaltung, Login und Registrierung inkl. Brute-Force-Schutz (SPEC 8.1)."""
from __future__ import annotations

import hmac
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from .. import db
from ..config import Einstellungen
from ..security import pin_hashen, pin_pruefen, session_token_erzeugen, token_hashen
from ..zeit import iso_utc, jetzt_iso, jetzt_utc

# Dummy-Hash, damit der Login bei unbekanntem Namen gleich lange rechnet
# wie bei bekanntem Namen (kein Timing-Orakel auf Nutzer-Existenz).
_DUMMY_HASH = pin_hashen("platzhalter-pin")


class LoginGesperrt(Exception):
    def __init__(self, gesperrt_bis_utc: str):
        super().__init__(f"Login gesperrt bis {gesperrt_bis_utc}")
        self.gesperrt_bis_utc = gesperrt_bis_utc


class LoginFehlgeschlagen(Exception):
    pass


class RegistrierungAbgelehnt(Exception):
    pass


@dataclass(frozen=True)
class LoginErgebnis:
    nutzer_id: int
    anzeigename: str
    rolle: str
    ki_freigeschaltet: bool
    session_token: str
    ablauf_utc: str


def nutzer_anlegen(
    conn: sqlite3.Connection,
    *,
    anzeigename: str,
    pin: str,
    rolle: str = "mitglied",
    akteur: str = "admin",
) -> int:
    anzeigename = anzeigename.strip()
    if not anzeigename:
        raise ValueError("Anzeigename darf nicht leer sein")
    if len(anzeigename) > 50:
        raise ValueError("Anzeigename ist zu lang (maximal 50 Zeichen)")
    if rolle not in ("admin", "mitglied", "ki"):
        raise ValueError(f"Unbekannte Rolle: {rolle}")
    pin_validieren(pin)
    jetzt = jetzt_iso()
    with db.schreib_transaktion(conn):
        try:
            cursor = conn.execute(
                "INSERT INTO nutzer (anzeigename, pin_hash, rolle, erstellt_utc) VALUES (?, ?, ?, ?)",
                (anzeigename, pin_hashen(pin), rolle, jetzt),
            )
        except sqlite3.IntegrityError as fehler:
            raise ValueError(f"Anzeigename '{anzeigename}' ist bereits vergeben") from fehler
        nutzer_id = cursor.lastrowid
        db.change_log_eintrag(
            conn,
            entitaet="nutzer",
            entitaet_id=nutzer_id,
            feld="anzeigename",
            alt_wert=None,
            neu_wert=anzeigename,
            quelle="admin",
            akteur=akteur,
            zeitpunkt_utc=jetzt,
        )
    return nutzer_id


def pin_validieren(pin: str) -> None:
    if not (4 <= len(pin) <= 32):
        raise ValueError("PIN muss zwischen 4 und 32 Zeichen lang sein")
    if any(zeichen.isspace() for zeichen in pin):
        raise ValueError("PIN darf keine Leerzeichen enthalten")


def pin_aendern(conn: sqlite3.Connection, *, nutzer_id: int, neue_pin: str, akteur: str) -> None:
    pin_validieren(neue_pin)
    jetzt = jetzt_iso()
    with db.schreib_transaktion(conn):
        cursor = conn.execute(
            "UPDATE nutzer SET pin_hash = ? WHERE id = ?", (pin_hashen(neue_pin), nutzer_id)
        )
        if cursor.rowcount == 0:
            raise ValueError(f"Nutzer {nutzer_id} existiert nicht")
        # Bestehende Sessions beenden — alte Anmeldungen sollen nach PIN-Wechsel enden.
        conn.execute("DELETE FROM sitzung WHERE nutzer_id = ?", (nutzer_id,))
        db.change_log_eintrag(
            conn,
            entitaet="nutzer",
            entitaet_id=nutzer_id,
            feld="pin_hash",
            alt_wert="(geheim)",
            neu_wert="(geheim)",
            quelle="admin",
            akteur=akteur,
            zeitpunkt_utc=jetzt,
        )


def _sperre_pruefen(conn: sqlite3.Connection, schluessel: str, jetzt: str) -> str | None:
    zeile = conn.execute(
        "SELECT gesperrt_bis_utc FROM login_sperre WHERE schluessel = ?", (schluessel,)
    ).fetchone()
    if zeile and zeile["gesperrt_bis_utc"] and zeile["gesperrt_bis_utc"] > jetzt:
        return zeile["gesperrt_bis_utc"]
    return None


def _fehlversuch_registrieren(
    conn: sqlite3.Connection, schluessel: str, einstellungen: Einstellungen
) -> None:
    jetzt_dt = jetzt_utc()
    jetzt = iso_utc(jetzt_dt)
    fenster_start = iso_utc(jetzt_dt - timedelta(minutes=einstellungen.login_sperre_minuten))
    with db.schreib_transaktion(conn):
        zeile = conn.execute(
            "SELECT fehlversuche, letzter_fehlversuch_utc FROM login_sperre WHERE schluessel = ?",
            (schluessel,),
        ).fetchone()
        if zeile is None or (zeile["letzter_fehlversuch_utc"] or "") < fenster_start:
            fehlversuche = 1
        else:
            fehlversuche = zeile["fehlversuche"] + 1
        gesperrt_bis = None
        if fehlversuche >= einstellungen.login_max_fehlversuche:
            gesperrt_bis = iso_utc(
                jetzt_dt + timedelta(minutes=einstellungen.login_sperre_minuten)
            )
        conn.execute(
            "INSERT INTO login_sperre (schluessel, fehlversuche, letzter_fehlversuch_utc, gesperrt_bis_utc)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(schluessel) DO UPDATE SET fehlversuche = excluded.fehlversuche,"
            " letzter_fehlversuch_utc = excluded.letzter_fehlversuch_utc,"
            " gesperrt_bis_utc = excluded.gesperrt_bis_utc",
            (schluessel, fehlversuche, jetzt, gesperrt_bis),
        )


def _sperren_aufheben(conn: sqlite3.Connection, schluessel_liste: list[str]) -> None:
    with db.schreib_transaktion(conn):
        for schluessel in schluessel_liste:
            conn.execute("DELETE FROM login_sperre WHERE schluessel = ?", (schluessel,))


def anmelden(
    conn: sqlite3.Connection,
    *,
    anzeigename: str,
    pin: str,
    client_ip: str,
    einstellungen: Einstellungen,
) -> LoginErgebnis:
    """Prüft Name+PIN und legt bei Erfolg eine Session an.

    Wirft LoginGesperrt (zu viele Fehlversuche) oder LoginFehlgeschlagen
    (bewusst ohne Unterscheidung zwischen unbekanntem Namen und falscher PIN).
    """
    anzeigename = anzeigename.strip()
    jetzt_dt = jetzt_utc()
    jetzt = iso_utc(jetzt_dt)
    name_schluessel = f"name:{anzeigename.lower()}"
    ip_schluessel = f"ip:{client_ip}"

    # Nur die IP-Sperre blockiert VOR der PIN-Prüfung (drosselt Brute-Force/Flutung
    # je Quelle). Die Namens-Sperre darf einen rechtmäßigen Login mit korrekter PIN
    # bewusst NICHT verhindern — sonst könnte ein Fremder ein bekanntes Konto (z. B.
    # den Admin) durch absichtliche Fehlversuche gezielt aussperren (DoS). Der
    # Namens-Zähler läuft weiter mit (Telemetrie, Reset bei Erfolg), sperrt aber nicht.
    gesperrt_bis = _sperre_pruefen(conn, ip_schluessel, jetzt)
    if gesperrt_bis:
        raise LoginGesperrt(gesperrt_bis)

    zeile = conn.execute(
        "SELECT id, anzeigename, pin_hash, rolle, ki_freigeschaltet FROM nutzer"
        " WHERE anzeigename = ?",
        (anzeigename,),
    ).fetchone()
    pin_korrekt = pin_pruefen(pin, zeile["pin_hash"] if zeile else _DUMMY_HASH)
    if zeile is None or not pin_korrekt:
        for schluessel in (name_schluessel, ip_schluessel):
            _fehlversuch_registrieren(conn, schluessel, einstellungen)
        raise LoginFehlgeschlagen()

    _sperren_aufheben(conn, [name_schluessel, ip_schluessel])

    token = session_token_erzeugen()
    ablauf = iso_utc(jetzt_dt + timedelta(days=einstellungen.session_dauer_tage))
    with db.schreib_transaktion(conn):
        # Abgelaufene Sessions bei dieser Gelegenheit aufräumen.
        conn.execute("DELETE FROM sitzung WHERE ablauf_utc <= ?", (jetzt,))
        conn.execute(
            "INSERT INTO sitzung (token_hash, nutzer_id, erstellt_utc, ablauf_utc) VALUES (?, ?, ?, ?)",
            (token_hashen(token), zeile["id"], jetzt, ablauf),
        )
    return LoginErgebnis(
        nutzer_id=zeile["id"],
        anzeigename=zeile["anzeigename"],
        rolle=zeile["rolle"],
        ki_freigeschaltet=bool(zeile["ki_freigeschaltet"]),
        session_token=token,
        ablauf_utc=ablauf,
    )


def registrieren(
    conn: sqlite3.Connection,
    *,
    anzeigename: str,
    pin: str,
    gruppen_passwort: str,
    client_ip: str,
    einstellungen: Einstellungen,
) -> LoginErgebnis:
    """Selbst-Registrierung mit Gruppen-Passwort; legt das Konto an und meldet es an.

    Wirft RegistrierungAbgelehnt (deaktiviert / falsches Gruppen-Passwort),
    LoginGesperrt (zu viele Fehlversuche dieser IP) oder ValueError (Validierung).
    """
    if not einstellungen.registrierung_passwort:
        raise RegistrierungAbgelehnt(
            "Die Registrierung ist deaktiviert — bitte beim Admin der Tipprunde melden."
        )
    schluessel = f"reg-ip:{client_ip}"
    jetzt = jetzt_iso()
    # Auch die Login-Sperre der IP vorab prüfen: sonst würde das Konto angelegt,
    # aber die anschließende Anmeldung scheitern (Konto existiert, Nutzer rät warum).
    for sperr_schluessel in (schluessel, f"ip:{client_ip}"):
        gesperrt_bis = _sperre_pruefen(conn, sperr_schluessel, jetzt)
        if gesperrt_bis:
            raise LoginGesperrt(gesperrt_bis)
    passt = hmac.compare_digest(
        gruppen_passwort.encode("utf-8"),
        einstellungen.registrierung_passwort.encode("utf-8"),
    )
    if not passt:
        _fehlversuch_registrieren(conn, schluessel, einstellungen)
        raise RegistrierungAbgelehnt("Das Gruppen-Passwort ist falsch.")
    nutzer_anlegen(
        conn,
        anzeigename=anzeigename,
        pin=pin,
        rolle="mitglied",
        akteur="registrierung",
    )
    _sperren_aufheben(conn, [schluessel])
    return anmelden(
        conn,
        anzeigename=anzeigename,
        pin=pin,
        client_ip=client_ip,
        einstellungen=einstellungen,
    )


def abmelden(conn: sqlite3.Connection, session_token: str) -> None:
    with db.schreib_transaktion(conn):
        conn.execute("DELETE FROM sitzung WHERE token_hash = ?", (token_hashen(session_token),))
