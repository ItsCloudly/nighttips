from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as db_modul
from app import ratelimit
from app.config import Einstellungen
from app.main import create_app


@pytest.fixture(autouse=True)
def _ratelimit_zuruecksetzen() -> None:
    """Der Rate-Limiter ist prozessweit — vor jedem Test leeren, sonst summieren
    sich Treffer über Tests hinweg und lösen fremde 429 aus."""
    ratelimit.zuruecksetzen()


@pytest.fixture
def einstellungen(tmp_path: Path) -> Einstellungen:
    return Einstellungen(
        db_pfad=tmp_path / "test.db",
        cookie_secure=False,
        session_dauer_tage=30,
        login_max_fehlversuche=3,
        login_sperre_minuten=15,
        api_provider="football-data",
        api_token="",
        api_basis_url="http://api.beispiel.invalid/v4",
        api_wettbewerb="WC",
        sync_intervall_minuten=0,
        ko_wertung_nach_120=True,
    )


@pytest.fixture
def client(einstellungen: Einstellungen) -> Iterator[TestClient]:
    app = create_app(einstellungen)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def conn(einstellungen: Einstellungen, client: TestClient) -> Iterator[sqlite3.Connection]:
    """DB-Verbindung auf die Test-Datenbank (Schema existiert nach App-Start)."""
    verbindung = db_modul.verbinden(einstellungen.db_pfad)
    yield verbindung
    verbindung.close()
