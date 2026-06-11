"""Schnelle Datenprüfung nach einem Sync: python tools/db_check.py"""
from __future__ import annotations

import sqlite3
from pathlib import Path

conn = sqlite3.connect(Path(__file__).resolve().parent.parent / "daten" / "wm26.db")
conn.row_factory = sqlite3.Row
try:
    print("Teams:", conn.execute("SELECT COUNT(*) FROM team").fetchone()[0])
    print("Spieler:", conn.execute("SELECT COUNT(*) FROM spieler").fetchone()[0])
    print("Trainer:", conn.execute("SELECT COUNT(*) FROM trainer").fetchone()[0])
    print("Spiele gesamt:", conn.execute("SELECT COUNT(*) FROM spiel").fetchone()[0])
    print("Spiele ohne api_ref:", conn.execute("SELECT COUNT(*) FROM spiel WHERE api_ref IS NULL").fetchone()[0])
    print("Teams ohne Gruppe:", conn.execute("SELECT COUNT(*) FROM team WHERE gruppe IS NULL").fetchone()[0])
    print("Direktvergleiche:", conn.execute("SELECT COUNT(*) FROM direktvergleich").fetchone()[0])
    print("Spiele mit h2h-Abruf:", conn.execute("SELECT COUNT(*) FROM h2h_abruf").fetchone()[0])
    print("Duell-Bilanzen:", conn.execute("SELECT COUNT(*) FROM duell_bilanz").fetchone()[0])
    print("Tabellenzeilen:", conn.execute("SELECT COUNT(*) FROM gruppen_tabelle").fetchone()[0])
    print("Torschützen:", conn.execute("SELECT COUNT(*) FROM torschuetze").fetchone()[0])
    print("Runden:", [r[0] for r in conn.execute("SELECT DISTINCT runde FROM spiel ORDER BY runde")])

    sql = (
        "SELECT s.id, s.runde, s.anstoss_utc, th.name AS heim, tg.name AS gast, s.status, s.api_ref"
        " FROM spiel s LEFT JOIN team th ON th.id = s.heim_team_id"
        " LEFT JOIN team tg ON tg.id = s.gast_team_id ORDER BY s.anstoss_utc LIMIT 8"
    )
    for zeile in conn.execute(sql):
        print(dict(zeile))
finally:
    conn.close()
