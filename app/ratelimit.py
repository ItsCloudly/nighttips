"""Schlanker In-Process-Rate-Limiter (gleitendes Fenster, pro Schlüssel).

Sitzt VOR den teuren Login-/Registrierungspfaden und wehrt unauthentifizierte
Fluten ab, bevor sie scrypt auslösen (CPU-DoS-Schutz auf dem Rock64). Bewusst
prozesslokal: der Service läuft als ein uvicorn-Worker (siehe deploy/wm26.service).
Bei mehreren Workern wäre ein geteilter Speicher (Redis o. ä.) nötig.
"""
from __future__ import annotations

import threading
import time
from collections import deque

_lock = threading.Lock()
_treffer: dict[str, deque[float]] = {}
# Fenstergröße je Schlüssel — die Reinigung muss jeden Zähler mit SEINEM
# Fenster kürzen. Mit dem Fenster des Aufrufers würde ein 60-s-Login-Aufruf
# die längeren Zähler (PIN-Wechsel 600 s, Feedback 3600 s) vorzeitig leeren
# und deren Limits aushebeln.
_fenster: dict[str, float] = {}
_LETZTE_REINIGUNG = [0.0]


def _aufraeumen(jetzt: float) -> None:
    """Leere Fenster-Deques entfernen, damit der Speicher nicht unbegrenzt wächst."""
    for schluessel in list(_treffer.keys()):
        eintraege = _treffer[schluessel]
        fenster = _fenster.get(schluessel, 3600.0)
        while eintraege and eintraege[0] <= jetzt - fenster:
            eintraege.popleft()
        if not eintraege:
            del _treffer[schluessel]
            _fenster.pop(schluessel, None)


def erlaubt(schluessel: str, *, limit: int, fenster_sekunden: float) -> bool:
    """True, wenn unter dem Limit (und zählt den Treffer); False, wenn überschritten.

    Gleitendes Fenster: erlaubt höchstens `limit` Treffer je `fenster_sekunden`.
    Konvention: Ein Schlüssel(-Präfix) verwendet immer dieselbe Fenstergröße —
    bei wechselnden Werten gewinnt für die Reinigung der zuletzt verwendete.
    """
    if limit <= 0:
        return True
    jetzt = time.monotonic()
    with _lock:
        eintraege = _treffer.get(schluessel)
        if eintraege is None:
            eintraege = deque()
            _treffer[schluessel] = eintraege
        _fenster[schluessel] = fenster_sekunden
        grenze = jetzt - fenster_sekunden
        while eintraege and eintraege[0] <= grenze:
            eintraege.popleft()
        if len(eintraege) >= limit:
            return False
        eintraege.append(jetzt)
        # Gelegentlich global aufräumen (höchstens alle 60 s), günstig amortisiert.
        if jetzt - _LETZTE_REINIGUNG[0] > 60:
            _LETZTE_REINIGUNG[0] = jetzt
            _aufraeumen(jetzt)
        return True


def zuruecksetzen() -> None:
    """Nur für Tests: kompletten Zustand leeren."""
    with _lock:
        _treffer.clear()
        _fenster.clear()
        _LETZTE_REINIGUNG[0] = 0.0
