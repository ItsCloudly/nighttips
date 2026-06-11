"""Update-Sicherheit der Datenbank: App-Starts und Migrationen dürfen
Bestandsdaten (Konten, Tipps) niemals verändern oder löschen.

Hintergrund: Updates werden als reine Code-Deploys eingespielt, die DB in
daten/ bleibt liegen. Beim Start läuft schema_anlegen() auf der Bestands-DB —
dieser Test schlägt Alarm, falls dort je etwas Destruktives landet.
"""
from __future__ import annotations

import sqlite3

from app import db
from app.services import nutzer as nutzer_service, tippspiel
from app.zeit import jetzt_iso


def _bestand_anlegen(conn: sqlite3.Connection) -> dict:
    nutzer_service.nutzer_anlegen(conn, anzeigename="Erika", pin="geheim123", akteur="t")
    erika = conn.execute("SELECT id FROM nutzer WHERE anzeigename = 'Erika'").fetchone()["id"]
    conn.execute(
        "INSERT INTO team (fifa_code, name, gruppe) VALUES ('AAA', 'Team A', 'A'), ('BBB', 'Team B', 'A')"
    )
    heim = conn.execute("SELECT id FROM team WHERE fifa_code = 'AAA'").fetchone()["id"]
    gast = conn.execute("SELECT id FROM team WHERE fifa_code = 'BBB'").fetchone()["id"]
    conn.execute(
        "INSERT INTO spiel (runde, anstoss_utc, status, heim_team_id, gast_team_id)"
        " VALUES ('Gruppe A', '2030-01-01T12:00:00Z', 'geplant', ?, ?)",
        (heim, gast),
    )
    spiel = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    conn.commit()  # offene implizite Transaktion schließen, tipp_abgeben startet selbst eine
    tippspiel.tipp_abgeben(conn, nutzer_id=erika, spiel_id=spiel, tipp_heim=2, tipp_gast=1)
    return {"nutzer_id": erika, "spiel_id": spiel}


def _bestand_pruefen(conn: sqlite3.Connection, bestand: dict) -> None:
    nutzer = conn.execute(
        "SELECT anzeigename, pin_hash FROM nutzer WHERE id = ?", (bestand["nutzer_id"],)
    ).fetchone()
    assert nutzer is not None and nutzer["anzeigename"] == "Erika"
    assert nutzer["pin_hash"]  # Login-Daten unangetastet
    tipp = conn.execute(
        "SELECT tipp_heim, tipp_gast FROM tipp WHERE nutzer_id = ? AND spiel_id = ?",
        (bestand["nutzer_id"], bestand["spiel_id"]),
    ).fetchone()
    assert tipp is not None and (tipp["tipp_heim"], tipp["tipp_gast"]) == (2, 1)


def test_app_start_erhaelt_bestandsdaten(tmp_path):
    """Mehrfache App-Starts (= schema_anlegen) lassen Konten und Tipps unberührt."""
    pfad = tmp_path / "bestand.db"
    conn = db.verbinden(pfad)
    db.schema_anlegen(conn)
    bestand = _bestand_anlegen(conn)
    conn.close()

    for _ in range(3):  # simulierte Update-Neustarts
        conn = db.verbinden(pfad)
        db.schema_anlegen(conn)
        _bestand_pruefen(conn, bestand)
        conn.close()


def test_migration_ergaenzt_spalte_ohne_datenverlust(tmp_path):
    """Eine Alt-DB ohne neuere Spalten bekommt sie per Migration — Daten bleiben."""
    pfad = tmp_path / "alt.db"
    conn = db.verbinden(pfad)
    db.schema_anlegen(conn)
    bestand = _bestand_anlegen(conn)
    # Alt-Stand nachstellen: Spalte aus einer früheren Version entfernen
    conn.execute("ALTER TABLE nutzer DROP COLUMN ki_freigeschaltet")
    conn.commit()
    conn.close()

    conn = db.verbinden(pfad)
    db.schema_anlegen(conn)
    spalten = {zeile["name"] for zeile in conn.execute("PRAGMA table_info(nutzer)")}
    assert "ki_freigeschaltet" in spalten
    _bestand_pruefen(conn, bestand)
    conn.close()


def test_schema_sql_ist_additiv():
    """schema.sql darf nichts löschen — nur IF-NOT-EXISTS-Anlagen."""
    text = db.SCHEMA_PFAD.read_text(encoding="utf-8").upper()
    assert "DROP " not in text
    assert "DELETE FROM" not in text
    assert "CREATE TABLE IF NOT EXISTS" in text
