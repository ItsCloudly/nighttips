"""Tests für die Live-Schicht: Ereignis-Ableitung aus Sync-Deltas, SSE-Broker,
manuelle Ticker-Einträge des Admins (SPEC 4.1/4.2)."""
from __future__ import annotations

from app.services import live, sync
from tests.test_grunddaten import API_TEAMS, ApiAttrappe, _api_match


def _match_mit(status: str, tore: tuple[int | None, int | None], minute: int | None = None):
    match = _api_match()
    match["status"] = status
    match["score"] = {
        "winner": None,
        "duration": "REGULAR",
        "fullTime": {"home": tore[0], "away": tore[1]},
    }
    if minute is not None:
        match["minute"] = minute
    return match


def _ereignisse(conn):
    return conn.execute(
        "SELECT typ, minute, text, quelle FROM ereignis ORDER BY id"
    ).fetchall()


def test_sync_leitet_ereignisse_aus_deltas_ab(conn, einstellungen):
    # Ausgangslage: Spiel geplant, 0 Ereignisse
    api = ApiAttrappe(API_TEAMS, [_match_mit("TIMED", (None, None))])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    assert _ereignisse(conn) == []

    # Anpfiff: geplant -> live
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (0, 0), minute=1)])
    bericht = sync.ergebnis_sync(conn, einstellungen, api=api)
    assert bericht.ereignisse == 1
    assert _ereignisse(conn)[-1]["typ"] == "anpfiff"

    # Tor für Heim in Minute 23
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (1, 0), minute=23)])
    bericht = sync.ergebnis_sync(conn, einstellungen, api=api)
    assert bericht.ereignisse == 1
    tor = _ereignisse(conn)[-1]
    assert tor["typ"] == "tor"
    assert tor["minute"] == 23
    assert "Deutschland" in tor["text"]
    assert "1:0" in tor["text"]

    # Doppelpack im selben Poll-Intervall: 1:0 -> 1:2 ergibt zwei Tor-Ereignisse
    # mit Zwischenständen (1:1, dann 1:2) statt zweimal dem Endstand
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (1, 2), minute=70)])
    bericht = sync.ergebnis_sync(conn, einstellungen, api=api)
    assert bericht.ereignisse == 2
    doppelpack = _ereignisse(conn)[-2:]
    assert [e["typ"] for e in doppelpack] == ["tor", "tor"]
    assert "1:1" in doppelpack[0]["text"]
    assert "1:2" in doppelpack[1]["text"]

    # VAR-Korrektur: Tor zurückgenommen — zählt erst, wenn der Folgelauf
    # den niedrigeren Stand bestätigt (Schutz gegen API-Flattern)
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (1, 1), minute=72)])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    assert _ereignisse(conn)[-1]["typ"] == "tor"  # noch zurückgehalten
    sync.ergebnis_sync(conn, einstellungen, api=api)
    assert _ereignisse(conn)[-1]["typ"] == "var"

    # Abpfiff
    api = ApiAttrappe(API_TEAMS, [_match_mit("FINISHED", (1, 1))])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    assert _ereignisse(conn)[-1]["typ"] == "abpfiff"


def test_halbzeit_uebergaenge(conn, einstellungen):
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (0, 0))])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    api = ApiAttrappe(API_TEAMS, [_match_mit("PAUSED", (0, 0))])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    assert _ereignisse(conn)[-1]["typ"] == "halbzeit"
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (0, 0))])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    letzte = _ereignisse(conn)[-1]
    assert letzte["typ"] == "freitext"
    assert "2. Halbzeit" in letzte["text"]


def test_doppelte_deltas_erzeugen_keine_doppelten_ereignisse(conn, einstellungen):
    """Race-Nachstellung (z. B. Prozess-Überlappung beim Deploy): dieselben
    Deltas ein zweites Mal verarbeitet → keine doppelten Ticker-Einträge."""
    from app import db

    api = ApiAttrappe(API_TEAMS, [_match_mit("TIMED", (0, 0))])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (1, 0), minute=9)])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    assert [e["typ"] for e in _ereignisse(conn)] == ["anpfiff", "tor"]

    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    with db.schreib_transaktion(conn):
        neue = live.deltas_verarbeiten(
            conn,
            spiel_id=spiel_id,
            deltas={"status": ("geplant", "live"), "tore_heim": (0, 1)},
        )
    assert neue == []
    assert [e["typ"] for e in _ereignisse(conn)] == ["anpfiff", "tor"]


