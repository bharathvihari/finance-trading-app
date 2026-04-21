from __future__ import annotations

import uuid

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.schemas.auth import LoginRequest, RegisterRequest, TokenResponse
from app.auth.jwt import create_access_token
from app.auth.password import hash_password, verify_password
from app.db.connection import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, conn=Depends(get_db)) -> TokenResponse:
    """Create a new user account and return a JWT access token."""
    user_id = str(uuid.uuid4())
    hashed = hash_password(body.password)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, email, password_hash)
                VALUES (%s, %s, %s);
                """,
                [user_id, body.email.lower(), hashed],
            )
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    return TokenResponse(access_token=create_access_token(user_id, body.email.lower()))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, conn=Depends(get_db)) -> TokenResponse:
    """Authenticate with email and password, return a JWT access token."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, email, password_hash FROM users WHERE email = %s;",
            [body.email.lower()],
        )
        row = cur.fetchone()

    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
    )

    if not row:
        raise invalid

    user_id, email, password_hash = row

    if not password_hash or not verify_password(body.password, password_hash):
        raise invalid

    return TokenResponse(access_token=create_access_token(str(user_id), email))
