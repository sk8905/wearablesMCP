"""Encrypted on-disk storage for the Whoop OAuth token.

The token bundle (access token, refresh token, expiry) is serialized to JSON,
encrypted with Fernet symmetric encryption, and written to a single file. The
Fernet key itself is never stored on disk next to the data or in .env — it
lives in the OS keyring (via the `keyring` package). Losing the keyring entry
means the encrypted file can no longer be read; just re-run authorization.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import keyring
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)

# Identifiers used to look the encryption key up in the OS keyring.
_KEYRING_SERVICE = "wearables-mcp"
_KEYRING_KEY_NAME = "whoop-token-fernet-key"


class TokenStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _fernet(self, *, create: bool = False) -> Optional[Fernet]:
        """Return a Fernet instance backed by the keyring-held key.

        If no key exists and create=False, return None (nothing to decrypt).
        If create=True, generate and persist a new key.
        """
        key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME)
        if key is None:
            if not create:
                return None
            key = Fernet.generate_key().decode("ascii")
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY_NAME, key)
            log.info("Generated new Fernet key and stored it in the OS keyring.")
        return Fernet(key.encode("ascii"))

    def load(self) -> Optional[dict]:
        """Load and decrypt the stored token, or None if not present/unreadable."""
        if not self.path.exists():
            return None
        fernet = self._fernet(create=False)
        if fernet is None:
            log.warning(
                "Encrypted token file exists but no key is in the keyring; "
                "re-run authorization."
            )
            return None
        try:
            plaintext = fernet.decrypt(self.path.read_bytes())
        except InvalidToken:
            log.error("Failed to decrypt Whoop token (key mismatch or corruption).")
            return None
        return json.loads(plaintext.decode("utf-8"))

    def save(self, token: dict) -> None:
        """Encrypt and atomically write the token bundle to disk (0600)."""
        fernet = self._fernet(create=True)
        assert fernet is not None  # create=True always returns a Fernet
        blob = fernet.encrypt(json.dumps(token).encode("utf-8"))

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(blob)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.path)
