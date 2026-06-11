"""Einmalige Struktur-Probe gegen football-data.org (1 Call):
PYTHONPATH=. python tools/api_probe.py"""
from __future__ import annotations

import json
import sys

from app.config import lade_einstellungen
from app.services.fussball_api import FussballApi, FussballApiFehler


def main() -> int:
    try:
        api = FussballApi(lade_einstellungen())
        teams = api.teams()
    except FussballApiFehler as fehler:
        print(f"API-Fehler: {fehler}", file=sys.stderr)
        return 1
    print("Teams:", len(teams))
    if not teams:
        print("Keine Teams in der Antwort.")
        return 0
    erstes = teams[0]
    print("Felder:", sorted(erstes.keys()))
    print("Coach:", json.dumps(erstes.get("coach"), ensure_ascii=False))
    kader = erstes.get("squad") or []
    print("Squad-Groesse:", len(kader))
    if kader:
        print("Spieler-Beispiel:", json.dumps(kader[0], ensure_ascii=False))
    print("Namen:", sorted(team.get("name", "?") for team in teams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
