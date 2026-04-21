from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.schemas.users import UpdateUserRequest, UserResponse
from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
def get_me(
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> UserResponse:
    """Return the authenticated user's profile and preferences."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, email, base_currency, display_tz, display_date_fmt, created_at
            FROM users
            WHERE id = %s;
            """,
            [str(current_user.id)],
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    return UserResponse(
        id=row[0],
        email=row[1],
        base_currency=row[2],
        display_tz=row[3],
        display_date_fmt=row[4],
        created_at=row[5],
    )


@router.patch("/me", response_model=UserResponse)
def update_me(
    body: UpdateUserRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> UserResponse:
    """Update the authenticated user's preferences. Omitted fields are unchanged."""
    updates: dict[str, object] = {}
    if body.base_currency is not None:
        updates["base_currency"] = body.base_currency
    if body.display_tz is not None:
        updates["display_tz"] = body.display_tz
    if body.display_date_fmt is not None:
        updates["display_date_fmt"] = body.display_date_fmt

    if updates:
        set_clause = ", ".join(f"{col} = %s" for col in updates)
        values = list(updates.values()) + [str(current_user.id)]
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE users SET {set_clause} WHERE id = %s;",
                values,
            )

    # Re-fetch to return the current state.
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, email, base_currency, display_tz, display_date_fmt, created_at
            FROM users
            WHERE id = %s;
            """,
            [str(current_user.id)],
        )
        row = cur.fetchone()

    return UserResponse(
        id=row[0],
        email=row[1],
        base_currency=row[2],
        display_tz=row[3],
        display_date_fmt=row[4],
        created_at=row[5],
    )
