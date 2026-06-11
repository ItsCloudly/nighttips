"""Zeit-Helfer: alle Zeitstempel werden als ISO-8601-UTC-Text gespeichert.

Format "YYYY-MM-DDTHH:MM:SSZ" — bei fester Länge sind die Texte auch
lexikographisch korrekt sortier- und vergleichbar (genutzt in SQL-Abfragen).
"""
from __future__ import annotations

from datetime import datetime, timezone


def jetzt_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(zeit: datetime) -> str:
    if zeit.tzinfo is None:
        raise ValueError("Naive Zeitangabe ohne Zeitzone wird nicht akzeptiert")
    return zeit.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jetzt_iso() -> str:
    return iso_utc(jetzt_utc())


def parse_utc(text: str) -> datetime:
    """Liest ISO-8601-Text (mit 'Z' oder Offset) und liefert eine UTC-Zeit."""
    zeit = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if zeit.tzinfo is None:
        zeit = zeit.replace(tzinfo=timezone.utc)
    return zeit.astimezone(timezone.utc)
