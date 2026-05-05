from __future__ import annotations

from datetime import datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from db.models.models import User
from db.session import get_db

log = structlog.get_logger()
router: APIRouter = APIRouter()
settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_active: bool
    is_verified: bool


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _hash_password(password: str) -> str:
    return pwd_context.hash(password)


def _verify_password(password: str, hashed_password: str) -> bool:
    return pwd_context.verify(password, hashed_password)


def _create_access_token(user_id: int) -> str:
    expiration: datetime = datetime.utcnow() + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload: dict[str, str | datetime] = {
        "sub": str(user_id),
        "exp": expiration,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    unauthorized_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        subject: str | None = payload.get("sub")
        if subject is None:
            raise unauthorized_error
        user_id: int = int(subject)
    except (JWTError, ValueError):
        log.warning("invalid auth token")
        raise unauthorized_error

    statement: Select[tuple[User]] = select(User).where(User.id == user_id)

    try:
        user: User | None = await session.scalar(statement)
    except Exception:
        log.exception("failed to load current user", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load user",
        )

    if user is None or not user.is_active:
        raise unauthorized_error

    return user


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> UserResponse:
    email: str = _normalize_email(payload.email)

    try:
        existing_user: User | None = await session.scalar(
            select(User).where(User.email == email)
        )
    except Exception:
        log.exception("failed to query user during registration", email=email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user",
        )

    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=email,
        hashed_password=_hash_password(payload.password),
        is_active=True,
        is_verified=False,
    )
    session.add(user)

    try:
        await session.flush()
        await session.refresh(user)
    except Exception:
        log.exception("failed to persist user", email=email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create user",
        )

    log.info("user registered", user_id=user.id, email=user.email)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    email: str = _normalize_email(payload.email)

    try:
        user: User | None = await session.scalar(select(User).where(User.email == email))
    except Exception:
        log.exception("failed to query user during login", email=email)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed",
        )

    if user is None or not _verify_password(payload.password, user.hashed_password):
        log.warning("invalid login attempt", email=email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    access_token: str = _create_access_token(user.id)
    log.info("user logged in", user_id=user.id, email=user.email)
    return TokenResponse(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)
