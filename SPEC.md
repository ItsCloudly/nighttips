# Spezifikation: WM 2026 App ("Project26")

Stand: 2026-06-10. Diese Spezifikation ist das Ergebnis eines strukturierten Anforderungsinterviews. Alle Kernentscheidungen sind getroffen; offene Detailpunkte sind als [FILL: ...] markiert.

---

## 1. Überblick und Ziele

**Produktidee:** Eine zentrale Datensammel- und Analyse-App zur Fußball-WM 2026 (11. Juni bis 19. Juli 2026, 48 Teams, 104 Spiele) mit benutzerfreundlicher Oberfläche, Live-Updates, eigenem Tippspiel und KI-gestützten Analysen.

**Zielgruppe:** Privater Freundeskreis und Familie, ca. 5 bis 30 Personen. Keine öffentliche Registrierung.

**Plattform:** Selbst gehostete Web-App (PWA, auf dem Smartphone installierbar) auf einem kleinen Single-Board-Computer (z. B. Rock64), betrieben als systemd-User-Service.

**Leitprinzipien:**
1. Hybride Datenstrategie: Fußball-API und RSS-Feeds als Hauptquellen, manuelle Anpassungen sind immer möglich und haben dauerhaft Vorrang, Cloud-Agenten können später als weitere Quelle zugeschaltet werden.
2. KI ohne laufende Kosten: keine automatischen API-Aufrufe an KI-Dienste. Stattdessen greifen Claude-Agenten (Claude Code, Cloud Cowork) manuell und tokengeschützt auf die Daten zu und schreiben Analysen zurück.
3. Etappen-MVP: Die App entsteht während des laufenden Turniers. Zeitkritisches zuerst (Tipps gehen nur vor Anpfiff), Ausbau in der Gruppenphase, Vollausbau zur K.o.-Runde.

---

## 2. Architektur

```
[Clients: PWA im Browser / Homescreen]
        |  HTTPS (Tailscale Funnel, feste Adresse)
        v
[Rock64 SBC]
  - FastAPI-Anwendung (Python, systemd --user Service "wm26.service")
  - SQLite-Datenbank (eine Datei: wm26.db)
  - Scheduler (APScheduler oder asyncio-Tasks) für Sync-Jobs
  - SSE-Endpunkt für Live-Push an Clients
  - Statisches Frontend: Vanilla JS, kein Build-Step
        |
        +--> Fußball-API ([FILL: finale API-Wahl, Kandidaten: API-Football, football-data.org])
        +--> RSS-Feeds ([FILL: konkrete Feed-Liste, z.B. Kicker, Sportschau])
        <--- Claude-Agenten via REST (lesen + Analysen schreiben, Token)
```

**Stack-Entscheidungen:**
- Backend: Python 3 mit FastAPI (async, natives SSE, automatische OpenAPI-Doku für die Agenten-Schnittstelle).
- Datenbank: SQLite. Begründung: relationale Daten (Teams, Spieler, Spiele, Tipps), gleichzeitige Tipp-Abgaben, Änderungshistorie, trotzdem eine einzige, einfach zu sichernde Datei.
- Frontend: Vanilla-JS-SPA als PWA (Service Worker, Web App Manifest, Web Push). Kein Build-Step, wie beim Lernhub.
- Zugang von außen: Tailscale Funnel (Alternative: Cloudflare Tunnel) stellt eine feste HTTPS-Adresse bereit, ohne Portfreigabe im Router. TLS inklusive.
- Deployment: lokales Git-Repo auf dem Rock64 (kein Remote, Commit nach jedem Deploy), Backups über einen nächtlichen systemd-Timer (siehe `deploy/wm26-backup.timer`).

---

## 3. Datenmodell

Alle Entitäten liegen in SQLite. Felder mit (O) sind durch Admin-Overrides überschreibbar (siehe 4.4).

### 3.1 Stammdaten

**team**
- id, fifa_code, name (O), gruppe, flagge_url, trainer_id, api_ref

**spieler**
- id, team_id, name (O), trikotnummer (O), position (Torwart/Abwehr/Mittelfeld/Sturm), geburtsdatum, verein, api_ref
- Auswechselspieler sind keine eigene Entität: die Rolle ergibt sich pro Spiel aus aufstellung.rolle (startelf/bank) und aus Einwechsel-Ereignissen im Live-Ticker.

