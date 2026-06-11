"""Deutsche Anzeigenamen für die API-Teamnamen (football-data.org liefert Englisch).

Unbekannte Namen bleiben unverändert; Admin-Overrides können
jeden Namen übersteuern.
"""
from __future__ import annotations

TEAMNAMEN_DE = {
    "Algeria": "Algerien",
    "Argentina": "Argentinien",
    "Australia": "Australien",
    "Austria": "Österreich",
    "Belgium": "Belgien",
    "Bosnia-Herzegovina": "Bosnien-Herzegowina",
    "Brazil": "Brasilien",
    "Canada": "Kanada",
    "Cape Verde Islands": "Kap Verde",
    "Colombia": "Kolumbien",
    "Congo DR": "DR Kongo",
    "Croatia": "Kroatien",
    "Curaçao": "Curaçao",
    "Czechia": "Tschechien",
    "Ecuador": "Ecuador",
    "Egypt": "Ägypten",
    "England": "England",
    "France": "Frankreich",
    "Germany": "Deutschland",
    "Ghana": "Ghana",
    "Haiti": "Haiti",
    "Iran": "Iran",
    "Iraq": "Irak",
    "Ivory Coast": "Elfenbeinküste",
    "Japan": "Japan",
    "Jordan": "Jordanien",
    "Mexico": "Mexiko",
    "Morocco": "Marokko",
    "Netherlands": "Niederlande",
    "New Zealand": "Neuseeland",
    "Norway": "Norwegen",
    "Panama": "Panama",
    "Paraguay": "Paraguay",
    "Portugal": "Portugal",
    "Qatar": "Katar",
    "Saudi Arabia": "Saudi-Arabien",
    "Scotland": "Schottland",
    "Senegal": "Senegal",
    "South Africa": "Südafrika",
    "South Korea": "Südkorea",
    "Spain": "Spanien",
    "Sweden": "Schweden",
    "Switzerland": "Schweiz",
    "Tunisia": "Tunesien",
    "Turkey": "Türkei",
    "United States": "USA",
    "Uruguay": "Uruguay",
    "Uzbekistan": "Usbekistan",
}


def deutscher_teamname(api_name: str) -> str:
    return TEAMNAMEN_DE.get(api_name, api_name)
