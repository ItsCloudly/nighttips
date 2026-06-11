"""Tests zur Sicherheits-Härtung:
Rate-Limit vor scrypt, Namens-Sperre blockt korrekte PIN nicht (DoS-Fix),
PIN-Mindestlänge, Session-Invalidierung bei PIN-Wechsel, HSTS-Header."""
from __future__ import annotations

import dataclasses
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import ratelimit
from app.main import create_app
from app.services import nutzer as nutzer_service


# ---------- Rate-Limiter (Einheit) ----------


def test_ratelimit_erlaubt_bis_limit_dann_429():
    ratelimit.zuruecksetzen()
    schluessel = "login:test"
    assert all(ratelimit.erlaubt(schluessel, limit=3, fenster_sekunden=60) for _ in range(3))
    # 4. Treffer im selben Fenster wird abgelehnt
    assert ratelimit.erlaubt(schluessel, limit=3, fenster_sekunden=60) is False
    # Anderer Schlüssel (andere IP) ist unabhängig
    assert ratelimit.erlaubt("login:andere", limit=3, fenster_sekunden=60) is True


def test_ratelimit_limit_null_deaktiviert():
    ratelimit.zuruecksetzen()
    assert all(ratelimit.erlaubt("x", limit=0, fenster_sekunden=60) for _ in range(50))


# ---------- Rate-Limit am Login-Endpunkt (vor scrypt) ----------


@pytest.fixture
def gedrosselter_client(einstellungen) -> Iterator[TestClient]:
    # Rate-Limit niedrig, Lockout hoch — so greift im Test wirklich der Rate-Limiter
    # (und nicht die Fehlversuch-Sperre) als erste Schranke.
    knapp = dataclasses.replace(
        einstellungen, login_rate_pro_minute=2, login_max_fehlversuche=99
    )
    app = create_app(knapp)
    with TestClient(app) as client:
        yield client


def test_login_rate_limit_greift_vor_lockout(gedrosselter_client):
    ratelimit.zuruecksetzen()
    # 2 Anfragen sind erlaubt (401 falsche Daten), die 3. wird vom Rate-Limit abgewiesen
    for _ in range(2):
        antwort = gedrosselter_client.post(
            "/api/login", json={"anzeigename": "Wer", "pin": "999999"}
        )
        assert antwort.status_code == 401
    gedrosselt = gedrosselter_client.post(
        "/api/login", json={"anzeigename": "Wer", "pin": "999999"}
    )
    assert gedrosselt.status_code == 429
    assert gedrosselt.headers.get("Retry-After") == "60"


# ---------- Kern-Fix: Namens-Sperre darf korrekte PIN NICHT blockieren ----------


def test_namens_sperre_blockt_korrekte_pin_von_anderer_ip_nicht(conn, einstellungen):
    """Ein Fremder sperrt 'Alex' per Fehlversuchen aus IP A — der echte Alex muss
    sich aus IP B mit korrekter PIN trotzdem anmelden können (kein gezielter DoS)."""
    nutzer_service.nutzer_anlegen(conn, anzeigename="Alex", pin="geheim99", rolle="admin", akteur="t")

    # Angreifer aus IP A rät die PIN falsch, bis die Namens-Sperre greift
    for _ in range(einstellungen.login_max_fehlversuche):
        with pytest.raises(nutzer_service.LoginFehlgeschlagen):
            nutzer_service.anmelden(
                conn, anzeigename="Alex", pin="falsch00", client_ip="10.0.0.1",
                einstellungen=einstellungen,
            )

    # name:alex ist jetzt gesperrt …
    from app.zeit import jetzt_iso
    sperre = nutzer_service._sperre_pruefen(conn, "name:alex", jetzt_iso())
    assert sperre is not None

    # … aber der echte Alex meldet sich aus einer ANDEREN IP mit korrekter PIN an
    ergebnis = nutzer_service.anmelden(
        conn, anzeigename="Alex", pin="geheim99", client_ip="10.0.0.2",
        einstellungen=einstellungen,
    )
    assert ergebnis.anzeigename == "Alex"


def test_eigene_ip_sperre_blockt_weiterhin(conn, einstellungen):
    """Aus DERSELBEN IP greift die IP-Sperre nach zu vielen Fehlversuchen — auch
    eine danach korrekte PIN wird abgewiesen (Brute-Force-Drosselung bleibt)."""
    nutzer_service.nutzer_anlegen(conn, anzeigename="Bea", pin="geheim99", akteur="t")
    for _ in range(einstellungen.login_max_fehlversuche):
        with pytest.raises(nutzer_service.LoginFehlgeschlagen):
            nutzer_service.anmelden(
                conn, anzeigename="Bea", pin="falsch00", client_ip="10.0.0.9",
                einstellungen=einstellungen,
            )
    with pytest.raises(nutzer_service.LoginGesperrt):
        nutzer_service.anmelden(
            conn, anzeigename="Bea", pin="geheim99", client_ip="10.0.0.9",
            einstellungen=einstellungen,
        )


# ---------- PIN-Mindestlänge auf Nutzer-Pfaden ----------


def test_registrierung_lehnt_kurze_pin_ab(einstellungen):
    reg = dataclasses.replace(einstellungen, registrierung_passwort="geheim123")
    app = create_app(reg)
    with TestClient(app) as client:
        antwort = client.post(
            "/api/registrieren",
            json={"anzeigename": "Kurz", "pin": "12345", "gruppen_passwort": "geheim123"},
        )
        assert antwort.status_code == 422


# ---------- Sessions bei PIN-Reset invalidiert ----------


def test_pin_reset_invalidiert_sessions(client, conn):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Alex", pin="adminpin", rolle="admin", akteur="t")
    mia = nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="miapin1", akteur="t")

    # Mia meldet sich an und hat eine gültige Session
    mia_client = TestClient(client.app)
    mia_client.post("/api/login", json={"anzeigename": "Mia", "pin": "miapin1"})
    assert mia_client.get("/api/me").status_code == 200

    # Admin setzt Mias PIN zurück
    client.post("/api/login", json={"anzeigename": "Alex", "pin": "adminpin"})
    assert client.patch(f"/api/admin/nutzer/{mia}", json={"pin": "neuepin1"}).status_code == 200

    # Mias alte Session ist jetzt ungültig
    assert mia_client.get("/api/me").status_code == 401


# ---------- Sicherheits-Header ----------


def test_hsts_nur_bei_cookie_secure(einstellungen):
    sicher = dataclasses.replace(einstellungen, cookie_secure=True)
    with TestClient(create_app(sicher)) as c:
        assert "Strict-Transport-Security" in c.get("/api/health").headers
    unsicher = dataclasses.replace(einstellungen, cookie_secure=False)
    with TestClient(create_app(unsicher)) as c:
        assert "Strict-Transport-Security" not in c.get("/api/health").headers