**trainer**
- id, team_id, name (O), nationalitaet, amtsantritt

**spielort**
- id, stadion_name, stadt, land (USA/Mexiko/Kanada), kapazitaet, zeitzone

**taktik** (pro Team, manuell und agentengepflegt)
- id, team_id, formation (z.B. "4-3-3"), beschreibung (Freitext), staerken, schwaechen, quelle (admin/agent), stand_datum
- Grundlage der taktischen Ansichten (siehe 6.3).

### 3.2 Spielbetrieb

**spiel**
- id, runde (Gruppe A..L, Achtelfinale, ... Finale), anstoss_utc, spielort_id, heim_team_id, gast_team_id, status (geplant/live/halbzeit/beendet/abgesagt), tore_heim, tore_gast, ergebnis_nach (90/120/elfmeterschiessen), api_ref

**aufstellung** (je Spiel und Team)
- id, spiel_id, team_id, spieler_id, rolle (startelf/bank), position_im_system (z.B. "LV", "ZM"), formation
- Quelle: API, sobald veröffentlicht (ca. 60 Minuten vor Anpfiff); Admin kann korrigieren.

**ereignis** (Live-Ticker-Einträge)
- id, spiel_id, minute, typ (tor/eigentor/elfmeter/gelb/gelbrot/rot/wechsel/var/anpfiff/halbzeit/abpfiff/freitext), spieler_id, spieler2_id (z.B. eingewechselt für), text, quelle (api/admin), erstellt_utc

**verletzung**
- id, spieler_id, beschreibung, status (fraglich/fällt aus/wieder fit), quelle (rss/admin/agent), gemeldet_utc, geprueft (bool, Admin-Bestätigung)

### 3.3 Nutzer, Tippspiel, Pins

**nutzer**
- id, anzeigename, pin_hash, rolle (admin/mitglied/ki), erstellt_utc
- Genau ein Nutzer mit rolle=ki ("KI-Tipper") repräsentiert die KI in der Rangliste.

**tipp**
- id, nutzer_id, spiel_id, tipp_heim, tipp_gast, abgegeben_utc, punkte (null bis Auswertung)
- Eindeutigkeit: ein Tipp pro Nutzer und Spiel, änderbar bis Anpfiff (Sperre serverseitig über spiel.anstoss_utc).

**bonusfrage**
- id, frage (z.B. "Wer wird Weltmeister?"), typ (team/spieler), punkte_wert, einsendeschluss_utc, aufloesung_ref

**bonustipp**
- id, nutzer_id, bonusfrage_id, antwort_ref, punkte

**pin**
- id, nutzer_id, typ (spiel/team), ref_id, erstellt_utc

**push_subscription**
- id, nutzer_id, endpoint, schluessel (p256dh, auth), erstellt_utc

### 3.4 News und KI

**feed**
- id, url, titel, aktiv (bool), letzter_abruf_utc

**news_item**
- id, feed_id, titel, link, zusammenfassung, veroeffentlicht_utc, team_id (optional, automatische Zuordnung über Teamnamen-Matching, vom Admin korrigierbar)

**ki_analyse**
- id, spiel_id, typ (prognose/nachanalyse), inhalt_markdown, struktur_json (siehe 7.3), agent_name, erstellt_utc, version
- Mehrere Versionen pro Spiel und Typ sind erlaubt; die App zeigt die jeweils neueste.

### 3.5 Versionierung und Herkunft

**change_log** (Änderungshistorie, append-only)
- id, entitaet, entitaet_id, feld, alt_wert, neu_wert, quelle (api/rss/admin/agent), akteur (Nutzer- oder Agentenname, bei API der Jobname), zeitpunkt_utc

**override**
- id, entitaet, entitaet_id, feld, wert, gesetzt_von (nutzer_id), gesetzt_utc, aktiv (bool)
- Quellen-Priorität bei der Anzeige und beim Sync: **admin-Override > Agent > API/RSS**. API-Syncs schreiben Rohwerte in die Entitätstabellen, die Ausgabeschicht blendet aktive Overrides darüber. Ein Override bleibt bestehen, bis der Admin ihn aufhebt; API-Updates überschreiben ihn nie.

