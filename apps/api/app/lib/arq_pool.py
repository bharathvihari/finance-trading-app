"""
ARQ Redis pool — shared across the FastAPI app lifetime.

The pool is created once on startup via the lifespan context manager in main.py
and stored in app.state.arq_pool. Route handlers access it via the
get_arq_pool() dependency.

Enqueue a job from any route:
    pool = await get_arq_pool(request)
    job = await pool.enqueue_job("fetch_bars_task", symbol="AAPL", exchange="NASDAQ")
    return {"job_id": job.job_id, "status": "queued"}
"""
from __future__ import annotations

import logging

from fastapi import Request

logger = logging.getLogger(__name__)


async def create_arq_pool(host: str, port: int):
    """Create and return an ArqRedis connection pool. Returns None on failure."""
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
        pool = await create_pool(RedisSettings(host=host, port=port))
        logger.info("ARQ pool connected to Redis %s:%s", host, port)
        return pool
    except Exception as exc:
        logger.warning("ARQ pool unavailable — on-demand jobs disabled: %s", exc)
        return None


async def get_arq_pool(request: Request):
    """FastAPI dependency — returns the shared ArqRedis pool or None."""
    return getattr(request.app.state, "arq_pool", None)


async def get_job_status(pool, job_id: str) -> dict:
    """
    Query ARQ's Redis store for the status and result of a job.

    ARQ stores job results under arq:result:{job_id} as a msgpack blob.
    We use the ArqRedis.job() helper which handles deserialization.
    """
    if pool is None:
        return {"job_id": job_id, "status": "unknown", "error": "ARQ pool unavailable"}
    try:
        from arq.jobs import Job, JobStatus
        job = Job(job_id=job_id, redis=pool)
        status = await job.status()
        result = None
        if status == JobStatus.complete:
            result = await job.result(timeout=0)
        return {
            "job_id": job_id,
            "status": status.value,
            "result": result,
        }
    except Exception as exc:
        return {"job_id": job_id, "status": "error", "error": str(exc)}
