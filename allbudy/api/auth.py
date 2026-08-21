"""Connexion et gestion du compte."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..models import User
from ..schemas import LoginRequest, MessageResponse, PasswordChange, TokenResponse, UserOut
from ..security import create_token, current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "allbudy_token"


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, response: Response, session: AsyncSession = Depends(get_session)
) -> TokenResponse:
    user = (
        await session.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides"
        )
    user.last_login = datetime.now(UTC)
    await session.commit()

    token = create_token(user)
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        path=settings.base_path or "/",
    )
    return TokenResponse(access_token=token, username=user.username)


@router.post("/logout", response_model=MessageResponse)
async def logout(response: Response) -> MessageResponse:
    response.delete_cookie(COOKIE_NAME, path=get_settings().base_path or "/")
    return MessageResponse(message="Deconnecte")


@router.get("/me", response_model=UserOut)
async def me(user: User | None = Depends(current_user)) -> UserOut:
    if user is None:
        # Authentification desactivee: on renvoie un compte local implicite.
        return UserOut(id=0, username="local", is_admin=True)
    return UserOut.model_validate(user)


@router.post("/password", response_model=MessageResponse)
async def change_password(
    payload: PasswordChange,
    user: User | None = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> MessageResponse:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authentification desactivee sur cette instance",
        )
    record = await session.get(User, user.id)
    if record is None or not verify_password(payload.current_password, record.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Mot de passe actuel incorrect"
        )
    record.password_hash = hash_password(payload.new_password)
    await session.commit()
    return MessageResponse(message="Mot de passe mis a jour")
