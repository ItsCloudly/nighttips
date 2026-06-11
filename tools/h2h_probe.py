"""Roh-Antwort des head2head-Endpunkts ansehen (2 Calls):
PYTHONPATH=. python tools/h2h_probe.py"""
from __future__ import annotations

import json
import sys

import httpx

from app.config import lade_einstellungen


def main() -> int:
    einstellungen = lade_einstellungen()
    # Deutschland - Schottland (api_ref aus der DB) und Mexiko - Suedafrika
    for match_id in ("537357", "537327"):
        url = f"{einstellungen.api_basis_url}/matches/{match_id}/head2head?limit=5"
        antwort = httpx.get(url, headers={"X-Auth-Token": einstellungen.api_token}, timeout=30)
        print(match_id, "->", antwort.status_code)
        if antwort.status_code != 200:
            print(antwort.text[:500])
            continue
        daten = antwort.json()
        print("Schluessel:", sorted(daten.keys()))
        print(json.dumps(daten, ensure_ascii=False)[:1200])
        print("---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
