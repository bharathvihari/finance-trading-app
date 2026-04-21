"""
On-demand job management.

GET  /api/v1/jobs/{job_id}   — check status of an enqueued ARQ job
POST /api/v1/jobs/fetch-bars — trigger an on-demand IBKR bar fetch for a symbol
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.auth.dependencies import CurrentUser, get_current_user
from app.lib.arq_pool import get_arq_pool, get_job_status

router = APIRouter(prefix="/jobs", tags=["jobs"])


class FetchBarsJobRequest(BaseModel):
    symbol: str
    exchange: str
    asset_class: str = "equity"
    frequency: str = "daily"


class JobStatusResponse(BaseModel):
    job_id: str
    status: str      # queued | in_progress | complete | failed | not_found | unknown
    result: dict | None = None
    error: str | None = None


@router.post(
    "/fetch-bars",
    response_model=JobStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_fetch_bars(
    body: FetchBarsJobRequest,
    request: Request,
    _: CurrentUser = Depends(get_current_user),
) -> JobStatusResponse:
    """
    Enqueue an on-demand IBKR bar fetch for a symbol that has no local data.

    Returns 202 Accepted with a job_id immediately. The client should poll
    GET /jobs/{job_id} for completion, then re-fetch the bars.
    The WebSocket channel 'job:{job_id}' will also push a notification when done.
    """
    pool = await get_arq_pool(request)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job queue is unavailable. Check Redis connection.",
        )
    job = await pool.enqueue_job(
        "fetch_bars_task",
        symbol=body.symbol.upper(),
        exchange=body.exchange.upper(),
        asset_class=body.asset_class.lower(),
        frequency=body.frequency.lower(),
    )
    return JobStatusResponse(job_id=job.job_id, status="queued")


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: str,
    request: Request,
    _: CurrentUser = Depends(get_current_user),
) -> JobStatusResponse:
    """
    Poll the status of an enqueued job.

    status values:
      queued       — job is waiting in the queue
      in_progress  — worker is actively executing the job
      complete     — job finished successfully; result contains the output
      failed       — job raised an exception; error contains the message
      not_found    — job_id is unknown (expired or never existed)
    """
    pool = await get_arq_pool(request)
    info = await get_job_status(pool, job_id)
    return JobStatusResponse(**info)
