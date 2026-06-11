"""RSS-/Atom-Abruf und News-Verwaltung (SPEC 4.3, 5.6).

Bewusst stdlib-only geparst (xml.etree): RSS 2.0 (<item>) und Atom (<entry>).
Deduplizierung über den SHA-256-Hash des Links; einfache Team-Zuordnung über
Namens-Matching gegen die deutschen Teamnamen (vom Admin korrigierbar).
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from email.utils import parsedate_to_datetime

import httpx

# defusedxml schützt gegen XML-Bomben/XXE — Feeds sind fremde Eingaben.
from defusedxml import ElementTree as ET

from .. import db
from ..zeit import iso_utc, jetzt_iso, jetzt_utc, parse_utc

logger = logging.getLogger("wm26.news")

_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_TAG_RE = re.compile(r"<[^>]+>")
_MAX_ZUSAMMENFASSUNG = 500


@dataclass
class AbrufBericht:
    feeds: int = 0
    neu: int = 0
    fehler: int = 0


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    text = "".join(element.itertext()).strip()
    return html.unescape(_TAG_RE.sub(" ", text)).strip()


def _datum_normalisieren(roh: str) -> str | None:
    if not roh:
        return None
    try:
        return iso_utc(parsedate_to_datetime(roh))  # RFC 822 (RSS)
    except (ValueError, TypeError):
        pass
    try:
        return iso_utc(parse_utc(roh))  # ISO 8601 (Atom)
    except (ValueError, TypeError):
        return None


def eintraege_parsen(xml_text: str) -> list[dict]:
    """Liefert {titel, link, zusammenfassung, veroeffentlicht_utc} je Eintrag."""
    wurzel = ET.fromstring(xml_text)
    eintraege: list[dict] = []
    # RSS 2.0
    for item in wurzel.iter("item"):
        link = _text(item.find("link")) or (item.findtext("guid") or "").strip()
        titel = _text(item.find("title"))
        if not link or not titel:
            continue
        eintraege.append(
            {
                "titel": titel,
                "link": link,
                "zusammenfassung": _text(item.find("description"))[:_MAX_ZUSAMMENFASSUNG],
                "veroeffentlicht_utc": _datum_normalisieren(item.findtext("pubDate") or ""),
            }
        )
    # Atom
    for entry in wurzel.iter(f"{_ATOM_NS}entry"):
        link = ""
        for link_el in entry.findall(f"{_ATOM_NS}link"):
            if link_el.get("rel") in (None, "alternate"):
                link = link_el.get("href") or ""
                break
        titel = _text(entry.find(f"{_ATOM_NS}title"))
        if not link or not titel:
            continue
        zusammenfassung = _text(entry.find(f"{_ATOM_NS}summary")) or _text(
            entry.find(f"{_ATOM_NS}content")
        )
        datum = entry.findtext(f"{_ATOM_NS}published") or entry.findtext(f"{_ATOM_NS}updated") or ""
        eintraege.append(
            {
                "titel": titel,
                "link": link,
                "zusammenfassung": zusammenfassung[:_MAX_ZUSAMMENFASSUNG],
                "veroeffentlicht_utc": _datum_normalisieren(datum),
            }
        )
    return eintraege


def team_zuordnen(text: str, teams: list[sqlite3.Row]) -> int | None:
    """Erstes im Text vorkommendes Team (Wortgrenze), sonst None."""
    for team in teams:
        if re.search(rf"\b{re.escape(team['name'])}\b", text, re.IGNORECASE):
            return team["id"]
    return None


def _link_hash(link: str) -> str:
    return hashlib.sha256(link.encode("utf-8")).hexdigest()


def feed_abrufen(conn: sqlite3.Connection, feed: sqlite3.Row, *, xml_text: str | None = None) -> int:
    """Holt einen Feed und legt neue Einträge an; liefert die Anzahl neuer Items."""
    if xml_text is None:
        # Defense in depth zur Pydantic-Validierung beim Anlegen (SSRF):
        # ausschließlich http(s) abrufen.
        from urllib.parse import urlparse

        schema = urlparse(feed["url"]).scheme
        if schema not in ("http", "https"):
            raise ValueError(f"Feed-URL mit unzulässigem Schema: {schema}")
        antwort = httpx.get(
            feed["url"], timeout=15.0, follow_redirects=True,
            headers={"User-Agent": "wm26-app/1.0 (+rss)"},
        )
        antwort.raise_for_status()
        xml_text = antwort.text
    eintraege = eintraege_parsen(xml_text)
    teams = conn.execute("SELECT id, name FROM team").fetchall()
    neu = 0
    with db.schreib_transaktion(conn):
        for eintrag in eintraege:
            eingefuegt = conn.execute(
                "INSERT OR IGNORE INTO news_item"
                " (feed_id, titel, link, link_hash, zusammenfassung, veroeffentlicht_utc, team_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    feed["id"],
                    eintrag["titel"],
                    eintrag["link"],
                    _link_hash(eintrag["link"]),
                    eintrag["zusammenfassung"] or None,
                    eintrag["veroeffentlicht_utc"],
                    team_zuordnen(
                        f"{eintrag['titel']} {eintrag['zusammenfassung']}", teams
                    ),
                ),
            ).rowcount
            neu += eingefuegt
        conn.execute(
            "UPDATE feed SET letzter_abruf_utc = ? WHERE id = ?", (jetzt_iso(), feed["id"])
        )
    return neu


def alle_feeds_abrufen(conn: sqlite3.Connection) -> AbrufBericht:
    bericht = AbrufBericht()
    feeds = conn.execute("SELECT * FROM feed WHERE aktiv = 1").fetchall()
    for feed in feeds:
        bericht.feeds += 1
        try:
            bericht.neu += feed_abrufen(conn, feed)
        except Exception:
            bericht.fehler += 1
            logger.exception("Feed-Abruf fehlgeschlagen: %s", feed["url"])
    return bericht


def abruf_faellig(conn: sqlite3.Connection, *, minuten: int = 30) -> bool:
    schwelle = iso_utc(jetzt_utc() - timedelta(minutes=minuten))
    zeile = conn.execute(
        "SELECT 1 FROM feed WHERE aktiv = 1 AND (letzter_abruf_utc IS NULL"
        " OR letzter_abruf_utc < ?) LIMIT 1",
        (schwelle,),
    ).fetchone()
    return zeile is not None
