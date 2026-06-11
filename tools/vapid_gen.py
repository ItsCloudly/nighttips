"""VAPID-Schlüsselpaar für Web Push erzeugen (einmalig, SPEC 5.5):
PYTHONPATH=. python tools/vapid_gen.py

Gibt die beiden Zeilen für die .env aus. Der private Schlüssel bleibt
ausschließlich in der .env (SPEC 8.4), niemals im Git.
"""
from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode("ascii")


def main() -> int:
    schluessel = ec.generate_private_key(ec.SECP256R1())
    privat = _b64url(
        schluessel.private_numbers().private_value.to_bytes(32, "big")
    )
    oeffentlich = _b64url(
        schluessel.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
    )
    print(f"WM26_VAPID_PRIVATE_KEY={privat}")
    print(f"WM26_VAPID_PUBLIC_KEY={oeffentlich}")
    print("WM26_VAPID_SUBJECT=mailto:du@example.org")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
