"""
Strategy configuration CRUD.

These routes manage NautilusTrader strategy definitions stored in
`strategy_configs`. They are separate from the live start/stop controls
in strategy_control.py, which talk directly to the trading engine.

Routes:
  POST   /api/v1/strategies           create strategy config
  GET    /api/v1/strategies           list user's strategies
  GET    /api/v1/strategies/{id}      get strategy config
  PATCH  /api/v1/strategies/{id}      update config / toggle active
  DELETE /api/v1/strategies/{id}      delete strategy config
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2.extras import Json

from app.api.schemas.strategies import (
    CreateStrategyRequest,
    StrategyResponse,
    UpdateStrategyRequest,
)
from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _row_to_strategy(row: tuple) -> StrategyResponse:
    return StrategyResponse(
        id=row[0], strategy_name=row[1], mode=row[2],
        config_json=row[3] or {}, description=row[4],
        is_active=row[5], created_at=row[6], updated_at=row[7],
    )


def _get_strategy_or_404(conn, strategy_id: str, user_id: str) -> tuple:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, strategy_name, mode, config_json, description,
                   is_active, created_at, updated_at
            FROM strategy_configs
            WHERE id = %s AND user_id = %s;
            """,
            [strategy_id, user_id],
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Strategy not found.")
    return row


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
def create_strategy(
    body: CreateStrategyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> StrategyResponse:
    """Create a new strategy configuration."""
    strat_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO strategy_configs
                (id, user_id, strategy_name, mode, config_json, description)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, strategy_name, mode, config_json, description,
                      is_active, created_at, updated_at;
            """,
            [strat_id, str(current_user.id), body.strategy_name,
             body.mode, Json(body.config_json), body.description],
        )
        return _row_to_strategy(cur.fetchone())


@router.get("", response_model=list[StrategyResponse])
def list_strategies(
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[StrategyResponse]:
    """List all strategy configs for the authenticated user."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, strategy_name, mode, config_json, description,
                   is_active, created_at, updated_at
            FROM strategy_configs
            WHERE user_id = %s
            ORDER BY updated_at DESC;
            """,
            [str(current_user.id)],
        )
        return [_row_to_strategy(r) for r in cur.fetchall()]


@router.get("/{strategy_id}", response_model=StrategyResponse)
def get_strategy(
    strategy_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> StrategyResponse:
    return _row_to_strategy(_get_strategy_or_404(conn, strategy_id, str(current_user.id)))


@router.patch("/{strategy_id}", response_model=StrategyResponse)
def update_strategy(
    strategy_id: str,
    body: UpdateStrategyRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> StrategyResponse:
    """Update strategy parameters, mode, or active flag."""
    _get_strategy_or_404(conn, strategy_id, str(current_user.id))

    updates: dict[str, object] = {}
    if body.strategy_name is not None:
        updates["strategy_name"] = body.strategy_name
    if body.mode is not None:
        updates["mode"] = body.mode
    if body.config_json is not None:
        updates["config_json"] = Json(body.config_json)
    if body.description is not None:
        updates["description"] = body.description
    if body.is_active is not None:
        updates["is_active"] = body.is_active

    if updates:
        set_clause = ", ".join(f"{col} = %s" for col in updates)
        set_clause += ", updated_at = NOW()"
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE strategy_configs SET {set_clause} WHERE id = %s AND user_id = %s;",
                [*updates.values(), strategy_id, str(current_user.id)],
            )

    return _row_to_strategy(_get_strategy_or_404(conn, strategy_id, str(current_user.id)))


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_strategy(
    strategy_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> None:
    """Delete a strategy config. Associated backtest runs will have their FK nulled."""
    _get_strategy_or_404(conn, strategy_id, str(current_user.id))
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM strategy_configs WHERE id = %s AND user_id = %s;",
            [strategy_id, str(current_user.id)],
        )
