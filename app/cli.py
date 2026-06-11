"""Kommandozeile für Betrieb und Bootstrap: python -m app.cli <befehl> ..."""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import db
from .config import lade_einstellungen
from .services import importer, nutzer as nutzer_service, quoten, sync
from .services.fussball_api import FussballApiFehler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wm26", description="WM26-Verwaltung")
    unterbefehle = parser.add_subparsers(dest="befehl", required=True)

    unterbefehle.add_parser("init-db", help="Datenbank und Schema anlegen")

    anlegen = unterbefehle.add_parser("nutzer-anlegen", help="Nutzer mit Name + PIN anlegen")
    anlegen.add_argument("--name", required=True, help="Anzeigename")
    anlegen.add_argument(
        "--rolle", default="mitglied", choices=["admin", "mitglied", "ki"], help="Rolle"
    )
    anlegen.add_argument("--pin", help="PIN (ohne Angabe: interaktive Abfrage)")

    spielplan = unterbefehle.add_parser(
        "import-spielplan", help="Spielplan aus JSON-Datei importieren"
    )
    spielplan.add_argument("datei", type=Path, help="Pfad zur JSON-Datei")

    sync_parser = unterbefehle.add_parser("sync", help="Sync-Job sofort ausführen")
    sync_parser.add_argument(
        "job",
        nargs="?",
        default=sync.JOB_ERGEBNISSE,
        choices=[
            sync.JOB_STAMMDATEN,
            sync.JOB_ERGEBNISSE,
            sync.JOB_VERGLEICHE,
            quoten.JOB_QUOTEN,
        ],
    )

    args = parser.parse_args(argv)
    einstellungen = lade_einstellungen()
    conn = db.verbinden(einstellungen.db_pfad)
    try:
        db.schema_anlegen(conn)
        if args.befehl == "init-db":
            print(f"Datenbank bereit: {einstellungen.db_pfad}")
        elif args.befehl == "nutzer-anlegen":
            pin = args.pin or getpass.getpass("PIN: ")
            try:
                nutzer_id = nutzer_service.nutzer_anlegen(
                    conn, anzeigename=args.name, pin=pin, rolle=args.rolle, akteur="cli"
                )
            except ValueError as fehler:
                print(f"Fehler: {fehler}", file=sys.stderr)
                return 1
            print(f"Nutzer '{args.name}' angelegt (id {nutzer_id}, Rolle {args.rolle}).")
        elif args.befehl == "import-spielplan":
            try:
                daten = json.loads(args.datei.read_text(encoding="utf-8"))
                ergebnis = importer.spielplan_importieren(conn, daten, akteur="cli")
            except (OSError, ValueError, KeyError) as fehler:
                print(f"Import fehlgeschlagen: {fehler}", file=sys.stderr)
                return 1
            print(
                f"Import: {ergebnis.teams} Teams, {ergebnis.spielorte} Spielorte, "
                f"{ergebnis.spiele_neu} Spiele neu, {ergebnis.spiele_aktualisiert} aktualisiert."
            )
        elif args.befehl == "sync":
            try:
                if args.job == sync.JOB_STAMMDATEN:
                    bericht = sync.stammdaten_sync(conn, einstellungen)
                elif args.job == sync.JOB_VERGLEICHE:
                    # 1 Call pro Spiel, gedrosselt aufs Free-Tier-Limit —
                    # der erste Volllauf (104 Spiele) dauert gut 10 Minuten.
                    bericht = sync.vergleiche_sync(conn, einstellungen)
                elif args.job == quoten.JOB_QUOTEN:
                    if not quoten.aktiv(einstellungen):
                        print("Kein WM26_QUOTEN_TOKEN gesetzt — Quoten bleiben aus.", file=sys.stderr)
                        return 1
                    bericht = quoten.quoten_sync(conn, einstellungen)
                else:
                    bericht = sync.ergebnis_sync(conn, einstellungen)
            except (FussballApiFehler, quoten.QuotenFehler) as fehler:
                print(f"Sync fehlgeschlagen: {fehler}", file=sys.stderr)
                return 1
            print(f"Sync {bericht.job}: {bericht.zusammenfassung()}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
