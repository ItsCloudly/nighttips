# nighttips

Selbst gehostetes Tippspiel zur Fußball-WM 2026 — eine schnelle, installierbare
Web-App (PWA) für den Freundeskreis. Dunkles UI, Live-Ticker, automatische
Punktewertung, Bonusfragen und ein optionaler KI-Mittipper.

## Features

- **Tippspiel**: Tipps bis zum Anpfiff, automatische Wertung (4 Punkte exakt,
  3 Tordifferenz, 2 Tendenz), Rangliste gesamt / pro Tag / pro Runde mit
  Podium und Formkurven, Bonusfragen mit konfigurierbarem Punktwert.
- **Live**: Ergebnis-Sync über football-data.org, Updates per
  Server-Sent-Events, Ticker, Web-Push (Tore, Anpfiff, Endstand,
  Tipp-Erinnerung) für gepinnte Spiele und Teams.
- **Turnier**: Gruppentabellen mit Quali-Markierung, wischbarer K.-o.-Baum,
  Spielseiten mit Phasen-Logik (vor / live / nach Abpfiff), Team- und
  Spielerprofile mit Kader-Spielfeld.
- **News**: RSS-Feeds mit Themen-Tags und Artikel-Reader.
- **Konten**: Login mit Name + PIN, Selbst-Registrierung per
  Gruppen-Passwort, Admin-Verwaltung. Fremde Tipps werden erst ab Anpfiff
  sichtbar.
- **KI-Mittipper (optional)**: Über eine tokengeschützte
  Agenten-Schnittstelle kann eine KI als Teilnehmer mittippen und Analysen
  schreiben. Sichtbar ist sie nur für Profile, die der Admin freigeschaltet
  hat — für alle anderen bleibt die App komplett KI-frei.

## Tech-Stack

FastAPI + SQLite (WAL) im Backend, Vanilla-JS-PWA ohne Build-Schritt im
Frontend. Läuft problemlos auf einem kleinen Single-Board-Computer.

## Schnellstart

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # ausfüllen, Kommentare in der Datei helfen
python -m app.cli init-db
python -m app.cli nutzer-anlegen --name Admin --rolle admin   # fragt die PIN ab; alternativ --pin …
python -m app.cli import-spielplan beispiel/spielplan-beispiel.json
uvicorn app.main:app --port 8026
```

Danach läuft die App auf `http://localhost:8026` (für lokale Entwicklung
`WM26_COOKIE_SECURE=0` setzen). Für echte Spieldaten einen kostenlosen
API-Key von [football-data.org](https://www.football-data.org/) in die
`.env` eintragen — Spielplan, Ergebnisse und Tabellen holt der Sync selbst.
Die API-Daten gehören dem Anbieter und liegen deshalb nicht im Repo.

## Betrieb & Updates

Eine Anleitung für den Dauerbetrieb (systemd-Units, HTTPS über Tailscale
Funnel, nächtliche Backups) steht in [docs/BETRIEB.md](docs/BETRIEB.md).

Wichtig fürs Updaten: **Die Datenbank wird von Updates nie angefasst.**
Sie liegt in `daten/` (gitignored), Updates sind reine Code-Deploys
(`git pull` + Service-Neustart), Schema-Änderungen laufen als additive
Migrationen beim App-Start. Konten und Tipps bleiben über Versionen hinweg
erhalten — abgesichert durch Tests (`tests/test_db_migration.py`) und das
Backup-Skript `tools/db_backup.py`.

## Struktur

```
app/
  main.py            App-Fabrik (Router, Security-Header, Sync-Scheduler)
  schema.sql         SQLite-Schema (additiv, change_log append-only)
  cli.py             init-db, nutzer-anlegen, import-spielplan, sync
  routers/           auth, spiele, tipps/rangliste, admin, SSE-Stream,
                     pins/push, news/bonusfragen, agenten
  services/          Login+Lockout, Import/Sync, Punktewertung, Live-Broker,
                     Web-Push, RSS, Bonusfragen, Admin-Overrides
  static/            PWA: index.html, app.js, styles.css, sw.js, Icons
deploy/              systemd-User-Units (App + nächtliches DB-Backup)
docs/                Spezifikation und Designkonzept
tests/               pytest-Suite
```

## Tests

```bash
python -m pytest tests -q
```

## API-Notizen

Eine interaktive API-Übersicht liefert FastAPI unter `/docs`. Der Login
erwartet z. B. `POST /api/login` mit `{"anzeigename": "…", "pin": "…"}`.

## Mitgeliefertes

- Schrift: [Space Grotesk](https://github.com/floriankarsten/space-grotesk),
  SIL Open Font License (siehe `app/static/fonts/OFL.txt`).
- Illustrationen und App-Icon: KI-generiert für dieses Projekt.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
