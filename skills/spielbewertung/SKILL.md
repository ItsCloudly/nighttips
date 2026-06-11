---
name: spielbewertung
description: Bewertet WM-Spiele für die WM26-App — Prognose vor Anpfiff (mit KI-Tipp), Nachanalyse nach Abpfiff, Kalibrierung über LESSONS.md. Aufruf z. B. "Bewerte die heutigen Spiele".
---

# Spielbewertung (WM26-App, SPEC 7.3)

Du bewertest Spiele der WM-2026-App als externer Agent über deren REST-API.
Basis-URL und Token kommen aus der Umgebung (`WM26_BASIS_URL`, `WM26_AGENT_TOKEN`
— der Token braucht die Scopes `read,write_analysis`; der Admin erzeugt ihn in
der App unter „Mehr → Agent-Tokens").

Alle Anfragen mit Header `Authorization: Bearer $WM26_AGENT_TOKEN`.

## Ablauf

**0. Kalibrierung lesen — immer zuerst.** Lies `skills/spielbewertung/LESSONS.md`.
Dort stehen Trefferquote, Brier-Score und systematische Fehler bisheriger
Prognosen. Berücksichtige die dokumentierten Verzerrungen aktiv (z. B.
"Favoriten überschätzt" → Wahrscheinlichkeiten dämpfen).

**1. Daten ziehen.**
- `GET /api/export/spiele` — Spielplan, Stände, Ticker-Ereignisse, Duell-Bilanz
  (optional `?spiel_id=` für ein Spiel)
- `GET /api/export/teams` — Kader, Taktikprofil, Verletzungen, Tabellenstand
- `GET /api/export/analysen` — bisherige Analysen inkl. Endergebnis (Trefferbilanz!)
- `GET /api/export/news` — aktuelle Meldungen (Ausfälle, Aufstellungen)

**2. Prognose (nur vor Anpfiff).** Bewerte strukturiert nach festen Faktoren:
Form (bisherige WM-Spiele), Kaderverfügbarkeit (Verletzungen, Sperren aus
gelbroten/roten Karten im Ticker), taktisches Matchup, Turnierkontext
(Gruppensituation, Reise/Müdigkeit — Spielorte stehen im Export).

Schreibe die Prognose per `POST /api/analysen`:

```json
{
  "spiel_id": 42,
  "typ": "prognose",
  "inhalt_markdown": "## Prognose …(lesbarer Text für die App)…",
  "struktur_json": {
    "typ": "prognose",
    "spiel_id": 42,
    "wahrscheinlichkeiten": {"heim": 0.45, "remis": 0.27, "gast": 0.28},
    "ergebnis_tipp": {"heim": 2, "gast": 1},
    "konfidenz": "mittel",
    "schluesselfaktoren": ["…", "…"],
    "begruendung_kurz": "…"
  }
}
```

Die Wahrscheinlichkeiten müssen sich zu 1 summieren; `konfidenz` ist
niedrig/mittel/hoch.

**3. KI-Tipp setzen.** `POST /api/tipps/ki` mit dem `ergebnis_tipp`
(`{"spiel_id": 42, "tipp_heim": 2, "tipp_gast": 1}`). Der Tipp läuft als
regulärer Mitspieler "KI-Tipper" in der Rangliste und ist für alle sichtbar.

**4. Nachanalyse (nach Abpfiff).** Vergleiche die eigene Prognose mit dem
Verlauf: Was traf zu, was nicht — und warum? Bewerte die Schlüsselszenen aus
dem Ticker und gib eine kurze Leistungseinschätzung beider Teams.
`POST /api/analysen` mit `typ: "nachanalyse"`.

**5. LESSONS.md fortschreiben.** Nach jeder Nachanalyse-Runde:
- Trefferquote des KI-Tippers aktualisieren (exakt/Differenz/Tendenz/daneben)
- Brier-Score der Prognose-Wahrscheinlichkeiten berechnen und mitteln
  (Brier = Σ (p_i − o_i)² über heim/remis/gast; niedriger ist besser)
- Systematische Fehler als kurze, anwendbare Regeln notieren

**6. Vorschläge (optional).** Erkenntnisse zu Taktik oder Verletzungen, die in
der App fehlen, per `POST /api/beitraege` einreichen (`typ` taktik/verletzung,
`inhalt` als Objekt) — sie landen zur Sichtung beim Admin.

## Leitplanken

- Keine Tipps nach Anpfiff (die API lehnt das ab — nicht umgehen).
- `inhalt_markdown` ist für den Freundeskreis sichtbar: deutsch, kompakt,
  keine rohen JSON-Dumps.
- Pro Spiel und Typ sind mehrere Versionen erlaubt; die App zeigt die neueste.
- Schreibe nur über die dokumentierten Endpunkte (der Token kann ohnehin
  nichts anderes).
