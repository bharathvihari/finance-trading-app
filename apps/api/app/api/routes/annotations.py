from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg2.extras import Json
from typing import Annotated

from app.api.schemas.annotations import (
    AnnotationResponse,
    CreateAnnotationRequest,
    UpdateAnnotationRequest,
)
from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db

router = APIRouter(prefix="/annotations", tags=["annotations"])


def _row_to_response(row: tuple) -> AnnotationResponse:
    return AnnotationResponse(
        id=row[0], symbol=row[1], exchange=row[2],
        annotation_type=row[3],
        timestamp_start=row[4], timestamp_end=row[5],
        price_start=row[6], price_end=row[7],
        label=row[8], color=row[9],
        data_json=row[10] or {},
        created_at=row[11], updated_at=row[12],
    )


@router.post("", response_model=AnnotationResponse, status_code=status.HTTP_201_CREATED)
def create_annotation(
    body: CreateAnnotationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> AnnotationResponse:
    """Save a chart annotation (trendline, note, etc.) for the authenticated user."""
    ann_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO annotations
                (id, user_id, symbol, exchange, annotation_type,
                 timestamp_start, timestamp_end, price_start, price_end,
                 label, color, data_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, symbol, exchange, annotation_type,
                      timestamp_start, timestamp_end, price_start, price_end,
                      label, color, data_json, created_at, updated_at;
            """,
            [
                ann_id, str(current_user.id),
                body.symbol.upper(), body.exchange.upper(), body.annotation_type,
                body.timestamp_start, body.timestamp_end,
                body.price_start, body.price_end,
                body.label, body.color, Json(body.data_json),
            ],
        )
        row = cur.fetchone()
    return _row_to_response(row)


@router.get("", response_model=list[AnnotationResponse])
def list_annotations(
    symbol: Annotated[str, Query(description="Filter by symbol")],
    exchange: Annotated[str, Query(description="Filter by exchange")],
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[AnnotationResponse]:
    """Return all annotations the user has saved for a symbol."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, exchange, annotation_type,
                   timestamp_start, timestamp_end, price_start, price_end,
                   label, color, data_json, created_at, updated_at
            FROM annotations
            WHERE user_id = %s AND symbol = %s AND exchange = %s
            ORDER BY timestamp_start ASC;
            """,
            [str(current_user.id), symbol.upper(), exchange.upper()],
        )
        rows = cur.fetchall()
    return [_row_to_response(r) for r in rows]


@router.patch("/{annotation_id}", response_model=AnnotationResponse)
def update_annotation(
    annotation_id: str,
    body: UpdateAnnotationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> AnnotationResponse:
    """Update a user annotation (e.g. move a trendline endpoint)."""
    updates: dict[str, object] = {}
    if body.label is not None:
        updates["label"] = body.label
    if body.color is not None:
        updates["color"] = body.color
    if body.timestamp_start is not None:
        updates["timestamp_start"] = body.timestamp_start
    if body.timestamp_end is not None:
        updates["timestamp_end"] = body.timestamp_end
    if body.price_start is not None:
        updates["price_start"] = body.price_start
    if body.price_end is not None:
        updates["price_end"] = body.price_end
    if body.data_json is not None:
        updates["data_json"] = Json(body.data_json)

    if updates:
        set_clause = ", ".join(f"{col} = %s" for col in updates)
        set_clause += ", updated_at = NOW()"
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE annotations SET {set_clause}
                WHERE id = %s AND user_id = %s;
                """,
                [*updates.values(), annotation_id, str(current_user.id)],
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, exchange, annotation_type,
                   timestamp_start, timestamp_end, price_start, price_end,
                   label, color, data_json, created_at, updated_at
            FROM annotations
            WHERE id = %s AND user_id = %s;
            """,
            [annotation_id, str(current_user.id)],
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Annotation not found.")
    return _row_to_response(row)


@router.delete("/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_annotation(
    annotation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> None:
    """Delete a user annotation."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM annotations WHERE id = %s AND user_id = %s;",
            [annotation_id, str(current_user.id)],
        )
