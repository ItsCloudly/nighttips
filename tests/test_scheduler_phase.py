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


def test_live_intervall_unter_dem_api_limit():
    """~8 Abrufe/Minute, nie unter dem 6,5-s-Mindestabstand des API-Clients."""
    assert 6.5 <= scheduler._LIVE_INTERVALL_SEKUNDEN <= 60
    assert 60 / scheduler._LIVE_INTERVALL_SEKUNDEN <= 9