---

## 4. Datenerfassung und Aktualisierung

### 4.1 Sync-Jobs (Scheduler auf dem Rock64)

| Job | Intervall | Inhalt |
|---|---|---|
| Stammdaten-Sync | 1x täglich (nachts) | Teams, Kader, Trainer, Spielorte, Spielplan-Korrekturen |
| Spieltags-Sync | stündlich an Spieltagen | Anstoßzeiten, Schiedsrichter, Status |
| Aufstellungs-Poll | alle 5 Min. ab T-75 Min. vor Anpfiff | Aufstellungen, sobald veröffentlicht; löst Push für gepinnte Spiele aus |
| Live-Poll | ca. alle 60 Sek. je laufendem Spiel | Spielstand, Ereignisse; Delta wird in `ereignis` geschrieben und per SSE gepusht |
| Abschluss-Sync | bei Statuswechsel auf beendet | Endstand fixieren, Tipp-Auswertung anstoßen |
| RSS-Abruf | alle 30 Min. | Neue news_items einlesen, Deduplizieren über Link-Hash |

Zustandsmaschine pro Spiel: `geplant -> (T-75) vorlauf -> (anstoss) live -> halbzeit -> live -> beendet`. Der Live-Poll läuft nur im Zustand vorlauf/live/halbzeit, um das API-Kontingent zu schonen. [FILL: Kontingent und Limits des gewählten API-Tarifs prüfen, ggf. Polling auf 90 Sek. strecken bei parallelen Spielen.]

### 4.2 Live-Update-Auslieferung an Clients

- Ein SSE-Endpunkt `GET /api/stream` hält die Verbindung zu allen geöffneten Clients.
- Events: `score`, `ereignis`, `aufstellung`, `status`, `tippstand`. Payload ist das geänderte Objekt als JSON.
- Clients ohne offene Verbindung erhalten Web Push (siehe 5.5), sofern berechtigt (Pin vorhanden).
- Fallback: Das Frontend pollt `GET /api/spiele?seit=<timestamp>` alle 90 Sekunden, falls SSE nicht verfügbar ist.

### 4.3 RSS-Verarbeitung

- Feeds werden im Admin-Bereich angelegt, aktiviert, deaktiviert (abonnieren/abbestellen).
- Jedes news_item wird per einfachem Namens-Matching einem Team zugeordnet (Anzeige im Team-Profil und in der News-Ansicht).
- Verletzungsmeldungen werden nicht automatisch als Fakt übernommen: Ein news_item kann vom Admin (oder einem Agenten) per Klick in einen `verletzung`-Eintrag umgewandelt werden, Status geprueft=false bis zur Admin-Bestätigung.

### 4.4 Manuelle Pflege (Admin-Bereich in der App)

- Passwortgeschützter Admin-Modus innerhalb der PWA, auch mobil nutzbar.
- Editierbar: Teamdaten, Spielerdaten, Trainer, Taktiken, Verletzungen, Aufstellungen, Ereignisse, Spielstände (Notfall, falls die API ausfällt), Feeds, Bonusfragen, Nutzerverwaltung.
- Jede manuelle Änderung erzeugt einen `override`-Eintrag plus `change_log`-Eintrag. Die Oberfläche markiert überschriebene Felder sichtbar (kleines Schloss-Symbol) und bietet "Override aufheben".

### 4.5 Cloud-Agenten als optionale Datenquelle

- Agenten (Claude Code, Cowork) können über die Schreib-Schnittstelle (siehe 7.2) Taktik-Beschreibungen, Verletzungs-Recherchen und Analysen beitragen, Quelle wird als `agent` protokolliert.
- Agenten-Beiträge rangieren unter Admin-Overrides: der Admin kann sie jederzeit korrigieren oder sperren.

---

## 5. Funktionsmodule

