from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg2.extras import Json

from app.api.schemas.dashboards import (
    CreateDashboardRequest,
    CreateWidgetRequest,
    DashboardDetailResponse,
    DashboardSummaryResponse,
    UpdateDashboardRequest,
    UpdateWidgetRequest,
    WidgetResponse,
)
from app.auth.dependencies import CurrentUser, get_current_user
from app.db.connection import get_db

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_dashboard_or_404(conn, dashboard_id: str, user_id: str) -> dict:
    """Fetch a dashboard row, raising 404 if missing or owned by someone else."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, user_id, name, layout_json, created_at, updated_at
            FROM dashboard_layouts
            WHERE id = %s AND user_id = %s;
            """,
            [dashboard_id, user_id],
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found.")
    return {
        "id": row[0], "user_id": row[1], "name": row[2],
        "layout_json": row[3] or {}, "created_at": row[4], "updated_at": row[5],
    }


def _fetch_widgets(conn, dashboard_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, dashboard_layout_id, widget_type, title,
                   config_json, position_json, created_at, updated_at
            FROM widget_configs
            WHERE dashboard_layout_id = %s
            ORDER BY created_at ASC;
            """,
            [dashboard_id],
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0], "dashboard_id": r[1], "widget_type": r[2], "title": r[3],
            "config_json": r[4] or {}, "position_json": r[5] or {},
            "created_at": r[6], "updated_at": r[7],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Dashboard CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=DashboardSummaryResponse, status_code=status.HTTP_201_CREATED)
def create_dashboard(
    body: CreateDashboardRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> DashboardSummaryResponse:
    """Create a new empty dashboard for the authenticated user."""
    dashboard_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO dashboard_layouts (id, user_id, name, layout_json)
            VALUES (%s, %s, %s, %s)
            RETURNING id, name, created_at, updated_at;
            """,
            [dashboard_id, str(current_user.id), body.name, Json(body.layout_json)],
        )
        row = cur.fetchone()
    return DashboardSummaryResponse(
        id=row[0], name=row[1], widget_count=0, created_at=row[2], updated_at=row[3],
    )


@router.get("", response_model=list[DashboardSummaryResponse])
def list_dashboards(
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[DashboardSummaryResponse]:
    """List all dashboards belonging to the authenticated user."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.name, d.created_at, d.updated_at,
                   COUNT(w.id) AS widget_count
            FROM dashboard_layouts d
            LEFT JOIN widget_configs w ON w.dashboard_layout_id = d.id
            WHERE d.user_id = %s
            GROUP BY d.id, d.name, d.created_at, d.updated_at
            ORDER BY d.updated_at DESC;
            """,
            [str(current_user.id)],
        )
        rows = cur.fetchall()
    return [
        DashboardSummaryResponse(
            id=r[0], name=r[1], created_at=r[2], updated_at=r[3], widget_count=r[4],
        )
        for r in rows
    ]


