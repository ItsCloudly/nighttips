"""PIN-Hashing (scrypt aus der Stdlib) und Session-Token (SPEC 8.1)."""
from __future__ import annotations

import hashlib
import hmac
import secrets

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_HASH_LAENGE = 32
_SALZ_LAENGE = 16


def pin_hashen(pin: str) -> str:
    salz = secrets.token_bytes(_SALZ_LAENGE)
    digest = hashlib.scrypt(
        pin.encode("utf-8"), salt=salz, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_HASH_LAENGE
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salz.hex()}${digest.hex()}"


def pin_pruefen(pin: str, gespeicherter_hash: str) -> bool:
    try:
        verfahren, n, r, p, salz_hex, digest_hex = gespeicherter_hash.split("$")
        if verfahren != "scrypt":
            return False
        erwartet = bytes.fromhex(digest_hex)
        berechnet = hashlib.scrypt(
            pin.encode("utf-8"),
            salt=bytes.fromhex(salz_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(erwartet),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(berechnet, erwartet)


def session_token_erzeugen() -> str:
    return secrets.token_urlsafe(32)


def token_hashen(token: str) -> str:
    """Sessions werden nur als SHA-256-Hash gespeichert (DB-Leck verrät keine Tokens)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
