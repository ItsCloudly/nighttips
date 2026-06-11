# Betrieb

Anleitung für den Dauerbetrieb auf einem eigenen Server oder
Single-Board-Computer (Linux mit systemd). Pfade gehen davon aus, dass das
Repo unter `~/wm26` liegt — sonst die Units entsprechend anpassen.

## Installation

```bash
git clone https://github.com/ItsCloudly/nighttips.git ~/wm26
cd ~/wm26
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env       # ausfüllen (API-Key, VAPID, Gruppen-Passwort …)
.venv/bin/python -m app.cli init-db
.venv/bin/python -m app.cli nutzer-anlegen --name Admin --rolle admin
```

## Als Dienst (systemd-User-Unit)

```bash
cp deploy/wm26.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wm26.service
loginctl enable-linger "$USER"     # Dienst überlebt Logout/Reboot
```

Die App lauscht nur auf `127.0.0.1:8026` — nach außen kommt sie über einen
HTTPS-Proxy. Bewährt: [Tailscale Funnel](https://tailscale.com/kb/1223/funnel)
(`tailscale funnel --bg 8026`), dann gilt `WM26_COOKIE_SECURE=1`.
Jeder andere Reverse-Proxy mit TLS funktioniert genauso.

## Nächtliche Backups

```bash
mkdir -p ~/backups/wm26
cp deploy/wm26-backup.service deploy/wm26-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wm26-backup.timer
```

Der Timer sichert die Datenbank jede Nacht über die SQLite-Backup-API
(konsistent auch im laufenden Betrieb), komprimiert sie nach
`~/backups/wm26/` und behält 14 Tage. Manuell:
`.venv/bin/python tools/db_backup.py --db daten/wm26.db --ziel ~/backups/wm26`

## Updates einspielen

```bash
cd ~/wm26
git pull --ff-only
.venv/bin/python -m pytest tests -q     # erst grün, dann neu starten
systemctl --user restart wm26.service
curl -s http://127.0.0.1:8026/api/health
```

Updates fassen die Datenbank nicht an: `daten/` ist gitignored,
Schema-Änderungen laufen als additive Migrationen beim App-Start
(`app/db.py`), und `tests/test_db_migration.py` stellt sicher, dass
Bestandsdaten (Konten, Tipps) jeden Update-Neustart unverändert überleben.

## Gesundheit prüfen

- `curl https://<deine-domain>/api/health` → `{"status": "ok", "version": "…"}`
- `systemctl --user status wm26.service`
- Sync-Status sieht der Admin in der App unter „Mehr".
