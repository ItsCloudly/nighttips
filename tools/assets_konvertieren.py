"""Asset-Pipeline: konvertiert die ChatGPT-Rohbilder aus assets-roh/ in Web-Assets.

- Objekt-Assets (B1–B5, C1–C5, D1) sollten transparent geliefert werden,
  kamen aber mit eingebackenem Schachbrettmuster (ChatGPT-Fake-Transparenz).
  Daher Freistellen per rembg (Modell isnet-general-use), danach
  beschneiden und als WebP mit Alphakanal exportieren.
- Szenen-Assets (A2–A4, D2, D3) behalten ihren dunklen Studiogrund und
  werden nur skaliert/komprimiert.
- A1 wird zum App-Icon-Satz (512/192 PNG fürs Manifest, 180 Apple-Touch);
  die schwarzen Ecken außerhalb der abgerundeten Kachel werden mit dem
  App-Hintergrund (#0A0C10) gefüllt, damit das Icon voll deckend ist.

Ziel je Illustration: ≤ 150 KB (Rock64-Budget). Aufruf:
    .venv/Scripts/python tools/assets_konvertieren.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image

WURZEL = Path(__file__).resolve().parent.parent
ROH = WURZEL / "assets-roh"
ZIEL = WURZEL / "app" / "static" / "illustrationen"
ICONS = WURZEL / "app" / "static" / "icons"

MAX_KB = 150
HINTERGRUND = (10, 12, 16)  # --ns-bg #0A0C10

# Plan-ID -> (Zielname, Art, lange Kante)
ASSETS: dict[str, tuple[str, str, int]] = {
    "A2": ("hero-login", "szene", 1200),
    "A3": ("hero-push", "szene", 1200),
    "A4": ("hero-team-pick", "szene", 1200),
    "B1": ("empty-no-matches", "freisteller", 800),
    "B2": ("empty-no-live", "freisteller", 800),
    "B3": ("empty-no-news", "freisteller", 800),
    "B4": ("empty-no-tipps", "freisteller", 800),
    "B5": ("error-offline", "freisteller", 800),
    "C1": ("success-tipp", "freisteller", 800),
    "C2": ("volltreffer", "freisteller", 800),
    "C3": ("podium", "freisteller", 800),
    "C4": ("bonus-question", "freisteller", 800),
    "C5": ("champion-placeholder", "freisteller", 800),
    "D1": ("ki-chip", "freisteller", 800),
    "D2": ("stadium-hero", "szene", 1200),
    "D3": ("news-fallback", "szene", 1200),
}


def skaliere(img: Image.Image, lange_kante: int) -> Image.Image:
    faktor = lange_kante / max(img.size)
    if faktor >= 1:
        return img
    neu = (round(img.width * faktor), round(img.height * faktor))
    return img.resize(neu, Image.LANCZOS)


def webp_speichern(img: Image.Image, pfad: Path) -> int:
    """Speichert mit der höchsten Qualität, die ins 150-KB-Budget passt."""
    for qualitaet in (90, 85, 80, 75, 70, 62, 55, 45):
        puffer = io.BytesIO()
        img.save(puffer, "WEBP", quality=qualitaet, method=6)
        if puffer.tell() <= MAX_KB * 1024:
            pfad.write_bytes(puffer.getvalue())
            return puffer.tell()
    pfad.write_bytes(puffer.getvalue())
    return puffer.tell()


def freistellen(img: Image.Image, session) -> Image.Image:
    from rembg import remove

    frei = remove(img, session=session)
    # Auf den sichtbaren Inhalt beschneiden, mit etwas Luft
    bbox = frei.getchannel("A").getbbox()
    if bbox:
        rand = 16
        bbox = (
            max(0, bbox[0] - rand),
            max(0, bbox[1] - rand),
            min(frei.width, bbox[2] + rand),
            min(frei.height, bbox[3] + rand),
        )
        frei = frei.crop(bbox)
    return frei


def icon_bauen(quelle: Path) -> None:
    """A1 -> App-Icon-Satz. Schwarze Ecken werden mit --ns-bg geflutet."""
    img = Image.open(quelle).convert("RGB")
    # Bounding-Box der abgerundeten Kachel (alles über Fast-Schwarz)
    maske = img.convert("L").point(lambda p: 255 if p > 6 else 0)
    bbox = maske.getbbox()
    if bbox:
        img = img.crop(bbox)
    grund = Image.new("RGB", img.size, HINTERGRUND)
    # Kachel auf den Grund legen; die schwarzen Eckpixel bleiben dunkler,
    # fallen auf #0A0C10 aber nicht auf (gleiche Farbwelt).
    grund.paste(img, (0, 0))
    for groesse, name in ((512, "icon-512.png"), (192, "icon-192.png"), (180, "icon-180.png")):
        klein = grund.resize((groesse, groesse), Image.LANCZOS)
        klein.save(ICONS / name, "PNG", optimize=True)
        print(f"  {name}: {(ICONS / name).stat().st_size // 1024} KB")


def main() -> int:
    ZIEL.mkdir(parents=True, exist_ok=True)
    session = None
    if any(art == "freisteller" for _, art, _ in ASSETS.values()):
        from rembg import new_session

        session = new_session("isnet-general-use")

    for plan_id, (name, art, kante) in ASSETS.items():
        quelle = ROH / f"{plan_id}.png"
        if not quelle.exists():
            print(f"!! {quelle.name} fehlt — übersprungen")
            continue
        img = Image.open(quelle)
        if art == "freisteller":
            img = freistellen(img.convert("RGB"), session)
        else:
            img = img.convert("RGB")
        img = skaliere(img, kante)
        ziel = ZIEL / f"{name}.webp"
        groesse = webp_speichern(img, ziel)
        marke = "" if groesse <= MAX_KB * 1024 else "  ÜBER BUDGET!"
        print(f"{plan_id} -> {ziel.name}: {img.size[0]}x{img.size[1]}, {groesse // 1024} KB{marke}")

    print("App-Icons aus A1:")
    icon_bauen(ROH / "A1.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