def test_api_flattern_erzeugt_keine_geistertore(conn, einstellungen):
    """Pendelt die API zwischen neuem und altem Stand (Cache-Flattern beim
    4-Sekunden-Poll), bleibt es bei EINEM Tor — keine Geister-Korrekturen."""
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (0, 0))])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (1, 0), minute=12)])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    for tore in ((0, 0), (1, 0), (0, 0), (1, 0)):
        api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", tore)])
        sync.ergebnis_sync(conn, einstellungen, api=api)
    assert [e["typ"] for e in _ereignisse(conn)] == ["tor"]
    spiel = conn.execute("SELECT tore_heim, tore_gast FROM spiel").fetchone()
    assert (spiel["tore_heim"], spiel["tore_gast"]) == (1, 0)


def test_bestaetigte_ruecknahme_wird_uebernommen(conn, einstellungen):
    """Eine echte VAR-Rücknahme kommt durch — einen Poll-Takt später."""
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (0, 0))])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (1, 0), minute=12)])
    sync.ergebnis_sync(conn, einstellungen, api=api)

    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (0, 0), minute=15)])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    spiel = conn.execute("SELECT tore_heim FROM spiel").fetchone()
    assert spiel["tore_heim"] == 1  # erster Lauf: noch zurückgehalten

    sync.ergebnis_sync(conn, einstellungen, api=api)
    spiel = conn.execute("SELECT tore_heim FROM spiel").fetchone()
    assert spiel["tore_heim"] == 0
    letzte = _ereignisse(conn)[-1]
    assert letzte["typ"] == "var"
    assert "0:0" in letzte["text"]


def test_spielzeit_bezugspunkte(conn, einstellungen):
    """live_zeit liefert Anpfiff- und Wiederanpfiff-Bezug für die Minutenanzeige."""
    api = ApiAttrappe(API_TEAMS, [_match_mit("TIMED", (None, None))])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    spiel = conn.execute("SELECT id, anstoss_utc, status FROM spiel").fetchone()
    assert (
        live.spielzeit(
            conn, spiel_id=spiel["id"], anstoss_utc=spiel["anstoss_utc"], status=spiel["status"]
        )
        is None
    )

    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (0, 0))])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    zeit = live.spielzeit(
        conn, spiel_id=spiel["id"], anstoss_utc=spiel["anstoss_utc"], status="live"
    )
    # Anpfiff-Ereignis liegt weit nach der (fiktiven) Anstoßzeit 2030 →
    # Rückfall auf die geplante Zeit; kein Wiederanpfiff bekannt
    assert zeit == {"anpfiff_utc": spiel["anstoss_utc"], "zweite_hz_utc": None}

    api = ApiAttrappe(API_TEAMS, [_match_mit("PAUSED", (0, 0))])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (0, 0))])
    sync.ergebnis_sync(conn, einstellungen, api=api)
    zeit = live.spielzeit(
        conn, spiel_id=spiel["id"], anstoss_utc=spiel["anstoss_utc"], status="live"
    )
    assert zeit["zweite_hz_utc"] is not None

    # Liegt der Anpfiff-Eintrag nah an der geplanten Zeit, ist er der Bezug
    from datetime import timedelta

    from app.zeit import iso_utc, parse_utc

    echt = iso_utc(parse_utc(spiel["anstoss_utc"]) + timedelta(minutes=2))
    conn.execute(
        "INSERT INTO ereignis (spiel_id, typ, erstellt_utc) VALUES (?, 'anpfiff', ?)",
        (spiel["id"], echt),
    )
    conn.commit()
    zeit = live.spielzeit(
        conn, spiel_id=spiel["id"], anstoss_utc=spiel["anstoss_utc"], status="live"
    )
    assert zeit["anpfiff_utc"] == echt


def test_nachtraeglicher_endstand_erzeugt_kein_live_tor(conn, einstellungen):
    """Erstes Update von None auf n Tore (verpasster Spieltag) ist kein Tor-Event."""
    api = ApiAttrappe(API_TEAMS, [_match_mit("TIMED", (None, None))])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    api = ApiAttrappe(API_TEAMS, [_match_mit("FINISHED", (3, 2))])
    bericht = sync.ergebnis_sync(conn, einstellungen, api=api)
    typen = [e["typ"] for e in _ereignisse(conn)]
    assert typen == ["abpfiff"]
    assert bericht.ereignisse == 1


