"""Erzeugt die PWA-PNG-Icons (180/192/512) ohne Zusatzpakete.

Motiv wie icons/icon.svg: dunkelgrüne Kachel, heller Ball, dunkles Fünfeck.
Aufruf: python tools/icon_gen.py
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

HINTERGRUND = (14, 31, 22)
BALL = (242, 244, 239)
FUENFECK = (14, 31, 22)

ZIEL = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"


def _punkt_im_fuenfeck(x: float, y: float, mx: float, my: float, radius: float) -> bool:
    ecken = [
        (mx + radius * math.sin(2 * math.pi * i / 5), my - radius * math.cos(2 * math.pi * i / 5))
        for i in range(5)
    ]
    innen = True
    for i in range(5):
        x1, y1 = ecken[i]
        x2, y2 = ecken[(i + 1) % 5]
        if (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1) > 0:
            innen = False
            break
    return innen


def bild_erzeugen(groesse: int) -> bytes:
    mitte = groesse / 2
    ball_radius = groesse * 0.30
    fuenfeck_radius = groesse * 0.13
    zeilen = bytearray()
    for y in range(groesse):
        zeilen.append(0)  # Filtertyp 0 je Scanline
        for x in range(groesse):
            abstand = math.hypot(x - mitte, y - mitte)
            if abstand <= ball_radius:
                if _punkt_im_fuenfeck(x, y, mitte, mitte, fuenfeck_radius):
                    farbe = FUENFECK
                else:
                    farbe = BALL
            else:
                farbe = HINTERGRUND
            zeilen.extend(farbe)
    return bytes(zeilen)


def _chunk(typ: bytes, daten: bytes) -> bytes:
    return (
        struct.pack(">I", len(daten))
        + typ
        + daten
        + struct.pack(">I", zlib.crc32(typ + daten) & 0xFFFFFFFF)
    )


def png_schreiben(pfad: Path, groesse: int) -> None:
    ihdr = struct.pack(">IIBBBBB", groesse, groesse, 8, 2, 0, 0, 0)  # 8 Bit, RGB
    idat = zlib.compress(bild_erzeugen(groesse), 9)
    pfad.write_bytes(
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    )
    print(f"{pfad.name}: {groesse}x{groesse}")


if __name__ == "__main__":
    ZIEL.mkdir(parents=True, exist_ok=True)
    for groesse, name in ((180, "icon-180.png"), (192, "icon-192.png"), (512, "icon-512.png")):
        png_schreiben(ZIEL / name, groesse)