### 5.1 Hauptmenü / Spielplan
- Startansicht der App. Zeigt die Spieltermine: heute und die nächsten Tage, gruppiert nach Datum, mit Anstoßzeit (lokale Zeitzone des Nutzers), Stadion, Gruppe/Runde, Spielstand bei live/beendet.
- Gepinnte Spiele und Spiele gepinnter Teams erscheinen in einer eigenen Sektion ganz oben.
- Filter: alle / nur gepinnt / nach Gruppe / nach Team. Ungetippte, bald startende Spiele tragen einen Hinweis-Badge.

### 5.2 Spiel-Detail (Lupe "Spiel")
- Kopf: Teams, Stand, Status, Stadion, Anstoß.
- Reiter: **Ticker** (Ereignisliste live), **Aufstellungen** (siehe 6.3), **Tipps** (alle Tipps des Freundeskreises, vor Anpfiff nur eigene Eingabe sichtbar, Tipps der anderen erst ab Anpfiff; KI-Tipp und KI-Prognose erscheinen hier nur für KI-freigeschaltete Profile), **KI-Analyse** (Prognose vor dem Spiel, Nachanalyse danach — der gesamte Reiter existiert nur für Admins, den KI-Tipper und vom Admin freigeschaltete Nutzer [`nutzer.ki_freigeschaltet`]), **Infos** (Spielort, Schiedsrichter, direkte Vergleiche soweit von der API geliefert). Zur KI-Sichtbarkeit insgesamt siehe 5.4: ohne Freischaltung ist die App komplett KI-frei.

### 5.3 Teams und Kader (Lupe "Team")
- Teamprofil: Gruppe, bisherige Ergebnisse, Restprogramm, Trainer, Taktik (Formation plus Beschreibung), Kader mit Positionen, Verletzungsliste, zugeordnete News.
- Spieler-Detail: Stammdaten, Turnierstatistik (Tore, Karten, Einsatzminuten, aus `ereignis` aggregiert), Verletzungsstatus.

