import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_key.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(s: str) -> str:
    return _fernet().encrypt(s.encode()).decode()


def decrypt_secret(s: str) -> str:
    return _fernet().decrypt(s.encode()).decode()
