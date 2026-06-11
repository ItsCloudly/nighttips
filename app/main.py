"""App-Fabrik: FastAPI-Anwendung mit Routern, statischem Frontend und Scheduler."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from . import db, scheduler
from . import APP_VERSION
from .config import Einstellungen, lade_einstellungen
from .routers import (
    admin,
    agenten,
    auth,
    feedback,
    news_bonus,
    notizen,
    pins,
    profil,
    spiele,
    stream,
    tipps,
)
from .services.live import broker

logger = logging.getLogger("wm26")

STATIC_DIR = Path(__file__).with_name("static")


def create_app(einstellungen: Einstellungen | None = None) -> FastAPI:
    if einstellungen is None:
        einstellungen = lade_einstellungen()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = db.verbinden(einstellungen.db_pfad)
        try:
            db.schema_anlegen(conn)
        finally:
            conn.close()
        broker.loop_setzen(asyncio.get_running_loop())
        stop_ereignis = asyncio.Event()
        sync_task: asyncio.Task | None = None
        if einstellungen.sync_intervall_minuten > 0 and einstellungen.api_token:
            sync_task = asyncio.create_task(
                scheduler.sync_schleife(einstellungen, stop_ereignis)
            )
        elif einstellungen.sync_intervall_minuten > 0:
            logger.warning("Kein WM26_API_TOKEN gesetzt — automatischer Sync bleibt aus")
        yield
        if sync_task is not None:
            stop_ereignis.set()
            await sync_task
        broker.loop_setzen(None)

    app = FastAPI(title="WM26", version=APP_VERSION, lifespan=lifespan)
    app.state.einstellungen = einstellungen

    # Sicherheits-Header für den öffentlichen Betrieb hinter Tailscale Funnel.
    # Inline-Skripte sind tabu (boot.js ist deshalb extern); Inline-Styles nutzt
    # das Frontend in Templates, daher style-src 'unsafe-inline'. Flaggen kommen
    # von crests.football-data.org → img-src https:.
    csp = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' https: data:; font-src 'self'; connect-src 'self'; "
        "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; "
        "base-uri 'self'; form-action 'self'; object-src 'none'"
    )

    @app.middleware("http")
    async def sicherheits_header(request: Request, call_next) -> Response:
        antwort: Response = await call_next(request)
        antwort.headers.setdefault("Content-Security-Policy", csp)
        antwort.headers.setdefault("X-Content-Type-Options", "nosniff")
        antwort.headers.setdefault("X-Frame-Options", "DENY")
        antwort.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        antwort.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        # Die App ist über Tailscale Funnel ausschließlich per HTTPS erreichbar.
        # Bewusst OHNE includeSubDomains/preload: <geraet>.<tailnet>.ts.net teilt die
        # übergeordnete Domain mit fremden Tailscale-Knoten, die HSTS nicht erben sollen.
        if einstellungen.cookie_secure:
            antwort.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000"
            )
        return antwort

    app.include_router(auth.router)
    app.include_router(spiele.router)
    app.include_router(tipps.router)
    app.include_router(admin.router)
    app.include_router(stream.router)
    app.include_router(pins.router)
    app.include_router(news_bonus.router)
    app.include_router(notizen.router)
    app.include_router(feedback.router)
    app.include_router(profil.router)
    app.include_router(agenten.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": APP_VERSION}

    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app


app = create_app()
