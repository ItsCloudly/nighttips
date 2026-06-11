"""Konfiguration aus Umgebungsvariablen, optional ergänzt durch eine .env-Datei."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def lade_env_datei(pfad: Path) -> None:
    """Setzt KEY=WERT-Zeilen aus der Datei, ohne echte Umgebungsvariablen zu überschreiben."""
    if not pfad.is_file():
        return
    for zeile in pfad.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, _, wert = zeile.partition("=")
        schluessel = schluessel.strip()
        wert = wert.strip()
        if len(wert) >= 2 and wert[0] == wert[-1] and wert[0] in "\"'":
            wert = wert[1:-1]
        if schluessel:
            os.environ.setdefault(schluessel, wert)


def _bool(name: str, standard: bool) -> bool:
    wert = os.environ.get(name)
    if wert is None or wert.strip() == "":
        return standard
    return wert.strip().lower() in ("1", "true", "ja", "yes", "on")


def _int(name: str, standard: int) -> int:
    wert = os.environ.get(name)
    if wert is None or wert.strip() == "":
        return standard
    try:
        return int(wert.strip())
    except ValueError:
        return standard


@dataclass(frozen=True)
class Einstellungen:
    db_pfad: Path
    cookie_secure: bool
    session_dauer_tage: int
    login_max_fehlversuche: int
    login_sperre_minuten: int
    api_provider: str
    api_token: str
    api_basis_url: str
    api_wettbewerb: str
    sync_intervall_minuten: int
    ko_wertung_nach_120: bool
    # Web Push (SPEC 5.5): VAPID-Schlüsselpaar; leer = Push deaktiviert.
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:admin@example.invalid"
    tipp_erinnerung_minuten: int = 120
    anpfiff_erinnerung_minuten: int = 30
    # Wettquoten via The Odds API (v0.1.1): leer = Quoten-Feature aus.
    quoten_token: str = ""
    quoten_basis_url: str = "https://api.the-odds-api.com/v4"
    quoten_sport: str = "soccer_fifa_world_cup"
    quoten_buchmacher: str = "tipico_de"
    # Gruppen-Passwort für die Selbst-Registrierung; leer = Registrierung deaktiviert.
    registrierung_passwort: str = ""
    # Rate-Limit (pro IP, gleitendes 60-s-Fenster) VOR der scrypt-Prüfung: wehrt
    # unauthentifizierte Login-/Registrierungs-Fluten ab (CPU-DoS-Schutz).
    login_rate_pro_minute: int = 12
    registrierung_rate_pro_minute: int = 6


def lade_einstellungen() -> Einstellungen:
    lade_env_datei(BASE_DIR / ".env")
    db_pfad = Path(os.environ.get("WM26_DB_PFAD") or BASE_DIR / "daten" / "wm26.db")
    return Einstellungen(
        db_pfad=db_pfad,
        cookie_secure=_bool("WM26_COOKIE_SECURE", True),
        session_dauer_tage=_int("WM26_SESSION_DAUER_TAGE", 30),
        login_max_fehlversuche=_int("WM26_LOGIN_MAX_FEHLVERSUCHE", 5),
        login_sperre_minuten=_int("WM26_LOGIN_SPERRE_MINUTEN", 15),
        api_provider=os.environ.get("WM26_API_PROVIDER", "football-data"),
        api_token=os.environ.get("WM26_API_TOKEN", ""),
        api_basis_url=os.environ.get("WM26_API_BASIS_URL", "https://api.football-data.org/v4"),
        api_wettbewerb=os.environ.get("WM26_API_WETTBEWERB", "WC"),
        sync_intervall_minuten=_int("WM26_SYNC_INTERVALL_MINUTEN", 60),
        ko_wertung_nach_120=os.environ.get("WM26_KO_WERTUNG", "120").strip() != "90",
        vapid_private_key=os.environ.get("WM26_VAPID_PRIVATE_KEY", ""),
        vapid_public_key=os.environ.get("WM26_VAPID_PUBLIC_KEY", ""),
        vapid_subject=os.environ.get("WM26_VAPID_SUBJECT", "mailto:admin@example.invalid"),
        tipp_erinnerung_minuten=_int("WM26_TIPP_ERINNERUNG_MINUTEN", 120),
        anpfiff_erinnerung_minuten=_int("WM26_ANPFIFF_ERINNERUNG_MINUTEN", 30),
        quoten_token=os.environ.get("WM26_QUOTEN_TOKEN", ""),
        quoten_basis_url=os.environ.get("WM26_QUOTEN_BASIS_URL", "https://api.the-odds-api.com/v4"),
        quoten_sport=os.environ.get("WM26_QUOTEN_SPORT", "soccer_fifa_world_cup"),
        quoten_buchmacher=os.environ.get("WM26_QUOTEN_BUCHMACHER", "tipico_de"),
        registrierung_passwort=os.environ.get("WM26_REGISTRIERUNG_PASSWORT", ""),
        login_rate_pro_minute=_int("WM26_LOGIN_RATE_PRO_MINUTE", 12),
        registrierung_rate_pro_minute=_int("WM26_REGISTRIERUNG_RATE_PRO_MINUTE", 6),
    )
