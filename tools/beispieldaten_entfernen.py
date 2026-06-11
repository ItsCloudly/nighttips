"""Entfernt die JSON-Beispielspiele (ohne api_ref) aus der lokalen Dev-Datenbank."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

pfad = Path(__file__).resolve().parent.parent / "daten" / "wm26.db"
conn = sqlite3.connect(pfad)
try:
    conn.execute("PRAGMA foreign_keys=ON")
    geloescht = conn.execute("DELETE FROM spiel WHERE api_ref IS NULL").rowcount
    conn.commit()
    print(f"{geloescht} Beispiel-Spiele entfernt (Tipps darauf via ON DELETE CASCADE).")
except sqlite3.Error as fehler:
    conn.rollback()
    print(f"Fehler beim Löschen: {fehler}", file=sys.stderr)
    sys.exit(1)
finally:
    conn.close()
