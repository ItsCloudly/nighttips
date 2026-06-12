"""Tests für den Gruppenchat (v0.2): schreiben, lesen, reagieren, Flutschutz."""
from __future__ import annotations

import pytest

from app.services import chat, nutzer as nutzer_service


@pytest.fixture
def zwei_nutzer(client, conn):
    nutzer_service.nutzer_anlegen(conn, anzeigename="Mia", pin="1234", akteur="t")
    nutzer_service.nutzer_anlegen(conn, anzeigename="Tom", pin="1234", akteur="t")
    conn.commit()
    ids = {
        zeile["anzeigename"]: zeile["id"]
        for zeile in conn.execute("SELECT id, anzeigename FROM nutzer").fetchall()
    }
    client.post("/api/login", json={"anzeigename": "Mia", "pin": "1234"})
    return ids


def _als(client, name: str) -> None:
    client.post("/api/login", json={"anzeigename": name, "pin": "1234"})


def test_chat_schreiben_und_lesen(client, zwei_nutzer):
    erste = client.post("/api/chat", json={"inhalt": "Moin Runde! ⚽"})
    assert erste.status_code == 201
    daten = erste.json()
    assert daten["anzeigename"] == "Mia"
    assert daten["reaktionen"] == []

    _als(client, "Tom")
    client.post("/api/chat", json={"inhalt": "Moin Mia!"})

    antwort = client.get("/api/chat").json()
    assert [n["inhalt"] for n in antwort["nachrichten"]] == ["Moin Runde! ⚽", "Moin Mia!"]
    assert antwort["aeltere_vorhanden"] is False
    assert antwort["emojis"] == list(chat.REAKTIONS_EMOJIS)


def test_chat_validierung(client, zwei_nutzer):
    assert client.post("/api/chat", json={"inhalt": ""}).status_code == 422
    assert client.post("/api/chat", json={"inhalt": "   "}).status_code == 422
    assert client.post("/api/chat", json={"inhalt": "x" * 501}).status_code == 422
    assert client.get("/api/chat").json()["nachrichten"] == []


def test_chat_nur_angemeldet(client):
    assert client.get("/api/chat").status_code == 401
    assert client.post("/api/chat", json={"inhalt": "hi"}).status_code == 401


def test_chat_ratelimit(client, zwei_nutzer):
    for i in range(10):
        assert client.post("/api/chat", json={"inhalt": f"Nachricht {i}"}).status_code == 201
    gebremst = client.post("/api/chat", json={"inhalt": "eine zu viel"})
    assert gebremst.status_code == 429


def test_chat_pagination(client, zwei_nutzer):
    for i in range(7):
        client.post("/api/chat", json={"inhalt": f"Nr. {i}"})
    seite = client.get("/api/chat?limit=3").json()
    assert [n["inhalt"] for n in seite["nachrichten"]] == ["Nr. 4", "Nr. 5", "Nr. 6"]
    assert seite["aeltere_vorhanden"] is True
    aeltere = client.get(f"/api/chat?limit=3&vor_id={seite['nachrichten'][0]['id']}").json()
    assert [n["inhalt"] for n in aeltere["nachrichten"]] == ["Nr. 1", "Nr. 2", "Nr. 3"]


def test_reaktion_setzen_wechseln_entfernen(client, zwei_nutzer):
    nachricht = client.post("/api/chat", json={"inhalt": "Wer tippt heute noch?"}).json()
    mia_id = zwei_nutzer["Mia"]
    tom_id = zwei_nutzer["Tom"]

    # Tom reagiert auf Mias Nachricht
    _als(client, "Tom")
    antwort = client.put(f"/api/chat/{nachricht['id']}/reaktion", json={"emoji": "👍"})
    assert antwort.status_code == 200
    assert antwort.json()["reaktionen"] == [
        {"emoji": "👍", "anzahl": 1, "nutzer_ids": [tom_id]}
    ]

    # Mia reagiert mit demselben Emoji → Anzahl 2
    _als(client, "Mia")
    antwort = client.put(f"/api/chat/{nachricht['id']}/reaktion", json={"emoji": "👍"})
    reaktion = antwort.json()["reaktionen"][0]
    assert reaktion["anzahl"] == 2
    assert sorted(reaktion["nutzer_ids"]) == sorted([mia_id, tom_id])

    # Mia wechselt das Emoji → ihre alte Reaktion verschwindet (eine je Nutzer)
    antwort = client.put(f"/api/chat/{nachricht['id']}/reaktion", json={"emoji": "⚽"})
    reaktionen = {r["emoji"]: r for r in antwort.json()["reaktionen"]}
    assert reaktionen["👍"]["anzahl"] == 1
    assert reaktionen["⚽"]["nutzer_ids"] == [mia_id]

    # Mia entfernt ihre Reaktion
    antwort = client.delete(f"/api/chat/{nachricht['id']}/reaktion")
    assert {r["emoji"] for r in antwort.json()["reaktionen"]} == {"👍"}


def test_reaktion_validierung(client, zwei_nutzer):
    nachricht = client.post("/api/chat", json={"inhalt": "Hm"}).json()
    assert (
        client.put(f"/api/chat/{nachricht['id']}/reaktion", json={"emoji": "🦄"}).status_code
        == 422
    )
    assert client.put("/api/chat/99999/reaktion", json={"emoji": "👍"}).status_code == 404


def test_reaktion_entfernen_404_ohne_broadcast_und_mit_ratelimit(
    client, zwei_nutzer, monkeypatch
):
    """Review-Fix v0.2: unbekannte ids lösen keinen SSE-Broadcast aus, und
    PUT + DELETE teilen sich das 30/Min-Reaktionsbudget."""
    from app.services import live

    events: list[str] = []
    monkeypatch.setattr(live.broker, "publish", lambda ev, daten: events.append(ev))

    assert client.delete("/api/chat/99999/reaktion").status_code == 404
    assert events == []

    # Der 404-DELETE oben hat bereits 1 vom 30er-Budget verbraucht (gewollt —
    # auch Fehlversuche bremsen): 29 PUTs füllen den Rest, dann ist Schluss.
    nachricht = client.post("/api/chat", json={"inhalt": "Budget-Test"}).json()
    for _ in range(29):
        assert (
            client.put(
                f"/api/chat/{nachricht['id']}/reaktion", json={"emoji": "👍"}
            ).status_code
            == 200
        )
    assert client.delete(f"/api/chat/{nachricht['id']}/reaktion").status_code == 429


def test_chat_sse_publikation(client, zwei_nutzer, monkeypatch):
    """Neue Nachrichten und Reaktions-Updates gehen an den SSE-Broker."""
    from app.services import live

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(live.broker, "publish", lambda ev, daten: events.append((ev, daten)))

    nachricht = client.post("/api/chat", json={"inhalt": "Live dabei?"}).json()
    client.put(f"/api/chat/{nachricht['id']}/reaktion", json={"emoji": "❤️"})

    typen = [ev for ev, _ in events]
    assert typen == ["chat", "chat_reaktion"]
    assert events[0][1]["inhalt"] == "Live dabei?"
    assert events[1][1]["nachricht_id"] == nachricht["id"]
    assert events[1][1]["reaktionen"][0]["emoji"] == "❤️"
