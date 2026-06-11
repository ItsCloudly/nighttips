"""Login, Logout und Sitzungs-Info (SPEC 8.1)."""
from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import db, ratelimit
from ..abhaengigkeiten import (
    SESSION_COOKIE,
    aktueller_nutzer,
    get_db,
    get_einstellungen,
)
from ..config import Einstellungen
from ..modelle import ErinnerungsEinstellung, LoginDaten, NutzerInfo, RegistrierungsDaten
from ..services import nutzer as nutzer_service

router = APIRouter(prefix="/api", tags=["auth"])


def _client_ip(request: Request) -> str:
    # uvicorn läuft mit --proxy-headers hinter dem HTTPS-Proxy (siehe docs/BETRIEB.md),
    # request.client enthält dann bereits die echte Client-Adresse.
    return request.client.host if request.client else "unbekannt"


def _rate_limit(client_ip: str, *, bereich: str, limit: int) -> None:
    """Wehrt unauthentifizierte Fluten je IP ab, BEVOR scrypt läuft (CPU-DoS-Schutz)."""
    if not ratelimit.erlaubt(f"{bereich}:{client_ip}", limit=limit, fenster_sekunden=60):
        raise HTTPException(
            status_code=429,
            detail="Zu viele Anfragen. Bitte einen Moment warten.",
            headers={"Retry-After": "60"},
        )


def _session_setzen(
    response: Response, ergebnis: nutzer_service.LoginErgebnis, einstellungen: Einstellungen
) -> NutzerInfo:
    response.set_cookie(
        SESSION_COOKIE,
        ergebnis.session_token,
        max_age=einstellungen.session_dauer_tage * 24 * 3600,
        httponly=True,
        secure=einstellungen.cookie_secure,
        samesite="lax",
        path="/",
    )
    return NutzerInfo(
        id=ergebnis.nutzer_id,
        anzeigename=ergebnis.anzeigename,
        rolle=ergebnis.rolle,
        ki_freigeschaltet=ergebnis.ki_freigeschaltet,
    )


@router.post("/login", response_model=NutzerInfo)
def login(
    daten: LoginDaten,
    request: Request,
    response: Response,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> NutzerInfo:
    client_ip = _client_ip(request)
    _rate_limit(client_ip, bereich="login", limit=einstellungen.login_rate_pro_minute)
    try:
        ergebnis = nutzer_service.anmelden(
            conn,
            anzeigename=daten.anzeigename,
            pin=daten.pin,
            client_ip=client_ip,
            einstellungen=einstellungen,
        )
    except nutzer_service.LoginGesperrt as fehler:
        raise HTTPException(
            status_code=429,
            detail="Zu viele Fehlversuche. Bitte später erneut versuchen.",
            headers={"Retry-After": str(einstellungen.login_sperre_minuten * 60)},
        ) from fehler
    except nutzer_service.LoginFehlgeschlagen:
        raise HTTPException(status_code=401, detail="Name oder PIN ist falsch.") from None

    return _session_setzen(response, ergebnis, einstellungen)


@router.post("/registrieren", response_model=NutzerInfo, status_code=201)
def registrieren(
    daten: RegistrierungsDaten,
    request: Request,
    response: Response,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    einstellungen: Annotated[Einstellungen, Depends(get_einstellungen)],
) -> NutzerInfo:
    """Selbst-Registrierung: braucht das Gruppen-Passwort der Tipprunde (SPEC 8.1)."""
    client_ip = _client_ip(request)
    _rate_limit(client_ip, bereich="reg", limit=einstellungen.registrierung_rate_pro_minute)
    try:
        ergebnis = nutzer_service.registrieren(
            conn,
            anzeigename=daten.anzeigename,
            pin=daten.pin,
            gruppen_passwort=daten.gruppen_passwort,
            client_ip=client_ip,
            einstellungen=einstellungen,
        )
    except nutzer_service.LoginGesperrt as fehler:
        raise HTTPException(
            status_code=429,
            detail="Zu viele Fehlversuche. Bitte später erneut versuchen.",
            headers={"Retry-After": str(einstellungen.login_sperre_minuten * 60)},
        ) from fehler
    except nutzer_service.RegistrierungAbgelehnt as fehler:
        raise HTTPException(status_code=403, detail=str(fehler)) from None
    except ValueError as fehler:
        raise HTTPException(status_code=409, detail=str(fehler)) from None

    return _session_setzen(response, ergebnis, einstellungen)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        nutzer_service.abmelden(conn, token)
    # delete_cookie auf der zurückgegebenen Response: Header einer injizierten
    # Response werden beim direkten Zurückgeben einer Response nicht übernommen.
    antwort = Response(status_code=204)
    antwort.delete_cookie(SESSION_COOKIE, path="/")
    return antwort


@router.get("/me", response_model=NutzerInfo)
def me(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
) -> NutzerInfo:
    return NutzerInfo(
        id=nutzer["id"],
        anzeigename=nutzer["anzeigename"],
        rolle=nutzer["rolle"],
        ki_freigeschaltet=bool(nutzer["ki_freigeschaltet"]),
        tipp_erinnerung_minuten=nutzer["tipp_erinnerung_minuten"],
        profilbild=nutzer["profilbild"],
    )


@router.patch("/me/einstellungen")
def einstellungen_aendern(
    daten: ErinnerungsEinstellung,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, int]:
    """Persönliche Vorlaufzeit der Tipp-Erinnerung setzen (0 = aus)."""
    with db.schreib_transaktion(conn):
        conn.execute(
            "UPDATE nutzer SET tipp_erinnerung_minuten = ? WHERE id = ?",
            (daten.tipp_erinnerung_minuten, nutzer["id"]),
        )
    return {"tipp_erinnerung_minuten": daten.tipp_erinnerung_minuten}