@router.get("/{dashboard_id}", response_model=DashboardDetailResponse)
def get_dashboard(
    dashboard_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> DashboardDetailResponse:
    """Return a single dashboard with its full widget list."""
    dash = _get_dashboard_or_404(conn, dashboard_id, str(current_user.id))
    widgets = _fetch_widgets(conn, dashboard_id)
    return DashboardDetailResponse(
        id=dash["id"],
        name=dash["name"],
        layout_json=dash["layout_json"],
        created_at=dash["created_at"],
        updated_at=dash["updated_at"],
        widgets=[WidgetResponse(**w) for w in widgets],
    )


@router.patch("/{dashboard_id}", response_model=DashboardSummaryResponse)
def update_dashboard(
    dashboard_id: str,
    body: UpdateDashboardRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> DashboardSummaryResponse:
    """Rename a dashboard or update its global layout settings."""
    _get_dashboard_or_404(conn, dashboard_id, str(current_user.id))

    updates: dict[str, object] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.layout_json is not None:
        updates["layout_json"] = Json(body.layout_json)

    if updates:
        set_clause = ", ".join(f"{col} = %s" for col in updates)
        set_clause += ", updated_at = NOW()"
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE dashboard_layouts SET {set_clause} WHERE id = %s;",
                [*updates.values(), dashboard_id],
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.name, d.created_at, d.updated_at,
                   COUNT(w.id) AS widget_count
            FROM dashboard_layouts d
            LEFT JOIN widget_configs w ON w.dashboard_layout_id = d.id
            WHERE d.id = %s
            GROUP BY d.id, d.name, d.created_at, d.updated_at;
            """,
            [dashboard_id],
        )
        row = cur.fetchone()
    return DashboardSummaryResponse(
        id=row[0], name=row[1], created_at=row[2], updated_at=row[3], widget_count=row[4],
    )


@router.delete("/{dashboard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dashboard(
    dashboard_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> None:
    """Delete a dashboard and all its widgets (CASCADE)."""
    _get_dashboard_or_404(conn, dashboard_id, str(current_user.id))
    with conn.cursor() as cur:
        cur.execute("DELETE FROM dashboard_layouts WHERE id = %s;", [dashboard_id])


# ---------------------------------------------------------------------------
# Widget CRUD  (nested under /dashboards/{dashboard_id}/widgets)
# ---------------------------------------------------------------------------

@router.post(
    "/{dashboard_id}/widgets",
    response_model=WidgetResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_widget(
    dashboard_id: str,
    body: CreateWidgetRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> WidgetResponse:
    """Add a widget to a dashboard."""
    _get_dashboard_or_404(conn, dashboard_id, str(current_user.id))
    widget_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO widget_configs
                (id, dashboard_layout_id, user_id, widget_type, title, config_json, position_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id, dashboard_layout_id, widget_type, title,
                      config_json, position_json, created_at, updated_at;
            """,
            [
                widget_id, dashboard_id, str(current_user.id),
                body.widget_type, body.title,
                Json(body.config_json), Json(body.position_json),
            ],
        )
        row = cur.fetchone()
    return WidgetResponse(
        id=row[0], dashboard_id=row[1], widget_type=row[2], title=row[3],
        config_json=row[4] or {}, position_json=row[5] or {},
        created_at=row[6], updated_at=row[7],
    )


@router.get("/{dashboard_id}/widgets", response_model=list[WidgetResponse])
def list_widgets(
    dashboard_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> list[WidgetResponse]:
    """List all widgets on a dashboard."""
    _get_dashboard_or_404(conn, dashboard_id, str(current_user.id))
    widgets = _fetch_widgets(conn, dashboard_id)
    return [WidgetResponse(**w) for w in widgets]


@router.patch("/{dashboard_id}/widgets/{widget_id}", response_model=WidgetResponse)
def update_widget(
    dashboard_id: str,
    widget_id: str,
    body: UpdateWidgetRequest,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> WidgetResponse:
    """Update a widget's title, config, or position.

    Called by the frontend on every drag/resize event (position_json)
    and when the user saves widget settings (config_json).
    """
    _get_dashboard_or_404(conn, dashboard_id, str(current_user.id))

    updates: dict[str, object] = {}
    if body.title is not None:
        updates["title"] = body.title
    if body.config_json is not None:
        updates["config_json"] = Json(body.config_json)
    if body.position_json is not None:
        updates["position_json"] = Json(body.position_json)

    if updates:
        set_clause = ", ".join(f"{col} = %s" for col in updates)
        set_clause += ", updated_at = NOW()"
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE widget_configs SET {set_clause}
                WHERE id = %s AND dashboard_layout_id = %s;
                """,
                [*updates.values(), widget_id, dashboard_id],
            )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, dashboard_layout_id, widget_type, title,
                   config_json, position_json, created_at, updated_at
            FROM widget_configs
            WHERE id = %s AND dashboard_layout_id = %s;
            """,
            [widget_id, dashboard_id],
        )
        row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found.")

    return WidgetResponse(
        id=row[0], dashboard_id=row[1], widget_type=row[2], title=row[3],
        config_json=row[4] or {}, position_json=row[5] or {},
        created_at=row[6], updated_at=row[7],
    )


@router.delete("/{dashboard_id}/widgets/{widget_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_widget(
    dashboard_id: str,
    widget_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
) -> None:
    """Remove a widget from a dashboard."""
    _get_dashboard_or_404(conn, dashboard_id, str(current_user.id))
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM widget_configs WHERE id = %s AND dashboard_layout_id = %s;",
            [widget_id, dashboard_id],
        )
