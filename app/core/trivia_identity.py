"""Independent anonymous practice identities; no Fantasy development access."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.core.security import (
    authenticate_access_token,
    ensure_active_profile,
    keycloak_oauth2_scheme,
)
from app.db.database import get_db
from app.models.trivia import TriviaGuest


def create_trivia_guest(db: Session) -> str:
    key = secrets.token_urlsafe(32)
    db.add(
        TriviaGuest(
            token_hash=sha256(key.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(days=365),
        )
    )
    db.commit()
    return key


def trivia_owner(
    token: str | None = Depends(keycloak_oauth2_scheme),
    guest_key: str | None = Header(None, alias="X-Trivia-Guest-Key"),
    db: Session = Depends(get_db),
) -> str:
    if token:
        user = authenticate_access_token(token)
        ensure_active_profile(user, db)
        return f"user:{user.subject}"
    if not guest_key or len(guest_key) != 43 or not guest_key.isascii():
        raise HTTPException(401, "A trivia guest session is required.")
    digest = sha256(guest_key.encode()).hexdigest()
    guest = db.get(TriviaGuest, digest)
    if guest is None or guest.expires_at.replace(tzinfo=UTC) <= datetime.now(
        UTC
    ):
        raise HTTPException(401, "Your trivia guest session has expired.")
    return f"guest:{digest}"
