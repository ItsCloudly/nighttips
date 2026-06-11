-- SQLite-Schema (SPEC Abschnitt 3). Nur additive Anlagen: bestehende
-- Datenbanken werden beim App-Start nie verändert oder geleert.
-- Zeitstempel: ISO-8601-UTC-Text "YYYY-MM-DDTHH:MM:SSZ" (lexikographisch vergleichbar).

CREATE TABLE IF NOT EXISTS team (
    id          INTEGER PRIMARY KEY,
    fifa_code   TEXT UNIQUE,
    name        TEXT NOT NULL,
    gruppe      TEXT,
    flagge_url  TEXT,
    api_ref     TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS spielort (
    id           INTEGER PRIMARY KEY,
    stadion_name TEXT NOT NULL,
    stadt        TEXT,
    -- Beim Sync aus der API ist das Land anfangs unbekannt, daher NULL erlaubt.
    land         TEXT CHECK (land IN ('USA', 'Mexiko', 'Kanada') OR land IS NULL),
    kapazitaet   INTEGER,
    zeitzone     TEXT,
    api_ref      TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS spiel (
    id            INTEGER PRIMARY KEY,
    runde         TEXT NOT NULL,
    anstoss_utc   TEXT NOT NULL,
    spielort_id   INTEGER REFERENCES spielort(id),
    -- Teams sind in der K.o.-Runde anfangs unbekannt, daher NULL erlaubt.
    heim_team_id  INTEGER REFERENCES team(id),
    gast_team_id  INTEGER REFERENCES team(id),
    status        TEXT NOT NULL DEFAULT 'geplant'
                  CHECK (status IN ('geplant', 'live', 'halbzeit', 'beendet', 'abgesagt')),
    tore_heim     INTEGER,
    tore_gast     INTEGER,
    ergebnis_nach TEXT CHECK (ergebnis_nach IN ('90', '120', 'elfmeterschiessen')),
    -- Elfmeterschießen: Sieger getrennt vom (Unentschieden-)Spielstand festhalten.
    elfmeter_sieger_team_id INTEGER REFERENCES team(id),
    api_ref       TEXT UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_spiel_anstoss ON spiel(anstoss_utc);
CREATE INDEX IF NOT EXISTS idx_spiel_status ON spiel(status);

CREATE TABLE IF NOT EXISTS spieler (
    id           INTEGER PRIMARY KEY,
    team_id      INTEGER NOT NULL REFERENCES team(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    trikotnummer INTEGER,
    position     TEXT CHECK (position IN ('Torwart', 'Abwehr', 'Mittelfeld', 'Sturm') OR position IS NULL),
    geburtsdatum TEXT,
    verein       TEXT,
    api_ref      TEXT UNIQUE,
    UNIQUE (team_id, trikotnummer)
);

CREATE INDEX IF NOT EXISTS idx_spieler_team ON spieler(team_id);

CREATE TABLE IF NOT EXISTS trainer (
    id            INTEGER PRIMARY KEY,
    team_id       INTEGER NOT NULL UNIQUE REFERENCES team(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    nationalitaet TEXT,
    api_ref       TEXT UNIQUE
);

-- Letzte Duelle der beiden Mannschaften eines Spiels (SPEC 5.2 "Infos").
CREATE TABLE IF NOT EXISTS direktvergleich (
    id         INTEGER PRIMARY KEY,
    spiel_id   INTEGER NOT NULL REFERENCES spiel(id) ON DELETE CASCADE,
    datum_utc  TEXT,
    wettbewerb TEXT,
    heim_name  TEXT,
    gast_name  TEXT,
    tore_heim  INTEGER,
    tore_gast  INTEGER,
    api_ref    TEXT,
    UNIQUE (spiel_id, api_ref)
);

-- Bilanz der bisherigen Duelle (Free Tier liefert nur Aggregate, keine Einzelspiele).
CREATE TABLE IF NOT EXISTS duell_bilanz (
    spiel_id   INTEGER PRIMARY KEY REFERENCES spiel(id) ON DELETE CASCADE,
    anzahl     INTEGER NOT NULL DEFAULT 0,
    heim_siege INTEGER NOT NULL DEFAULT 0,
    gast_siege INTEGER NOT NULL DEFAULT 0,
    remis      INTEGER NOT NULL DEFAULT 0,
    tore       INTEGER NOT NULL DEFAULT 0
);

-- Live-Ticker-Einträge (SPEC 3.2). Quelle api: aus Sync-Deltas abgeleitet,
-- Quelle admin: manuell nachgetragen (z. B. Minute, Torschütze).
CREATE TABLE IF NOT EXISTS ereignis (
    id           INTEGER PRIMARY KEY,
    spiel_id     INTEGER NOT NULL REFERENCES spiel(id) ON DELETE CASCADE,
    minute       INTEGER,
    typ          TEXT NOT NULL CHECK (typ IN ('tor', 'eigentor', 'elfmeter', 'gelb', 'gelbrot',
                                              'rot', 'wechsel', 'var', 'anpfiff', 'halbzeit',
                                              'abpfiff', 'freitext')),
    -- SET NULL: Ticker-Historie übersteht Kader-Abgleiche (Spieler-Löschungen).
    team_id      INTEGER REFERENCES team(id) ON DELETE SET NULL,
    spieler_id   INTEGER REFERENCES spieler(id) ON DELETE SET NULL,
    spieler2_id  INTEGER REFERENCES spieler(id) ON DELETE SET NULL,
    text         TEXT,
    quelle       TEXT NOT NULL DEFAULT 'api' CHECK (quelle IN ('api', 'admin')),
    erstellt_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ereignis_spiel ON ereignis(spiel_id);

-- Offizielle Gruppentabelle aus dem standings-Endpunkt (1 Call für alle Gruppen).
CREATE TABLE IF NOT EXISTS gruppen_tabelle (
    team_id      INTEGER PRIMARY KEY REFERENCES team(id) ON DELETE CASCADE,
    gruppe       TEXT NOT NULL,
    platz        INTEGER NOT NULL,
    spiele       INTEGER NOT NULL DEFAULT 0,
    siege        INTEGER NOT NULL DEFAULT 0,
    remis        INTEGER NOT NULL DEFAULT 0,
    niederlagen  INTEGER NOT NULL DEFAULT 0,
    tore         INTEGER NOT NULL DEFAULT 0,
    gegentore    INTEGER NOT NULL DEFAULT 0,
    tordifferenz INTEGER NOT NULL DEFAULT 0,
    punkte       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_gruppen_tabelle_gruppe ON gruppen_tabelle(gruppe);

-- Torschützenliste aus dem scorers-Endpunkt (für Anzeige und Bonusfrage Torschützenkönig).
CREATE TABLE IF NOT EXISTS torschuetze (
    api_ref   TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    team_id   INTEGER REFERENCES team(id) ON DELETE SET NULL,
    spiele    INTEGER,
    tore      INTEGER NOT NULL DEFAULT 0,
    vorlagen  INTEGER,
    elfmeter  INTEGER
);

-- Buchführung des Vergleichs-Syncs (1 API-Call je Spiel, daher wiederaufnehmbar).
CREATE TABLE IF NOT EXISTS h2h_abruf (
    spiel_id      INTEGER PRIMARY KEY REFERENCES spiel(id) ON DELETE CASCADE,
    abgerufen_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nutzer (
    id           INTEGER PRIMARY KEY,
    anzeigename  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pin_hash     TEXT NOT NULL,
    rolle        TEXT NOT NULL DEFAULT 'mitglied' CHECK (rolle IN ('admin', 'mitglied', 'ki')),
    erstellt_utc TEXT NOT NULL,
    -- KI-Prognosen/-Analysen sichtbar? Admin/KI sehen sie immer; Mitglieder
    -- nur nach Freischaltung durch den Admin. (Bestands-DBs: Migration in db.py)
    ki_freigeschaltet INTEGER NOT NULL DEFAULT 0,
    -- Taucht das Konto in Wertungs-Ansichten auf (Rangliste, Podium, Top-Tipper)?
    -- 0 z. B. für Test-/Dev-Konten; Tipplisten zeigen sie weiterhin. Nur der
    -- Admin schaltet um. (Bestands-DBs: Migration in db.py)
    rangliste_sichtbar INTEGER NOT NULL DEFAULT 1,
    -- Persönliche Vorlaufzeit der Tipp-Erinnerung in Minuten:
    -- NULL = Server-Standard (WM26_TIPP_ERINNERUNG_MINUTEN), 0 = keine Erinnerung.
    tipp_erinnerung_minuten INTEGER
);

CREATE TABLE IF NOT EXISTS tipp (
    id            INTEGER PRIMARY KEY,
    nutzer_id     INTEGER NOT NULL REFERENCES nutzer(id) ON DELETE CASCADE,
    spiel_id      INTEGER NOT NULL REFERENCES spiel(id) ON DELETE CASCADE,
    tipp_heim     INTEGER NOT NULL CHECK (tipp_heim BETWEEN 0 AND 99),
    tipp_gast     INTEGER NOT NULL CHECK (tipp_gast BETWEEN 0 AND 99),
    abgegeben_utc TEXT NOT NULL,
    punkte        INTEGER,
    UNIQUE (nutzer_id, spiel_id)
);

CREATE INDEX IF NOT EXISTS idx_tipp_spiel ON tipp(spiel_id);

CREATE TABLE IF NOT EXISTS sitzung (
    token_hash   TEXT PRIMARY KEY,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id) ON DELETE CASCADE,
    erstellt_utc TEXT NOT NULL,
    ablauf_utc   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sitzung_ablauf ON sitzung(ablauf_utc);

-- Brute-Force-Schutz: Zähler je Schlüssel ("name:<anzeigename>" bzw. "ip:<adresse>").
CREATE TABLE IF NOT EXISTS login_sperre (
    schluessel              TEXT PRIMARY KEY,
    fehlversuche            INTEGER NOT NULL DEFAULT 0,
    letzter_fehlversuch_utc TEXT,
    gesperrt_bis_utc        TEXT
);

-- RSS-Feeds (SPEC 3.4), im Admin-Bereich gepflegt.
CREATE TABLE IF NOT EXISTS feed (
    id                 INTEGER PRIMARY KEY,
    url                TEXT NOT NULL UNIQUE,
    titel              TEXT,
    aktiv              INTEGER NOT NULL DEFAULT 1,
    letzter_abruf_utc  TEXT
);

-- News-Einträge, dedupliziert über den Link-Hash (SPEC 4.3).
CREATE TABLE IF NOT EXISTS news_item (
    id                  INTEGER PRIMARY KEY,
    feed_id             INTEGER NOT NULL REFERENCES feed(id) ON DELETE CASCADE,
    titel               TEXT NOT NULL,
    link                TEXT NOT NULL,
    link_hash           TEXT NOT NULL UNIQUE,
    zusammenfassung     TEXT,
    veroeffentlicht_utc TEXT,
    team_id             INTEGER REFERENCES team(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_news_veroeffentlicht ON news_item(veroeffentlicht_utc DESC);
CREATE INDEX IF NOT EXISTS idx_news_team ON news_item(team_id);

-- Bonusfragen (SPEC 3.3): z. B. Weltmeister, Torschützenkönig.
CREATE TABLE IF NOT EXISTS bonusfrage (
    id                   INTEGER PRIMARY KEY,
    frage                TEXT NOT NULL,
    typ                  TEXT NOT NULL CHECK (typ IN ('team', 'spieler')),
    punkte_wert          INTEGER NOT NULL DEFAULT 10,
    einsendeschluss_utc  TEXT NOT NULL,
    aufloesung_ref       INTEGER
);

CREATE TABLE IF NOT EXISTS bonustipp (
    id            INTEGER PRIMARY KEY,
    nutzer_id     INTEGER NOT NULL REFERENCES nutzer(id) ON DELETE CASCADE,
    bonusfrage_id INTEGER NOT NULL REFERENCES bonusfrage(id) ON DELETE CASCADE,
    antwort_ref   INTEGER NOT NULL,
    abgegeben_utc TEXT NOT NULL,
    punkte        INTEGER,
    UNIQUE (nutzer_id, bonusfrage_id)
);

-- Private Spiel-Notizen (v0.1.1): eigene Gedanken je Nutzer und Spiel.
-- Strikt privat — taucht in keiner Tippliste und keinem Agenten-Export auf.
CREATE TABLE IF NOT EXISTS notiz (
    id            INTEGER PRIMARY KEY,
    nutzer_id     INTEGER NOT NULL REFERENCES nutzer(id) ON DELETE CASCADE,
    spiel_id      INTEGER NOT NULL REFERENCES spiel(id) ON DELETE CASCADE,
    text          TEXT NOT NULL,
    erstellt_utc  TEXT NOT NULL,
    geaendert_utc TEXT NOT NULL,
    UNIQUE (nutzer_id, spiel_id)
);

CREATE INDEX IF NOT EXISTS idx_notiz_nutzer ON notiz(nutzer_id);

-- Feedback/Fehlermeldungen (v0.1.1): Nutzer melden aus der App heraus,
-- der Admin sichtet den Posteingang in der Verwaltung.
CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id) ON DELETE CASCADE,
    kategorie    TEXT NOT NULL CHECK (kategorie IN ('fehler', 'idee', 'sonstiges')),
    nachricht    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'offen' CHECK (status IN ('offen', 'erledigt')),
    erstellt_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback(status, erstellt_utc DESC);

-- Admin-Overrides (SPEC 3.5): überdauern API-Syncs, Priorität admin > api.
CREATE TABLE IF NOT EXISTS override (
    id           INTEGER PRIMARY KEY,
    entitaet     TEXT NOT NULL,
    entitaet_id  INTEGER NOT NULL,
    feld         TEXT NOT NULL,
    wert         TEXT,
    gesetzt_von  INTEGER REFERENCES nutzer(id),
    gesetzt_utc  TEXT NOT NULL,
    aktiv        INTEGER NOT NULL DEFAULT 1,
    UNIQUE (entitaet, entitaet_id, feld)
);

-- Pins: markierte Spiele/Teams je Nutzer (SPEC 3.3) — eigene Sektion + Push.
CREATE TABLE IF NOT EXISTS pin (
    id           INTEGER PRIMARY KEY,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id) ON DELETE CASCADE,
    typ          TEXT NOT NULL CHECK (typ IN ('spiel', 'team')),
    ref_id       INTEGER NOT NULL,
    erstellt_utc TEXT NOT NULL,
    UNIQUE (nutzer_id, typ, ref_id)
);

CREATE INDEX IF NOT EXISTS idx_pin_nutzer ON pin(nutzer_id);

-- Web-Push-Abos (SPEC 3.3); ein Nutzer kann mehrere Geräte registrieren.
CREATE TABLE IF NOT EXISTS push_subscription (
    id           INTEGER PRIMARY KEY,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id) ON DELETE CASCADE,
    endpoint     TEXT NOT NULL UNIQUE,
    p256dh       TEXT NOT NULL,
    auth         TEXT NOT NULL,
    erstellt_utc TEXT NOT NULL
);

-- Versand-Buchführung: verhindert doppelte Pushes je Anlass/Bezug/Nutzer
-- (z. B. Tipp-Erinnerung nur einmal pro Spiel).
CREATE TABLE IF NOT EXISTS push_versand (
    anlass       TEXT NOT NULL,
    ref_id       INTEGER NOT NULL,
    nutzer_id    INTEGER NOT NULL REFERENCES nutzer(id) ON DELETE CASCADE,
    gesendet_utc TEXT NOT NULL,
    PRIMARY KEY (anlass, ref_id, nutzer_id)
);

-- Änderungshistorie, append-only (SPEC 3.5).
CREATE TABLE IF NOT EXISTS change_log (
    id            INTEGER PRIMARY KEY,
    entitaet      TEXT NOT NULL,
    entitaet_id   INTEGER NOT NULL,
    feld          TEXT NOT NULL,
    alt_wert      TEXT,
    neu_wert      TEXT,
    quelle        TEXT NOT NULL CHECK (quelle IN ('api', 'rss', 'admin', 'agent')),
    akteur        TEXT NOT NULL,
    zeitpunkt_utc TEXT NOT NULL
);

-- Agent-Tokens (SPEC 7.2): Bearer-Tokens mit festen Scopes, widerrufbar.
CREATE TABLE IF NOT EXISTS agent_token (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE,
    token_hash     TEXT NOT NULL UNIQUE,
    scopes         TEXT NOT NULL,             -- CSV: read,write_analysis
    erstellt_utc   TEXT NOT NULL,
    widerrufen_utc TEXT
);

-- KI-Analysen (SPEC 3.4): Prognosen und Nachanalysen, versioniert.
CREATE TABLE IF NOT EXISTS ki_analyse (
    id               INTEGER PRIMARY KEY,
    spiel_id         INTEGER NOT NULL REFERENCES spiel(id) ON DELETE CASCADE,
    typ              TEXT NOT NULL CHECK (typ IN ('prognose', 'nachanalyse')),
    inhalt_markdown  TEXT NOT NULL,
    struktur_json    TEXT,
    agent_name       TEXT NOT NULL,
    erstellt_utc     TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_ki_analyse_spiel ON ki_analyse(spiel_id, typ, version);

-- Taktikprofil je Team (SPEC 3.1), manuell und agentengepflegt.
CREATE TABLE IF NOT EXISTS taktik (
    id           INTEGER PRIMARY KEY,
    team_id      INTEGER NOT NULL UNIQUE REFERENCES team(id) ON DELETE CASCADE,
    formation    TEXT,
    beschreibung TEXT,
    staerken     TEXT,
    schwaechen   TEXT,
    quelle       TEXT NOT NULL DEFAULT 'admin' CHECK (quelle IN ('admin', 'agent')),
    stand_utc    TEXT NOT NULL
);

-- Verletzungen/Ausfälle (SPEC 3.2), geprueft = Admin-Bestätigung.
CREATE TABLE IF NOT EXISTS verletzung (
    id           INTEGER PRIMARY KEY,
    spieler_id   INTEGER NOT NULL REFERENCES spieler(id) ON DELETE CASCADE,
    beschreibung TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'fraglich'
                 CHECK (status IN ('fraglich', 'faellt aus', 'wieder fit')),
    quelle       TEXT NOT NULL DEFAULT 'admin' CHECK (quelle IN ('rss', 'admin', 'agent')),
    gemeldet_utc TEXT NOT NULL,
    geprueft     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_verletzung_spieler ON verletzung(spieler_id);

-- Agenten-Vorschläge zur Admin-Sichtung (SPEC 7.2, POST /api/beitraege).
CREATE TABLE IF NOT EXISTS beitrag (
    id           INTEGER PRIMARY KEY,
    typ          TEXT NOT NULL CHECK (typ IN ('taktik', 'verletzung')),
    team_id      INTEGER REFERENCES team(id) ON DELETE SET NULL,
    spieler_id   INTEGER REFERENCES spieler(id) ON DELETE SET NULL,
    inhalt_json  TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'offen'
                 CHECK (status IN ('offen', 'uebernommen', 'verworfen')),
    erstellt_utc TEXT NOT NULL
);

-- Sync-Überblick für die Admin-Ansicht (SPEC 10).
CREATE TABLE IF NOT EXISTS sync_status (
    job                 TEXT PRIMARY KEY,
    letzter_versuch_utc TEXT,
    letzter_erfolg_utc  TEXT,
    status              TEXT,
    detail              TEXT
);
