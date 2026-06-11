"""Tests für den In-Process-Rate-Limiter.

Wichtig: Die amortisierte Reinigung muss jeden Schlüssel mit SEINEM Fenster
kürzen — sonst leert ein 60-s-Login-Aufruf die längeren Zähler (PIN-Wechsel
600 s) vorzeitig und hebelt deren Limits aus.
"""
from __future__ import annotations

from app import ratelimit


def test_limit_und_gleitendes_fenster(monkeypatch):
    zeit = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: zeit[0])
    for _ in range(3):
        assert ratelimit.erlaubt("x", limit=3, fenster_sekunden=60)
    assert not ratelimit.erlaubt("x", limit=3, fenster_sekunden=60)
    zeit[0] += 61
    assert ratelimit.erlaubt("x", limit=3, fenster_sekunden=60)


def test_aufraeumen_respektiert_fenster_je_schluessel(monkeypatch):
    zeit = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: zeit[0])
    # PIN-Zähler (600-s-Fenster) ausschöpfen
    for _ in range(5):
        assert ratelimit.erlaubt("pinwechsel:1", limit=5, fenster_sekunden=600)
    assert not ratelimit.erlaubt("pinwechsel:1", limit=5, fenster_sekunden=600)
    # 61 s später stößt ein Login-Aufruf (60-s-Fenster) die globale Reinigung an
    zeit[0] += 61
    assert ratelimit.erlaubt("login:1.2.3.4", limit=12, fenster_sekunden=60)
    # Der PIN-Zähler darf dadurch nicht geleert worden sein
    assert not ratelimit.erlaubt("pinwechsel:1", limit=5, fenster_sekunden=600)
