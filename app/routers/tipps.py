"""Tippabgabe und Rangliste (SPEC 5.4)."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ..abhaengigkeiten import aktueller_nutzer, get_db, ki_sichtbar
from ..modelle import TippAbgabe
from ..services import tippspiel

router = APIRouter(prefix="/api", tags=["tippspiel"])


@router.post("/tipps")
def tipp_abgeben(
    daten: TippAbgabe,
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> dict[str, Any]:
    try:
        return tippspiel.tipp_abgeben(
            conn,
            nutzer_id=nutzer["id"],
            spiel_id=daten.spiel_id,
            tipp_heim=daten.tipp_heim,
            tipp_gast=daten.tipp_gast,
        )
    except tippspiel.SpielNichtGefunden:
        raise HTTPException(status_code=404, detail="Spiel nicht gefunden") from None
    except tippspiel.TippGesperrt as fehler:
        raise HTTPException(status_code=409, detail=str(fehler)) from None


@router.get("/tipps/meine")
def meine_tipps(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
) -> list[dict[str, Any]]:
    zeilen = conn.execute(
        "SELECT spiel_id, tipp_heim, tipp_gast, abgegeben_utc, punkte"
        " FROM tipp WHERE nutzer_id = ? ORDER BY spiel_id",
        (nutzer["id"],),
    ).fetchall()
    return [dict(zeile) for zeile in zeilen]


@router.get("/rangliste")
def rangliste(
    nutzer: Annotated[sqlite3.Row, Depends(aktueller_nutzer)],
    conn: Annotated[sqlite3.Connection, Depends(get_db)],
    datum: str | None = None,
    runde: str | None = None,
) -> list[dict[str, Any]]:
    if datum is not None:
        try:
            datetime.strptime(datum, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422, detail="datum muss das Format JJJJ-MM-TT haben"
            ) from None
    return tippspiel.rangliste(conn, datum=datum, runde=runde, mit_ki=ki_sichtbar(nutzer))
