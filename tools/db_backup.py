"""Nächtliches Datenbank-Backup mit Rotation.

Nutzt die SQLite-Backup-API (konsistent auch bei laufender App im WAL-Modus),
komprimiert das Ergebnis und räumt Stände auf, die älter als --tage sind.

Aufruf (z. B. per systemd-Timer, siehe deploy/wm26-backup.timer):
    python tools/db_backup.py --db daten/wm26.db --ziel ~/backups/wm26 --tage 14
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import sqlite3
import sys
from pathlib import Path


def backup(db_pfad: Path, ziel_dir: Path, tage: int) -> Path:
    if not db_pfad.exists():
        raise SystemExit(f"Datenbank nicht gefunden: {db_pfad}")
    ziel_dir.mkdir(parents=True, exist_ok=True)
    stempel = dt.datetime.now().strftime("%Y-%m-%d")
    ziel = ziel_dir / f"wm26-{stempel}.db.gz"

    quelle = sqlite3.connect(db_pfad)
    kopie = sqlite3.connect(ziel_dir / "wm26-backup.tmp")
    try:
        quelle.backup(kopie)
        kopie.commit()
    finally:
        kopie.close()
        quelle.close()

    tmp = ziel_dir / "wm26-backup.tmp"
    with open(tmp, "rb") as roh, gzip.open(ziel, "wb") as gz:
        gz.writelines(roh)
    tmp.unlink()

    grenze = dt.datetime.now() - dt.timedelta(days=tage)
    geloescht = 0
    for alt in ziel_dir.glob("wm26-*.db.gz"):
        if dt.datetime.fromtimestamp(alt.stat().st_mtime) < grenze:
            alt.unlink()
            geloescht += 1
    print(f"Backup: {ziel.name} ({ziel.stat().st_size // 1024} KB), {geloescht} alte entfernt")
    return ziel


def main() -> int:
    parser = argparse.ArgumentParser(description="WM26-Datenbank sichern")
    parser.add_argument("--db", type=Path, default=Path("daten/wm26.db"))
    parser.add_argument("--ziel", type=Path, required=True, help="Backup-Verzeichnis")
    parser.add_argument("--tage", type=int, default=14, help="Aufbewahrung in Tagen")
    args = parser.parse_args()
    backup(args.db, args.ziel.expanduser(), args.tage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
