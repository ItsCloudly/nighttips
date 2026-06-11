"""SSE-Endpunkt für Live-Updates an die Clients (SPEC 4.2).

Events: score, status, ereignis — Payload ist das geänderte Objekt als JSON.
Clients ohne offene Verbindung erhalten Web Push (SPEC 5.5); Fallback im
Frontend ist normales Nachladen der Spielliste.
"""
from __future__ import annotations

import asyncio
import sqlite3
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..abhaengigkeiten import aktueller_nutzer
from ..services.live import broker

router = APIRouter(prefix="/api", tags=["live"])

_HEARTBEAT_SEKUNDEN = 25


async def _event_strom() -> AsyncIterator[str]:
    queue = broker.anmelden()
    try:
        # Reconnect-Hinweis für den Browser und sofortiges erstes Byte,
        # damit Proxies (Funnel) die Verbindung als aktiv erkennen.
        yield "retry: 5000\n\n"
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SEKUNDEN)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        broker.abmelden(queue)


@router.get("/stream")
async def stream(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
) -> StreamingResponse:
    return StreamingResponse(
        _event_strom(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
