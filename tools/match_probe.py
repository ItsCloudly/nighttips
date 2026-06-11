"""Roh-Antwort des Match-Detail-Endpunkts ansehen (1 Call):
PYTHONPATH=. python tools/match_probe.py [match_id]

Zweck: prüfen, welche Felder der Free Tier im Spiel-Detail füllt
(goals/bookings/substitutions/lineups/referees) — relevant für den
Live-Ticker. Aussagekräftig erst, wenn ein Spiel gespielt ist.
"""
from __future__ import annotations

import json
import sys

import httpx

from app.config import lade_einstellungen


def main() -> int:
    match_id = sys.argv[1] if len(sys.argv) > 1 else "537327"  # Mexiko - Suedafrika
    einstellungen = lade_einstellungen()
    url = f"{einstellungen.api_basis_url}/matches/{match_id}"
    antwort = httpx.get(url, headers={"X-Auth-Token": einstellungen.api_token}, timeout=30)
    print(match_id, "->", antwort.status_code)
    if antwort.status_code != 200:
        print(antwort.text[:500])
        return 1
    daten = antwort.json()
    print("Schluessel:", sorted(daten.keys()))
    for feld in ("goals", "bookings", "substitutions", "referees", "homeTeam"):
        wert = daten.get(feld)
        if isinstance(wert, dict):
            print(f"{feld}-Schluessel:", sorted(wert.keys()))
        else:
            print(f"{feld}:", json.dumps(wert, ensure_ascii=False)[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
