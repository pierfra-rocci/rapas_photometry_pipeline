"""Security helpers for authenticating API requests."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash

from .database import get_db
from .models import User

security = HTTPBasic()


def find_user_by_identifier(db: Session, identifier: str) -> Optional[User]:
    """Return the user matching a username or an email address.

    The username is matched verbatim. The email is matched case-insensitively
    and ignoring surrounding whitespace, because registration keeps the local
    part of the address as it was typed while users rarely retype it with the
    exact same casing.
    """
    return (
        db.query(User)
        .filter(
            or_(
                User.username == identifier,
                func.lower(User.email) == identifier.strip().lower(),
            )
        )
        .first()
    )


def get_current_user(
    credentials: HTTPBasicCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Authenticate a user via HTTP Basic credentials.

    Both the account username and the email used at registration are accepted
    as the Basic user name, so a session started with either identifier keeps
    its authenticated access.
    """
    user = find_user_by_identifier(db, credentials.username)
    if not user or not check_password_hash(
        user.password,
        credentials.password,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return user
