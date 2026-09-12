"""Token encryption at rest using Fernet, keyed from the application secret."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.application.errors import AuthenticationError


class FernetTokenCipher:
    def __init__(self, secret_key: str) -> None:
        digest = hashlib.sha256(f"token-cipher:{secret_key}".encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            # Happens when SECRET_KEY was rotated: the session is unusable, force re-login.
            raise AuthenticationError("stored token cannot be decrypted") from exc
