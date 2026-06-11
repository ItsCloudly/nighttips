"""Nutzer-Endpunkte für News (SPEC 5.6) und Bonusfragen (SPEC 5.4)."""
from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..abhaengigkeiten import aktueller_nutzer, get_db, ki_sichtbar
from ..services import bonus, turnier

router = APIRouter(prefix="/api", tags=["news", "bonus"])


@router.get("/news")
def news_liste(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    team_id: int | None = None,
    tag: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT n.id, n.titel, n.link, n.zusammenfassung, n.veroeffentlicht_utc,"
        " n.team_id, t.name AS team_name, f.titel AS feed_titel"
        " FROM news_item n JOIN feed f ON f.id = n.feed_id"
        " LEFT JOIN team t ON t.id = n.team_id"
    )
    parameter: list[Any] = []
    if team_id is not None:
        sql += " WHERE n.team_id = ?"
        parameter.append(team_id)
    sql += " ORDER BY n.veroeffentlicht_utc DESC, n.id DESC LIMIT ?"
    # Beim Tag-Filter mehr lesen, da erst nach der Kategorisierung gefiltert wird.
    parameter.append(max(1, min(limit, 200)) * (4 if tag else 1))
    items = []
    for zeile in conn.execute(sql, parameter).fetchall():
        item = dict(zeile)
        item["tags"] = turnier.news_tags(f"{item['titel']} {item['zusammenfassung'] or ''}")
        if tag and tag not in item["tags"]:
            continue
        items.append(item)
        if len(items) >= max(1, min(limit, 200)):
            break
    return items


@router.get("/news/tags")
def news_tags_liste(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
) -> list[str]:
    return [name for name, _ in turnier.NEWS_TAGS]


class BonusTippAbgabe(BaseModel):
    bonusfrage_id: int
    antwort_ref: int


@router.get("/bonusfragen")
def bonusfragen(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    return bonus.fragen_fuer_nutzer(conn, nutzer["id"], mit_ki=ki_sichtbar(nutzer))


@router.post("/bonustipps")
def bonustipp_abgeben(
    daten: BonusTippAbgabe,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return bonus.tipp_abgeben(
            conn,
            nutzer_id=nutzer["id"],
            bonusfrage_id=daten.bonusfrage_id,
            antwort_ref=daten.antwort_ref,
        )
    except bonus.BonusFehler as fehler:
        raise HTTPException(status_code=409, detail=str(fehler)) from None
