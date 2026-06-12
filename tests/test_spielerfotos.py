"""Tests für den Spielerfoto-Endpunkt (v0.2, tools/kader_sync.py)."""
from __future__ import annotations

import pytest

from app.services import importer, nutzer as nutzer_service


@pytest.fixture
def welt(conn, client):
    importer.spielplan_importieren(
        conn,
        {"teams": [{"fifa_code": "GER", "name": "Deutschland", "gruppe": "E"}], "spiele": []},
        akteur="test",
    )
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="123456", akteur="t")
    team_id = conn.execute("SELECT id FROM team").fetchone()["id"]
    conn.execute(
        "INSERT INTO spieler (team_id, name) VALUES (?, 'Testspieler')", (team_id,)
    )
    conn.commit()
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "123456"})
    return conn.execute("SELECT id FROM spieler").fetchone()["id"]


def test_spielerfoto_404_ohne_foto(client, welt):
    assert client.get(f"/api/spielerfotos/{welt}").status_code == 404
    assert client.get("/api/spielerfotos/99999").status_code == 404


def test_spielerfoto_ausgeliefert(client, conn, welt, einstellungen):
    verzeichnis = einstellungen.db_pfad.parent / "spielerfotos"
    verzeichnis.mkdir(parents=True, exist_ok=True)
    (verzeichnis / f"{welt}.webp").write_bytes(b"RIFF0000WEBPVP8 ")
    conn.execute("UPDATE spieler SET foto = ? WHERE id = ?", (f"{welt}.webp", welt))
    conn.commit()
    antwort = client.get(f"/api/spielerfotos/{welt}")
    assert antwort.status_code == 200
    assert antwort.headers["content-type"] == "image/webp"
    assert "private" in antwort.headers["cache-control"]
    # Kaderliste des Teams liefert das foto-Feld mit
    team_id = conn.execute("SELECT team_id FROM spieler WHERE id = ?", (welt,)).fetchone()[
        "team_id"
    ]
    kader = client.get(f"/api/teams/{team_id}").json()["kader"]
    assert kader[0]["foto"] == f"{welt}.webp"


def test_spielerfoto_pfad_schutz(client, conn, welt):
    """Ein manipulierter Dateiname in der DB darf nie aus dem Ordner führen."""
    conn.execute("UPDATE spieler SET foto = '../wm26.db' WHERE id = ?", (welt,))
    conn.commit()
    assert client.get(f"/api/spielerfotos/{welt}").status_code == 404