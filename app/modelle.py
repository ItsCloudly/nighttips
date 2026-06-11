"""Pydantic-Modelle für Request- und Response-Bodies."""
from __future__ import annotations

from pydantic import BaseModel, Field


# PINs werden für NEU gesetzte Konten auf mind. 6 Zeichen gehoben (eine reine
# 4-stellige Zahl hat nur 10 000 Möglichkeiten — für eine öffentlich erreichbare
# App zu schwach). Der Login akzeptiert weiterhin ab 4 Zeichen, damit Bestands-PINs
# gültig bleiben, bis sie geändert werden.
PIN_MIN_NEU = 6


class LoginDaten(BaseModel):
    anzeigename: str = Field(min_length=1, max_length=50)
    pin: str = Field(min_length=4, max_length=32)


class RegistrierungsDaten(BaseModel):
    anzeigename: str = Field(min_length=1, max_length=50)
    pin: str = Field(min_length=PIN_MIN_NEU, max_length=32)
    gruppen_passwort: str = Field(min_length=1, max_length=64)


class NutzerInfo(BaseModel):
    id: int
    anzeigename: str
    rolle: str
    ki_freigeschaltet: bool = False
    # Persönliche Vorlaufzeit der Tipp-Erinnerung (None = Server-Standard, 0 = aus)
    tipp_erinnerung_minuten: int | None = Field(default=None, ge=0, le=720)
    # Dateiname des Profilbilds (None = Initialen-Avatar)
    profilbild: str | None = None


class ErinnerungsEinstellung(BaseModel):
    tipp_erinnerung_minuten: int = Field(ge=0, le=720)


class NameAenderung(BaseModel):
    anzeigename: str = Field(min_length=1, max_length=50)


class PinWechsel(BaseModel):
    """Selbst-Service: aktuelle PIN bestätigen, neue setzen (mind. 6 Zeichen)."""

    alte_pin: str = Field(min_length=4, max_length=32)
    neue_pin: str = Field(min_length=PIN_MIN_NEU, max_length=32)


class NeuerNutzer(BaseModel):
    anzeigename: str = Field(min_length=1, max_length=50)
    pin: str = Field(min_length=PIN_MIN_NEU, max_length=32)
    rolle: str = Field(default="mitglied", pattern="^(admin|mitglied|ki)$")


class TippAbgabe(BaseModel):
    spiel_id: int
    tipp_heim: int = Field(ge=0, le=99)
    tipp_gast: int = Field(ge=0, le=99)


class NotizEingabe(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class FeedbackEingabe(BaseModel):
    kategorie: str = Field(pattern="^(fehler|idee|sonstiges)$")
    nachricht: str = Field(min_length=3, max_length=2000)


class EreignisEingabe(BaseModel):
    """Manueller Ticker-Eintrag des Admins (z. B. Minute/Torschütze nachtragen)."""

    typ: str = Field(
        pattern="^(tor|eigentor|elfmeter|gelb|gelbrot|rot|wechsel|var|anpfiff|halbzeit|abpfiff|freitext)$"
    )
    minute: int | None = Field(default=None, ge=0, le=150)
    team_id: int | None = None
    spieler_id: int | None = None
    spieler2_id: int | None = None
    text: str | None = Field(default=None, max_length=500)


class ErgebnisEingabe(BaseModel):
    tore_heim: int = Field(ge=0, le=99)
    tore_gast: int = Field(ge=0, le=99)
    status: str = Field(default="beendet", pattern="^(geplant|live|halbzeit|beendet|abgesagt)$")
    ergebnis_nach: str = Field(default="90", pattern="^(90|120|elfmeterschiessen)$")
    elfmeter_sieger_team_id: int | None = None
