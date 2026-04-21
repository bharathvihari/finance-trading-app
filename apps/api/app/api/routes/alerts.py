"""
User alert CRUD.

Alerts define price/condition triggers that are evaluated in real-time by
the AlertEvaluator when a price tick arrives on the Redis subscriber loop.

Routes:
  POST   /api/v1/alerts                create alert
  GET    /api/v1/alerts                list alerts (optional ?status= filter)
  PATCH  /api/v1/alerts/{id}           update condition, message, or dismiss
  DELETE /api/v1/alerts/{id}           delete alert
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg2.extras import Json

from app.api.schemas.alerts import AlertResponse, CreateAlertRequest, UpdateAlertRequest
from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _row_to_alert(row: tuple) -> AlertResponse:
    return AlertResponse(
        id=row[0], symbol=row[1], exchange=row[2],
        alert_type=row[3], message=row[4],
        condition_json=row[5] or {}, status=row[6],
        triggered_at=row[7], resolved_at=row[8], created_at=row[9],
    )


@router.post("", response_model=AlertResponse, status_code=status.HTTP_201_CREATED)
def create_alert(
    body: CreateAlertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> AlertResponse:
    """Create a price/condition alert. Evaluated in real-time on each price tick."""
    alert_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO alerts
                (id, user_id, symbol, exchange, alert_type, message, condition_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, symbol, exchange, alert_type, message,
                      condition_json, status, triggered_at, resolved_at, created_at;
            """,
            [
                alert_id, str(current_user.id),
                body.symbol.upper(), body.exchange.upper(),
                body.alert_type, body.message, Json(body.condition_json),
            ],
        )
        return _row_to_alert(cur.fetchone())


@router.get("", response_model=list[AlertResponse])
def list_alerts(
    alert_status: Annotated[
        str | None, Query(alias="status", description="Filter: active | triggered | dismissed")
    ] = None,
    symbol: Annotated[str | None, Query()] = None,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[AlertResponse]:
    """List all alerts for the authenticated user."""
    where = ["user_id = %s"]
    params: list[object] = [str(current_user.id)]
    if alert_status:
        where.append("status = %s")
        params.append(alert_status)
    if symbol:
        where.append("symbol = %s")
        params.append(symbol.upper())

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, symbol, exchange, alert_type, message,
                   condition_json, status, triggered_at, resolved_at, created_at
            FROM alerts
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC;
            """,
            params,
        )
        return [_row_to_alert(r) for r in cur.fetchall()]


@router.patch("/{alert_id}", response_model=AlertResponse)
def update_alert(
    alert_id: str,
    body: UpdateAlertRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> AlertResponse:
    """Update alert message, condition, or dismiss it."""
    updates: dict[str, object] = {}
    if body.message is not None:
        updates["message"] = body.message
    if body.condition_json is not None:
        updates["condition_json"] = Json(body.condition_json)
    if body.status is not None:
        updates["status"] = body.status
        if body.status == "dismissed":
            updates["resolved_at"] = "NOW()"  # handled below as a literal

    if updates:
        # Handle NOW() literal separately
        parts = []
        vals: list[object] = []
        for col, val in updates.items():
            if val == "NOW()":
                parts.append(f"{col} = NOW()")
            else:
                parts.append(f"{col} = %s")
                vals.append(val)
        set_clause = ", ".join(parts)
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE alerts SET {set_clause} WHERE id = %s AND user_id = %s;",
                [*vals, alert_id, str(current_user.id)],
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, exchange, alert_type, message,
                   condition_json, status, triggered_at, resolved_at, created_at
            FROM alerts WHERE id = %s AND user_id = %s;
            """,
            [alert_id, str(current_user.id)],
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found.")
    return _row_to_alert(row)


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert(
    alert_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM alerts WHERE id = %s AND user_id = %s;",
            [alert_id, str(current_user.id)],
        )
