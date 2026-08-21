"""Authentification (JWT) et chiffrement des secrets stockes."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import User

_PBKDF2_ROUNDS = 180_000
_ALGO = "HS256"


# --------------------------------------------------------------------------- mots de passe
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# --------------------------------------------------------------------------- jetons
def create_token(user: User) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "name": user.username,
        "adm": user.is_admin,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.session_ttl_hours)).timestamp()),
    }
    return jwt.encode(payload, settings.resolve_secret_key(), algorithm=_ALGO)


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, get_settings().resolve_secret_key(), algorithms=[_ALGO])
    except jwt.PyJWTError:
        return None


def _token_from_request(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    token = request.cookies.get("allbudy_token")
    if token:
        return token
    # Les WebSocket navigateurs ne peuvent pas porter d'en-tete: on tolere ?token=
    return request.query_params.get("token")


async def current_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User | None:
    """Utilisateur courant, ou None si l'authentification est desactivee."""
    settings = get_settings()
    if not settings.auth_enabled:
        return None
    token = _token_from_request(request)
    payload = decode_token(token) if token else None
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await session.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Compte inconnu")
    return user


async def websocket_user_ok(token: str | None, session: AsyncSession) -> bool:
    settings = get_settings()
    if not settings.auth_enabled:
        return True
    payload = decode_token(token) if token else None
    if not payload:
        return False
    return await session.get(User, int(payload["sub"])) is not None


# --------------------------------------------------------------------------- secrets stockes
def _fernet() -> Fernet:
    key = hashlib.sha256(get_settings().resolve_secret_key().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return None


async def ensure_admin_user() -> None:
    """Cree le compte admin initial si la table est vide."""
    from .db import session_scope

    settings = get_settings()
    async with session_scope() as session:
        exists = (await session.execute(select(User.id).limit(1))).first()
        if exists:
            return
        session.add(
            User(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                is_admin=True,
            )
        )