### 5.4 Tippspiel
- Tippabgabe pro Spiel bis Anpfiff (serverseitige Sperre). Eingabe direkt in der Spielliste und im Spiel-Detail.
- Punktevergabe automatisch nach Abpfiff: exaktes Ergebnis 4 Punkte, richtige Tordifferenz 3, richtige Tendenz 2, sonst 0. Bei K.o.-Spielen zählt das Ergebnis nach 90 bzw. 120 Minuten (Elfmeterschießen zählt als Unentschieden-Tendenz). [FILL: finale Festlegung mit dem Freundeskreis, ob 120-Minuten-Regel oder 90-Minuten-Regel gilt.]
- Bonusfragen vor Turnierstart bzw. vor der K.o.-Runde (Weltmeister, Torschützenkönig), Punktwert konfigurierbar.
- Rangliste: Gesamt, pro Tag, pro Runde.
- **KI-Sichtbarkeit (Nutzer-Entscheidung vom 11.06.2026, ersetzt die frühere Regel „KI-Tipps jederzeit für alle sichtbar"):** Der KI-Tipper nimmt weiter als Teilnehmer teil, ist aber nur für KI-freigeschaltete Profile sichtbar (Admins, rolle=ki, `nutzer.ki_freigeschaltet=1`). Für alle anderen Profile ist die App **komplett KI-frei**: keine KI-Zeile in den Ranglisten (Plätze werden ohne den KI-Tipper berechnet), keine KI-Tipps und -Begründungen in Tipplisten, keine KI-Analysen, keine KI-Badges oder -Sektionen im UI. **Standard für neue Profile: KI aus.** Freischaltung ausschließlich durch den Admin (siehe 8.2). Für freigeschaltete Profile gilt unverändert: Tipps und Begründungen der KI sind einsehbar — wer abschreibt, kann höchstens gleichziehen.
- Tippspiel-Import (spätere Erweiterung, nicht MVP): Import von Tipps/Ständen aus externen Plattformen. [FILL: Zielplattform und Exportformat, z.B. Kicktipp-CSV.]

### 5.5 Pins und Benachrichtigungen
- Pinnen von Spielen und Teams (ein Tap auf das Pin-Symbol). Wirkung: eigene Sektion oben im Hauptmenü, Filteroption, Web-Push-Benachrichtigungen.
- Push-Anlässe für gepinnte Inhalte: Anpfiff in 30 Minuten, Aufstellung veröffentlicht, Tor, Endstand. Zusätzlich für alle Nutzer: Tipp-Erinnerung, wenn ein ungetipptes Spiel in [FILL: Vorlaufzeit, Vorschlag 2 Stunden] beginnt.
- Technik: Web Push (VAPID) aus dem Service Worker, Subscriptions in `push_subscription`.

### 5.6 News (RSS)
- Chronologische News-Ansicht über alle aktiven Feeds, filterbar nach Team.
- Feed-Verwaltung (abonnieren, deaktivieren) im Admin-Bereich.

### 5.7 Admin-Bereich
- Siehe 4.4. Zusätzlich: Nutzer anlegen (Name + PIN), Bonusfragen verwalten und auflösen, Sync-Status und letzte API-Abrufe einsehen, change_log durchsuchen, API-Token für Agenten erzeugen und widerrufen.

---

## 6. UI/UX

### 6.1 Navigation
- Untere Navigationsleiste (PWA-tauglich, Muster vom Lernhub übernehmbar): **Spiele** (Hauptmenü), **Tippspiel**, **Teams**, **News**, **Mehr** (Einstellungen, Admin, Regeln).
- Sprache der Oberfläche: Deutsch.

### 6.2 Lupen-Prinzip
- "Lupen" sind fokussierte Detailansichten, die aus Listen heraus geöffnet werden: Spiel-Lupe, Team-Lupe, Spieler-Lupe. Jede Lupe folgt demselben Aufbau (Kopf mit Kernfakten, Reiter für Teilbereiche), damit die Darstellung konsistent und schnell erlernbar bleibt.

### 6.3 Taktische Ansichten
- Aufstellungs-Darstellung als gezeichnetes Spielfeld (SVG): Punkte je Spieler gemäß Formation und position_im_system, Bank darunter als Liste, Einwechslungen werden nach Live-Ereignissen nachgeführt.
- Team-Taktik-Ansicht: Formation als Feldgrafik plus Freitext (Stärken, Schwächen, typische Muster) aus `taktik`, inklusive Quellen- und Stand-Angabe.

### 6.4 Darstellungsgrundsätze
- Dark Mode wie beim Lernhub über CSS-Token.
- Live-Inhalte aktualisieren sich ohne Neuladen (SSE), mit dezenter Hervorhebung neuer Ereignisse.
- Jede angezeigte Dateneinheit mit Herkunfts-Kennzeichnung, wo relevant (API-Stand, manuell korrigiert, Agenten-Beitrag), mindestens im Admin-Modus.

### 6.5 Mobiles Layout (verbindliche Anforderung, ab Etappe 1)
Das mobile Layout ist kein nachgelagerter Ausbau, sondern wird von Anfang an mitgeplant und mitrealisiert. Hauptnutzung ist das Smartphone; der Desktop ist die abgeleitete Anpassung, nicht umgekehrt.
- Jede Ansicht, die ausgeliefert wird, erscheint gleichzeitig mit funktionierendem mobilen Layout. Es gibt keinen Zwischenstand "nur Desktop".
- Responsive über CSS-Breakpoints (Erfahrungswerte aus dem Lernhub: ca. 720px und 1050px), Layout mit dvh statt vh wegen mobiler Browser-Leisten, safe-area-insets beachten.
- Untere Navigationsleiste als freischwebende Pill (bewährtes Lernhub-Muster), Touch-Ziele mindestens ca. 44px.
- Tippabgabe einhändig bedienbar: Ergebnis-Eingabe direkt in der Spielliste ohne Pflicht-Umweg über die Spiel-Lupe.
- Taktik-Feldgrafik (SVG) skaliert auf Hochkant-Bildschirme; Tabellen (Rangliste, Kader) brechen mobil in Karten- oder Scroll-Darstellung um, kein horizontales Quetschen.
- PWA-Installation (Homescreen) und Web Push werden auf den realen Geräten des Freundeskreises getestet ([FILL: Geräte-/Browserliste, mindestens Android/Chrome und iOS/Safari]).

---

## 7. KI-Integration

### 7.1 Grundsatz
- Keine automatischen, kostenpflichtigen KI-Aufrufe durch den Server. KI-Arbeit geschieht ausschließlich durch manuell gestartete Agenten-Sessions (Claude Code lokal, Cloud Cowork), die über die REST-Schnittstelle auf die App zugreifen.

### 7.2 Agenten-Schnittstelle (REST, tokengeschützt)
- Lese-Endpunkte (Token-Scope `read`):
  - `GET /api/export/spiele` (Spielplan, Stände, Ereignisse, optional `?spiel_id=`)
  - `GET /api/export/teams` (Teams, Kader, Trainer, Taktik, Verletzungen)
  - `GET /api/export/tipps` (Tipps pseudonymisiert, siehe 8.3)
  - `GET /api/export/news` (news_items)
  - `GET /api/export/analysen` (bisherige ki_analysen inkl. Trefferbilanz)
- Schreib-Endpunkte (Token-Scope `write_analysis`, kein Zugriff auf andere Tabellen):
  - `POST /api/analysen` (ki_analyse anlegen: spiel_id, typ, inhalt_markdown, struktur_json)
  - `POST /api/tipps/ki` (Tipp des KI-Nutzers setzen, nur vor Anpfiff)
  - `POST /api/beitraege` (Vorschläge für taktik/verletzung mit quelle=agent, landen zur Sichtung beim Admin)
- Tokens: pro Agent ein Bearer-Token, im Admin-Bereich erzeugbar und widerrufbar, Scopes fest zugeordnet, alle Zugriffe im change_log bzw. Zugriffslog protokolliert. Rate-Limit auf den Export-Endpunkten.

### 7.3 Eigener Skill "Spielbewertung"
Ein eigener Agent-Skill (Datei `skills/spielbewertung/SKILL.md` im Projekt-Repo) standardisiert Prognose und Nachanalyse, damit Bewertungen möglichst genau, konsistent und über das Turnier hinweg lernfähig sind.

Kernablauf des Skills:
1. **Daten ziehen:** Export-Endpunkte abrufen (beide Teams, Form aus bisherigen WM-Spielen, Aufstellungen falls da, Verletzungen, Taktik, Spielort/Reise, bisherige eigene Analysen samt Trefferbilanz).
2. **Prognose (vor Anpfiff):** strukturiert nach festen Faktoren: Form, Kaderverfügbarkeit (Verletzungen/Sperren aus Karten), taktisches Matchup, Turnierkontext (Gruppensituation, Müdigkeit/Reise). Ausgabe als struktur_json:

```json
{
  "typ": "prognose",
  "spiel_id": 42,
  "wahrscheinlichkeiten": {"heim": 0.45, "remis": 0.27, "gast": 0.28},
  "ergebnis_tipp": {"heim": 2, "gast": 1},
  "konfidenz": "mittel",
  "schluesselfaktoren": ["...", "..."],
  "begruendung_kurz": "..."
}
```

3. **Tipp setzen:** `POST /api/tipps/ki` mit dem ergebnis_tipp.
4. **Nachanalyse (nach Abpfiff):** Vergleich Prognose gegen Verlauf (was traf zu, was nicht und warum), Bewertung der Schlüsselszenen aus dem Ticker, kurze Team-Leistungseinschätzung. Ausgabe als ki_analyse typ=nachanalyse.
5. **Kalibrierung (Feedback-Schleife):** Der Skill pflegt eine Lern-Datei (`skills/spielbewertung/LESSONS.md`): Trefferquote, Brier-Score der Wahrscheinlichkeiten, systematische Fehler (z.B. "Favoriten überschätzt", "Remis zu selten getippt"). Jede neue Prognose beginnt mit dem Lesen dieser Datei. So wird der Skill im Turnierverlauf messbar genauer.

Aufruf in der Praxis: eine Claude-Code-Session mit z.B. "Bewerte die heutigen Spiele" lädt den Skill, arbeitet alle anstehenden Spiele ab und postet Prognosen plus KI-Tipps zurück in die App.

---

## 8. Sicherheit und Datenschutz

### 8.1 Zugang und Authentifizierung
- App nur über HTTPS via Tailscale Funnel erreichbar; der Rock64 hat keine offenen Router-Ports.
- Ohne Login ist nichts erreichbar: jede API (außer /api/health, Login, Registrierung) erfordert eine Session; das Frontend zeigt ohne Session nur den Login-Screen.
- Login: Anzeigename + PIN (gehasht, scrypt), Session-Cookie (httpOnly, secure). Brute-Force-Schutz durch Versuchszähler und Sperrzeit.
- Selbst-Registrierung (Ergänzung 10.06.2026): nur mit dem **Gruppen-Passwort** der Tipprunde (`WM26_REGISTRIERUNG_PASSWORT`; leer = Registrierung deaktiviert). Fehlversuche werden pro IP wie Login-Fehlversuche gesperrt. Neue Konten sind immer Rolle `mitglied`.
- Nutzerverwaltung durch den Admin: Nutzer anlegen, löschen (samt Tipps/Pins/Sessions, mit Änderungshistorie), PIN zurücksetzen, KI-Wertung pro Nutzer freischalten/entziehen. Selbstlöschung und Löschen des letzten Admins sind blockiert.
- Admin-Funktionen erfordern die Admin-Rolle plus erneute PIN-Eingabe für kritische Aktionen (Nutzer löschen, Token erzeugen). [Hinweis: erneute PIN-Eingabe ist noch nicht umgesetzt.]
- Auslieferung mit Sicherheits-Headern (CSP ohne Inline-Skripte, X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy).

### 8.2 Personenbezogene Daten (Freundeskreis)
- Gespeichert wird das Minimum: Anzeigename (frei wählbar, kann Pseudonym sein), PIN-Hash, Tipps, Pins, Push-Subscription. Keine E-Mail-Pflicht, kein Tracking.
- Information der Mitspieler beim ersten Login: was gespeichert wird, dass Tipps für alle sichtbar werden (ab Anpfiff) und dass pseudonymisierte Tippdaten an KI-Agenten gehen können. Löschung auf Zuruf durch den Admin (Nutzer löschen entfernt Tipps oder anonymisiert sie). [FILL: kurze Datenschutz-Infoseite formulieren.]

### 8.3 Datenweitergabe an KI-Plattformen
- Über die Export-Endpunkte verlassen ausschließlich Sportdaten und pseudonymisierte Tippdaten (nutzer_id wird zu "Spieler 1..n", Anzeigenamen werden nicht exportiert) das System.
- Niemals exportiert: PIN-Hashes, Push-Subscriptions, Tokens, Zugriffslogs.
- Schreibzugriff der Agenten ist auf Analysen, KI-Tipp und Sichtungs-Vorschläge begrenzt (Scope-Trennung), damit ein fehlgeleiteter Agent keine Spielstände oder Nutzerdaten ändern kann.
- Bewusstsein: An Anthropic (oder eine andere Plattform) übermittelte Inhalte unterliegen deren Verarbeitungsbedingungen; deshalb die Pseudonymisierung als Standard.

### 8.4 Betriebssicherheit
- Secrets (Fußball-API-Key, VAPID-Keys, Agent-Tokens) in einer .env-Datei außerhalb des Web-Roots, nicht im Git.
- SQLite-Schreibzugriffe über eine zentrale Lock-Disziplin (WAL-Modus plus serialisierte Schreib-Transaktionen), analog zur DATA_LOCK-Erfahrung aus dem Lernhub.
- change_log ist append-only und macht jede Datenänderung nachvollziehbar.

---

## 9. Tests

- **Unit-Tests (pytest, wie beim Lernhub):** Punkteberechnung (alle Fälle: exakt, Differenz, Tendenz, K.o.-Sonderfälle), Override-Mergelogik (admin > agent > api), Tipp-Sperre zum Anpfiff, Pseudonymisierung der Exporte.
- **Sync-Tests mit Mock-API:** aufgezeichnete API-Antworten (Fixtures) für Stammdaten, Aufstellung, Live-Delta; Test, dass Deltas korrekt in `ereignis` und SSE-Events übersetzt werden und Overrides unangetastet bleiben.
- **Schnittstellen-Tests:** Token-Scopes (read darf nicht schreiben, write_analysis darf nur Analysen), Rate-Limit, Login/Lockout.
- **Skill-Eval:** Nach jeder Turnierwoche Trefferquote und Brier-Score des KI-Tippers aus der Datenbank ziehen und gegen die LESSONS.md-Erwartung prüfen (manuell oder per Agent).
- Vor jedem Server-Deploy: Testlauf `pytest -q` auf dem Rock64 (eingeübter Workflow).

---

## 10. Wartung und Betrieb

- systemd-User-Service `wm26.service` (Restart-Policy on-failure), Logs über journalctl.
- Nightly-Backup über das vorhandene Timer-Muster: wm26.db plus Git-Bundle in die bestehende Backup-Rotation, Spiegelung auf den Windows-PC (RockBackupPull).
- Lokales Git-Repo, Commit nach jedem Deploy; Betriebsdoku in `docs/BETRIEB.md` im Repo.
- Monitoring leichtgewichtig: Admin-Seite zeigt letzter erfolgreicher Sync je Job, API-Kontingentverbrauch (falls die API das meldet), SSE-Verbindungen. Bei Sync-Fehlern Web-Push an den Admin.
- Nach dem Turnier: Datenbank einfrieren (read-only-Archivmodus), Rangliste und Analysen bleiben abrufbar.

---

## 11. MVP und Roadmap (Etappen während des Turniers)

**Etappe 1, sofort (Ziel: vor den ersten Gruppenspielen, zur Not in den ersten Turniertagen):**
- Spielplan-Import (Teams, Spielorte, Termine), Hauptmenü mit Spielterminen.
- Login (Name + PIN), Tippabgabe mit Anpfiff-Sperre, automatische Punktevergabe, Rangliste.
- Ergebnisse per Spieltags-Sync (noch ohne Minuten-Ticker). Tailscale Funnel live.
- Mobiles Layout gemäß 6.5 für alle Etappe-1-Ansichten von Beginn an (Spielliste, Tippabgabe, Rangliste, Login), inklusive PWA-Installierbarkeit. Der Freundeskreis nutzt die App ab Tag 1 am Handy.
- Verpasste erste Spiele: Ergebnisse werden nachträglich synchronisiert; Tipps sind erst ab App-Start möglich (faire Regel: Wertung erst ab dem ersten gemeinsamen Spiel). [FILL: Startspieltag des Tippspiels mit dem Freundeskreis festlegen.]

**Etappe 2, Gruppenphase:**
- Live-Poll (60 Sek.) + SSE-Ticker, Ereignisanzeige im Spiel-Detail.
- Pins (Spiele/Teams) + Web-Push (Anpfiff, Aufstellung, Tore, Endstand, Tipp-Erinnerung).
- Kader, Spielerprofile, Aufstellungs-Ansicht; RSS-Feeds + News-Ansicht; Admin-Bereich mit Overrides.
- Bonusfragen (spätestens vor der K.o.-Runde: Weltmeister, Torschützenkönig).

**Etappe 3, K.o.-Runde:**
- Agenten-Schnittstelle (Export + Analyse-Schreibendpunkt, Tokens), KI-Tipper-Nutzer.
- Skill "Spielbewertung" inkl. LESSONS.md-Kalibrierung; Prognosen und Nachanalysen sichtbar in der App.
- Taktische Ansichten (Feldgrafik), Team-Taktikprofile.

**Nach dem Turnier / optional:**
- Tippspiel-Import von externen Plattformen, Archivmodus, Wiederverwendung für EM 2028. [FILL: Bedarf prüfen.]

---

## 12. Offene Platzhalter (gesammelt)

1. [FILL: finale Fußball-API-Wahl und Tarif (Kandidaten: API-Football, football-data.org); Kontingent gegen 60-Sekunden-Live-Poll bei bis zu 4 Parallelspielen prüfen]
2. [FILL: konkrete RSS-Feed-Liste]
3. [FILL: K.o.-Regel im Tippspiel: Wertung nach 90 oder 120 Minuten]
4. [FILL: Vorlaufzeit der Tipp-Erinnerung]
5. [FILL: Startspieltag der Tippspiel-Wertung]
6. [FILL: Text der Datenschutz-Infoseite für den Freundeskreis]
7. [FILL: Bedarf an Tippspiel-Import und Zielplattform]
8. [FILL: Geräte-/Browserliste des Freundeskreises für den Mobil-Test (mindestens Android/Chrome und iOS/Safari)]
