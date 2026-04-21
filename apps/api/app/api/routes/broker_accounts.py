from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.schemas.portfolios import (
    BrokerAccountResponse,
    CreateBrokerAccountRequest,
    UpdateBrokerAccountRequest,
)
from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db

router = APIRouter(prefix="/broker-accounts", tags=["broker-accounts"])


def _row_to_broker(row: tuple) -> BrokerAccountResponse:
    return BrokerAccountResponse(
        id=row[0], broker=row[1], display_name=row[2],
        account_ref=row[3], currency=row[4], is_active=row[5],
        created_at=row[6], updated_at=row[7],
    )


@router.post("", response_model=BrokerAccountResponse, status_code=status.HTTP_201_CREATED)
def create_broker_account(
    body: CreateBrokerAccountRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> BrokerAccountResponse:
    """Register a broker connection for the authenticated user."""
    acc_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO broker_accounts
                (id, user_id, broker, display_name, account_ref, currency)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, broker, display_name, account_ref,
                      currency, is_active, created_at, updated_at;
            """,
            [acc_id, str(current_user.id), body.broker,
             body.display_name, body.account_ref, body.currency],
        )
        return _row_to_broker(cur.fetchone())


@router.get("", response_model=list[BrokerAccountResponse])
def list_broker_accounts(
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[BrokerAccountResponse]:
    """List all broker accounts for the authenticated user."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, broker, display_name, account_ref,
                   currency, is_active, created_at, updated_at
            FROM broker_accounts
            WHERE user_id = %s
            ORDER BY created_at ASC;
            """,
            [str(current_user.id)],
        )
        return [_row_to_broker(r) for r in cur.fetchall()]


@router.patch("/{account_id}", response_model=BrokerAccountResponse)
def update_broker_account(
    account_id: str,
    body: UpdateBrokerAccountRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> BrokerAccountResponse:
    """Update a broker account's display name, reference, currency, or active status."""
    updates: dict[str, object] = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.account_ref is not None:
        updates["account_ref"] = body.account_ref
    if body.currency is not None:
        updates["currency"] = body.currency
    if body.is_active is not None:
        updates["is_active"] = body.is_active

    if updates:
        set_clause = ", ".join(f"{col} = %s" for col in updates)
        set_clause += ", updated_at = NOW()"
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE broker_accounts SET {set_clause} WHERE id = %s AND user_id = %s;",
                [*updates.values(), account_id, str(current_user.id)],
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, broker, display_name, account_ref,
                   currency, is_active, created_at, updated_at
            FROM broker_accounts WHERE id = %s AND user_id = %s;
            """,
            [account_id, str(current_user.id)],
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Broker account not found.")
    return _row_to_broker(row)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_broker_account(
    account_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> None:
    """Delete a broker account. Associated portfolios will have their broker link nulled."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM broker_accounts WHERE id = %s AND user_id = %s;",
            [account_id, str(current_user.id)],
        )
