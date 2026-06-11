"""Tests für die Poll-Phasen des Schedulers: live > vorlauf > normal."""
from __future__ import annotations

from datetime import timedelta

from app import db, scheduler
from app.zeit import iso_utc, jetzt_utc


def _spiel_anlegen(conn, *, anstoss, status):
    conn.execute(
        "INSERT INTO spiel (runde, anstoss_utc, status) VALUES ('Gruppe A', ?, ?)",
        (anstoss, status),
    )
    conn.commit()


def _frische_db(tmp_path):
    conn = db.verbinden(tmp_path / "phase.db")
    db.schema_anlegen(conn)
    return conn


def test_phase_normal_ohne_spiele(tmp_path):
    conn = _frische_db(tmp_path)
    assert scheduler._poll_phase(conn) == "normal"
    # Weit entferntes Spiel ändert nichts
    _spiel_anlegen(conn, anstoss=iso_utc(jetzt_utc() + timedelta(days=3)), status="geplant")
    assert scheduler._poll_phase(conn) == "normal"


def test_phase_vorlauf_ab_t75(tmp_path):
    conn = _frische_db(tmp_path)
    _spiel_anlegen(conn, anstoss=iso_utc(jetzt_utc() + timedelta(minutes=50)), status="geplant")
    assert scheduler._poll_phase(conn) == "vorlauf"


def test_phase_vorlauf_wenn_api_hinterherhinkt(tmp_path):
    """Anstoß vorbei, Status hängt auf 'geplant' → weiter eng pollen."""
    conn = _frische_db(tmp_path)
    _spiel_anlegen(conn, anstoss=iso_utc(jetzt_utc() - timedelta(minutes=10)), status="geplant")
    assert scheduler._poll_phase(conn) == "vorlauf"


def test_phase_live_schlaegt_vorlauf(tmp_path):
    conn = _frische_db(tmp_path)
    _spiel_anlegen(conn, anstoss=iso_utc(jetzt_utc() + timedelta(minutes=50)), status="geplant")
    _spiel_anlegen(conn, anstoss=iso_utc(jetzt_utc() - timedelta(minutes=30)), status="live")
    assert scheduler._poll_phase(conn) == "live"


def test_phase_haengengebliebenes_spiel_faellt_zurueck(tmp_path):
    """Nach dem Nachlauf-Fenster zählt ein vergessenes Live-Spiel nicht mehr."""
    conn = _frische_db(tmp_path)
    _spiel_anlegen(conn, anstoss=iso_utc(jetzt_utc() - timedelta(hours=6)), status="live")
    assert scheduler._poll_phase(conn) == "normal"


def test_drosselung_folgt_dem_tarif(einstellungen):
    """Der API-Mindestabstand leitet sich aus dem konfigurierten Limit ab."""
    import dataclasses

    from app.services.fussball_api import FussballApi

    frei = dataclasses.replace(einstellungen, api_token="t")  # Standard: 10/Min.
    assert 6.0 <= FussballApi(frei)._min_abstand <= 6.6
    livescores = dataclasses.replace(einstellungen, api_token="t", api_rate_pro_minute=20)
    assert 3.0 <= FussballApi(livescores)._min_abstand <= 3.3
    # Standard-Live-Poll bleibt deutlich unter dem Limit
    assert einstellungen.live_poll_sekunden >= 60 / einstellungen.api_rate_pro_minute


def test_live_takt_wird_auf_tarif_geklemmt(einstellungen, monkeypatch):
    """Ein zu klein konfigurierter Live-Takt darf das Tarif-Limit nie reißen."""
    import asyncio
    import dataclasses

    kaputt = dataclasses.replace(
        einstellungen, live_poll_sekunden=0.5, api_rate_pro_minute=20, sync_intervall_minuten=60
    )
    gewartet: list[float] = []

    async def schnelltest():
        stop = asyncio.Event()

        async def fake_wait_for(aufgabe, timeout):
            gewartet.append(timeout)
            stop.set()  # nach dem ersten Tick beenden
            raise asyncio.TimeoutError

        monkeypatch.setattr(scheduler.asyncio, "wait_for", fake_wait_for)
        monkeypatch.setattr(scheduler, "_sync_lauf", lambda _einstellungen: "live")
        await scheduler.sync_schleife(kaputt, stop)

    asyncio.run(schnelltest())
    assert gewartet and gewartet[0] >= 3.0  # 60/20 = 3 s Untergrenze