def test_spiel_detail_enthaelt_ereignisse(client, conn, einstellungen):
    from app.services import nutzer as nutzer_service

    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (0, 0))])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (1, 0), minute=10)])
    sync.ergebnis_sync(conn, einstellungen, api=api)

    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="test")
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    detail = client.get(f"/api/spiele/{spiel_id}").json()
    assert detail["ereignisse"]
    assert detail["ereignisse"][0]["typ"] == "tor"  # neueste zuerst


def test_admin_ereignis_anlegen_und_loeschen(client, conn, einstellungen):
    from app.services import nutzer as nutzer_service

    api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (1, 0))])
    sync.stammdaten_sync(conn, einstellungen, api=api)
    nutzer_service.nutzer_anlegen(
        conn, anzeigename="Chef", pin="1234", rolle="admin", akteur="test"
    )
    client.post("/api/login", json={"anzeigename": "Chef", "pin": "1234"})
    spiel_id = conn.execute("SELECT id FROM spiel").fetchone()["id"]
    spieler_id = conn.execute("SELECT id FROM spieler LIMIT 1").fetchone()["id"]

    antwort = client.post(
        f"/api/admin/spiele/{spiel_id}/ereignis",
        json={"typ": "tor", "minute": 23, "spieler_id": spieler_id, "text": "Traumtor"},
    )
    assert antwort.status_code == 201
    eintrag = antwort.json()
    assert eintrag["quelle"] == "admin"
    assert eintrag["spieler_name"]

    geloescht = client.delete(f"/api/admin/ereignisse/{eintrag['id']}")
    assert geloescht.status_code == 204
    assert client.delete("/api/admin/ereignisse/99999").status_code == 404


def test_broker_publiziert_an_clients(conn, einstellungen):
    import asyncio
    import json

    async def szenario():
        loop = asyncio.get_running_loop()
        live.broker.loop_setzen(loop)
        queue = live.broker.anmelden()
        try:
            # publish() ist threadsicher; hier reicht der Aufruf im Loop-Thread,
            # SQLite-Verbindungen sind an ihren Erzeuger-Thread gebunden.
            api = ApiAttrappe(API_TEAMS, [_match_mit("TIMED", (None, None))])
            sync.stammdaten_sync(conn, einstellungen, api=api)
            api = ApiAttrappe(API_TEAMS, [_match_mit("IN_PLAY", (1, 0), minute=9)])
            sync.ergebnis_sync(conn, einstellungen, api=api)
            events = []
            while len(events) < 3:
                events.append(await asyncio.wait_for(queue.get(), timeout=5))
            return events
        finally:
            live.broker.abmelden(queue)
            live.broker.loop_setzen(None)

    events = asyncio.run(szenario())
    typen = sorted(zeile.split("\n")[0] for zeile in events)
    assert typen == ["event: ereignis", "event: score", "event: status"]
    score_event = next(e for e in events if e.startswith("event: score"))
    payload = json.loads(score_event.split("data: ")[1].strip())
    assert payload["tore_heim"] == 1


def test_stream_endpunkt_erfordert_login(client):
    assert client.get("/api/stream").status_code == 401


def test_live_phase_erkennung(conn):
    from datetime import timedelta

    from app import db as db_modul
    from app.scheduler import _poll_phase
    from app.zeit import iso_utc, jetzt_utc

    def spiel_mit(status: str, minuten_bis_anstoss: int) -> None:
        with db_modul.schreib_transaktion(conn):
            conn.execute("DELETE FROM spiel")
            conn.execute(
                "INSERT INTO spiel (runde, anstoss_utc, status) VALUES (?, ?, ?)",
                (
                    "Gruppe A",
                    iso_utc(jetzt_utc() + timedelta(minutes=minuten_bis_anstoss)),
                    status,
                ),
            )

    spiel_mit("geplant", 30)
    assert _poll_phase(conn) == "vorlauf"  # Anpfiff in 30 Minuten
    spiel_mit("geplant", 120)
    assert _poll_phase(conn) == "normal"  # noch zu früh
    spiel_mit("live", -60)
    assert _poll_phase(conn) == "live"  # Spiel läuft → engster Takt
    spiel_mit("halbzeit", -50)
    assert _poll_phase(conn) == "live"
    spiel_mit("beendet", -120)
    assert _poll_phase(conn) == "normal"
    spiel_mit("live", -60 * 5)
    assert _poll_phase(conn) == "normal"  # Sicherheitsnetz: hängender Live-Status
