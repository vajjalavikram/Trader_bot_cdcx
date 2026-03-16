"""Encrypted exchange API-key management endpoints.

Identity is derived from the ``X-API-Key`` header.  Users can only
access their own keys, and the encrypted secret is never returned.
"""

import time
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.security.identity import get_caller_id
from backend.security.crypto import encrypt_secret
from db.database import get_connection

router = APIRouter(prefix="/keys", tags=["api-keys"])


# ── Models ────────────────────────────────────────────────────────────────

class AddKeyRequest(BaseModel):
    exchange: str
    api_key: str
    api_secret: str


class KeyInfo(BaseModel):
    key_id: str
    exchange: str
    api_key: str
    created_at: int


class DeleteKeyResponse(BaseModel):
    success: bool


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/add", response_model=KeyInfo, status_code=201)
def add_key(
    req: AddKeyRequest,
    user_id: str = Depends(get_caller_id),
):
    """Store a new exchange API key (secret is encrypted at rest)."""
    key_id = uuid.uuid4().hex
    encrypted = encrypt_secret(req.api_secret)
    now = int(time.time())

    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO api_keys "
            "(key_id, user_id, exchange, api_key, encrypted_secret, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key_id, user_id, req.exchange, req.api_key, encrypted, now),
        )

    return KeyInfo(
        key_id=key_id,
        exchange=req.exchange,
        api_key=req.api_key,
        created_at=now,
    )


@router.get("", response_model=List[KeyInfo])
def list_keys(user_id: str = Depends(get_caller_id)):
    """Return all API keys owned by the caller (secret excluded)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT key_id, exchange, api_key, created_at "
        "FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    return [KeyInfo(**dict(r)) for r in rows]


@router.delete("/{key_id}", response_model=DeleteKeyResponse)
def delete_key(
    key_id: str,
    user_id: str = Depends(get_caller_id),
):
    """Delete an API key (only if owned by the caller)."""
    conn = get_connection()

    row = conn.execute(
        "SELECT user_id FROM api_keys WHERE key_id = ?", (key_id,),
    ).fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    if dict(row)["user_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your key")

    with conn:
        conn.execute("DELETE FROM api_keys WHERE key_id = ?", (key_id,))

    return DeleteKeyResponse(success=True)
