"""Symmetric encryption for exchange API secrets.

Uses ``cryptography.fernet`` with a key read from the ``ENCRYPTION_KEY``
environment variable.

Generate a key once with::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Then set ``ENCRYPTION_KEY`` in your environment or ``.env`` file.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

_KEY = os.getenv("ENCRYPTION_KEY", "")


def _get_fernet() -> Fernet:
    if not _KEY:
        raise RuntimeError(
            "ENCRYPTION_KEY environment variable is not set. "
            "Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(_KEY.encode("utf-8"))


def encrypt_secret(secret: str) -> str:
    """Return a Fernet-encrypted, URL-safe string."""
    f = _get_fernet()
    return f.encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(encrypted: str) -> str:
    """Decrypt a value previously encrypted by :func:`encrypt_secret`.

    Raises ``cryptography.fernet.InvalidToken`` on bad input.
    """
    f = _get_fernet()
    return f.decrypt(encrypted.encode("utf-8")).decode("utf-8")
