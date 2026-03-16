"""API-key-based user identity.

The CoinDCX API key is the user's identity.  Its SHA-256 hash serves as
a deterministic, non-reversible ``user_id`` used for database ownership
and strategy isolation.
"""

import hashlib

from fastapi import Header, HTTPException, status


def get_user_id_from_api_key(api_key: str) -> str:
    """Return the SHA-256 hex digest of *api_key*."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def get_caller_id(
    x_api_key: str = Header(..., alias="X-API-Key"),
) -> str:
    """FastAPI dependency that extracts the caller's ``user_id``.

    Reads the ``X-API-Key`` header, hashes it, and returns the
    resulting ``user_id``.  Returns HTTP 401 if the header is missing.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header is required",
        )
    return get_user_id_from_api_key(x_api_key)
