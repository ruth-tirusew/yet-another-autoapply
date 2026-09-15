"""Symmetric encryption for per-user API keys and other secrets."""

from __future__ import annotations

import base64
import hashlib
import os

_fernet = None


def _encryption_key() -> bytes:
    raw = os.getenv("CREDENTIALS_ENCRYPTION_KEY", "").strip()
    if raw:
        try:
            return base64.urlsafe_b64decode(raw.encode("utf-8"))
        except Exception:
            pass
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)
    session = os.getenv("SESSION_SECRET", "").strip()
    if not session:
        from src.settings import SESSION_SECRET

        session = SESSION_SECRET
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"job_crawler_credentials_v1",
        info=b"credentials",
    )
    return base64.urlsafe_b64encode(hkdf.derive(session.encode("utf-8")))


def _get_fernet():
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet

        _fernet = Fernet(_encryption_key())
    return _fernet


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("Cannot encrypt empty secret")
    token = _get_fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        raise ValueError("Cannot decrypt empty ciphertext")
    return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


def mask_secret(plaintext: str, visible: int = 4) -> str:
    if not plaintext:
        return ""
    tail = plaintext[-visible:] if len(plaintext) >= visible else plaintext
    return f"••••••{tail}"
