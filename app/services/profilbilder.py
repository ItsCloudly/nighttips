"""Profilbilder (v0.1.1): Upload-Verarbeitung mit Pillow.

Jedes Bild wird verifiziert und IMMER neu kodiert (256×256 WebP, zentriert
beschnitten) — präparierte Dateien überleben den Weg auf die Platte nicht.
Ablage in daten/profilbilder/ (gitignored, im selben Ordner wie die DB und
damit im Nightly-Backup); der Zeitstempel im Dateinamen macht die
Auslieferungs-URL cache-sicher.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..config import Einstellungen
from ..zeit import jetzt_utc

MAX_BYTES = 2 * 1024 * 1024  # Roh-Upload; nach dem Re-Encode bleiben ~20 KB übrig
KANTE = 256

# Dekompressionsbomben früh abfangen (Standard-Limit wäre erst ~178 MP)
Image.MAX_IMAGE_PIXELS = 30_000_000


class BildFehler(Exception):
    pass


def verzeichnis(einstellungen: Einstellungen) -> Path:
    return einstellungen.db_pfad.parent / "profilbilder"


def _sicherer_name(dateiname: str | None) -> bool:
    return bool(
        dateiname
        and "/" not in dateiname
        and "\\" not in dateiname
        and ".." not in dateiname
    )


def pfad(einstellungen: Einstellungen, dateiname: str | None) -> Path | None:
    """Validierter Pfad zu einem gespeicherten Bild — None bei Unfug."""
    if not _sicherer_name(dateiname):
        return None
    ziel = verzeichnis(einstellungen) / dateiname
    return ziel if ziel.is_file() else None


def verarbeiten(rohdaten: bytes) -> bytes:
    """Bild prüfen, quadratisch beschneiden, auf 256 px verkleinern, als WebP kodieren."""
    try:
        Image.open(io.BytesIO(rohdaten)).verify()
        bild = Image.open(io.BytesIO(rohdaten)).convert("RGB")
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as fehler:
        raise BildFehler("Die Datei ist kein lesbares Bild.") from fehler
    breite, hoehe = bild.size
    kante = min(breite, hoehe)
    links = (breite - kante) // 2
    oben = (hoehe - kante) // 2
    bild = bild.crop((links, oben, links + kante, oben + kante))
    bild = bild.resize((KANTE, KANTE), Image.LANCZOS)
    ausgabe = io.BytesIO()
    bild.save(ausgabe, "WEBP", quality=82, method=6)
    return ausgabe.getvalue()


def speichern(
    einstellungen: Einstellungen, nutzer_id: int, rohdaten: bytes, *, alt: str | None
) -> str:
    daten = verarbeiten(rohdaten)
    ordner = verzeichnis(einstellungen)
    ordner.mkdir(parents=True, exist_ok=True)
    stempel = jetzt_utc().strftime("%Y%m%d%H%M%S")
    name = f"{nutzer_id}-{stempel}.webp"
    (ordner / name).write_bytes(daten)
    if alt and alt != name:
        loeschen(einstellungen, alt)
    return name


def loeschen(einstellungen: Einstellungen, dateiname: str | None) -> None:
    if not _sicherer_name(dateiname):
        return
    try:
        (verzeichnis(einstellungen) / dateiname).unlink(missing_ok=True)
    except OSError:
        pass  # verwaiste Datei räumt das nächste Speichern bzw. der Admin weg
