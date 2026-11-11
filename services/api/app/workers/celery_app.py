"""Celery transport for persistent jobs (see app/services/jobs.py) and periodic maintenance."""

from __future__ import annotations

import uuid

from celery import Celery

from app.config import get_settings

s = get_settings()
celery = Celery("clipx", broker=s.redis_url, backend=None)
celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={"clipx.run_job": {"queue": "default"}},
    broker_transport_options={"visibility_timeout": 3 * 3600},
    timezone="UTC",
    beat_schedule={
        "recover-stale-jobs": {"task": "clipx.recover", "schedule": 60.0},
        "sync-analytics": {"task": "clipx.periodic", "schedule": 3 * 3600.0, "args": ["sync_analytics"]},
        "discover-viral": {"task": "clipx.periodic", "schedule": 6 * 3600.0, "args": ["discover_viral"]},
        "cleanup-media": {"task": "clipx.periodic", "schedule": 24 * 3600.0, "args": ["cleanup_media"]},
        "refresh-sources": {"task": "clipx.periodic", "schedule": 15 * 60.0, "args": ["probe_sources"]},
    },
)


@celery.task(name="clipx.run_job")
def run_job_task(job_id: str) -> str:
    from app.services.jobs import run_job

    return run_job(uuid.UUID(job_id))


@celery.task(name="clipx.recover")
def recover_task() -> dict[str, int]:
    from app.services.jobs import recover_stale_jobs

    return recover_stale_jobs()


@celery.task(name="clipx.periodic")
def periodic_task(kind: str) -> str:
    """Periodic work is also a persistent job (bucketed idempotency key = one run per window)."""
    import time

    from app.db import session_scope
    from app.services import jobs

    bucket = int(time.time() // 900)
    with session_scope() as db:
        jobs.enqueue(db, kind, f"{kind}:{bucket}", max_attempts=2)
    return kind
