from __future__ import annotations

import dataclasses

import pytest

from app.services.tippspiel import (
    PUNKTE_DIFFERENZ,
    PUNKTE_EXAKT,
    PUNKTE_TENDENZ,
    berechne_punkte,
    punkte_fuer_tipp,
    wertung_ermitteln,
)


@pytest.mark.parametrize(
    ("tipp", "ergebnis", "punkte"),
    [
        ((2, 1), (2, 1), PUNKTE_EXAKT),
        ((1, 1), (1, 1), PUNKTE_EXAKT),
        ((0, 0), (0, 0), PUNKTE_EXAKT),
        ((3, 2), (2, 1), PUNKTE_DIFFERENZ),  # gleiche Differenz, gleicher Sieger
        ((2, 2), (1, 1), PUNKTE_DIFFERENZ),  # Remis, aber nicht exakt
        ((1, 0), (4, 1), PUNKTE_TENDENZ),  # richtige Tendenz, andere Differenz
        ((0, 1), (1, 3), PUNKTE_TENDENZ),
        ((2, 1), (1, 1), 0),  # Sieg getippt, Remis gespielt
        ((1, 0), (0, 2), 0),  # falsche Tendenz
        ((1, 1), (2, 0), 0),  # Remis getippt, Sieg gespielt
    ],
)
def test_berechne_punkte(tipp, ergebnis, punkte):
    assert berechne_punkte(*tipp, *ergebnis) == punkte


def _spiel(**werte) -> dict:
    basis = {
        "runde": "Gruppe A",
        "status": "beendet",
        "tore_heim": 2,
        "tore_gast": 1,
        "ergebnis_nach": "90",
    }
    basis.update(werte)
    return basis


def test_wertung_offenes_spiel(einstellungen):
    assert wertung_ermitteln(_spiel(status="geplant", tore_heim=None, tore_gast=None), einstellungen) is None
    assert wertung_ermitteln(_spiel(status="live"), einstellungen) is None


def test_wertung_gruppenspiel(einstellungen):
    wertung = wertung_ermitteln(_spiel(), einstellungen)
    assert (wertung.tore_heim, wertung.tore_gast) == (2, 1)
    assert not wertung.nur_remis_bekannt


def test_ko_120_regel_verlaengerung_zaehlt(einstellungen):
    # Standard: Ergebnis nach Verlängerung zählt normal
    spiel = _spiel(runde="Achtelfinale", ergebnis_nach="120", tore_heim=2, tore_gast=1)
    assert punkte_fuer_tipp(spiel, 2, 1, einstellungen) == PUNKTE_EXAKT
    assert punkte_fuer_tipp(spiel, 1, 0, einstellungen) == PUNKTE_DIFFERENZ


def test_ko_elfmeterschiessen_zaehlt_als_remis(einstellungen):
    # Nach 120 Minuten unentschieden — Elfmeterschießen ändert die Wertung nicht
    spiel = _spiel(runde="Finale", ergebnis_nach="elfmeterschiessen", tore_heim=1, tore_gast=1)
    assert punkte_fuer_tipp(spiel, 1, 1, einstellungen) == PUNKTE_EXAKT
    assert punkte_fuer_tipp(spiel, 2, 2, einstellungen) == PUNKTE_DIFFERENZ
    assert punkte_fuer_tipp(spiel, 2, 1, einstellungen) == 0


def test_ko_elfmeterschiessen_defensiv_bei_ungleichen_toren(einstellungen):
    # Liefert die API nach Elfmeterschießen ungleiche Tore, zählt nur die Remis-Tendenz
    spiel = _spiel(runde="Finale", ergebnis_nach="elfmeterschiessen", tore_heim=4, tore_gast=2)
    assert punkte_fuer_tipp(spiel, 1, 1, einstellungen) == PUNKTE_DIFFERENZ
    assert punkte_fuer_tipp(spiel, 4, 2, einstellungen) == 0


def test_ko_90_regel(einstellungen):
    einstellungen_90 = dataclasses.replace(einstellungen, ko_wertung_nach_120=False)
    # Spiel ging in die Verlängerung: nach 90 Minuten stand es unentschieden
    spiel = _spiel(runde="Halbfinale", ergebnis_nach="120", tore_heim=2, tore_gast=1)
    assert punkte_fuer_tipp(spiel, 1, 1, einstellungen_90) == PUNKTE_DIFFERENZ
    assert punkte_fuer_tipp(spiel, 2, 1, einstellungen_90) == 0
    # Ohne Verlängerung zählt das Ergebnis normal
    spiel_regulaer = _spiel(runde="Halbfinale", ergebnis_nach="90", tore_heim=2, tore_gast=1)
    assert punkte_fuer_tipp(spiel_regulaer, 2, 1, einstellungen_90) == PUNKTE_EXAKT
