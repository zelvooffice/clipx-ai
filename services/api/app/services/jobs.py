"""Persistent, idempotent background jobs (docs/ARCHITECTURE.md §4.3).

Every unit of background work is a ``processing_jobs`` row:
* ``idempotency_key`` is unique -> the same work can never be enqueued twice.
* A worker *claims* a job with a lease (``locked_until``). If the worker dies, the lease expires and
  ``recover_stale_jobs`` re-queues it. Handlers must therefore be idempotent (they are: each step checks
  the current state of the clip/job before acting, and media is written to storage before the DB pointer).
* Celery is only the transport. Dispatch happens *after* the enclosing DB transaction commits.
"""

from __future__ import annotations

import logging
import socket
import traceback
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import event, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import session_scope, utcnow
from app.models import JobStatus, ProcessingJob

log = logging.getLogger("clipx.jobs")

Handler = Callable[[dict[str, Any], uuid.UUID], dict[str, Any] | None]
_HANDLERS: dict[str, Handler] = {}
_LEASE = {"default": 600, "render_clip": 1800, "transcribe_clip": 1800, "render_viral_edit": 1800}

WORKER_ID = f"{socket.gethostname()}:{uuid.uuid4().hex[:6]}"


class RetryLater(Exception):  # noqa: N818 - control-flow signal, not an error
    """Raise from a handler to re-run it later without counting as a failure (e.g. clip not ready)."""

    def __init__(self, delay_s: float, reason: str, *, max_polls: int = 30) -> None:
        super().__init__(reason)
        self.delay_s = delay_s
        self.reason = reason
        self.max_polls = max_polls


class PermanentFailure(Exception):
    """Raise when retrying cannot help (e.g. clips disabled on the channel)."""


def handler(kind: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        _HANDLERS[kind] = fn
        return fn

    return deco


# ----------------------------------------------------------------------------------------- dispatch
Dispatcher = Callable[[uuid.UUID, float], None]
_dispatcher: Dispatcher | None = None


def set_dispatcher(fn: Dispatcher | None) -> None:
    global _dispatcher
    _dispatcher = fn


def _dispatch(job_id: uuid.UUID, countdown: float = 0) -> None:
    if _dispatcher is not None:
        _dispatcher(job_id, countdown)
        return
    from app.workers.celery_app import run_job_task  # lazy: avoid import cycle

    run_job_task.apply_async(args=[str(job_id)], countdown=max(0.0, countdown))


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    pending = session.info.pop("clipx_pending_dispatch", [])
    for job_id, countdown in pending:
        try:
            _dispatch(job_id, countdown)
        except Exception:  # broker down: recover_stale_jobs will pick it up
            log.exception("dispatch failed for job %s (will be recovered)", job_id)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop("clipx_pending_dispatch", None)


def enqueue(
    db: Session,
    kind: str,
    idempotency_key: str,
    *,
    ref_id: str | None = None,
    payload: dict[str, Any] | None = None,
    delay_s: float = 0,
    max_attempts: int = 4,
) -> uuid.UUID:
    """Create the job if it does not exist yet; dispatch after commit. Returns the job id."""
    if kind not in _HANDLERS:
        import app.workers.handlers  # noqa: F401  (registers handlers)
    if kind not in _HANDLERS:
        raise ValueError(f"unknown job kind {kind}")
    run_after = utcnow() + timedelta(seconds=delay_s) if delay_s else None
    values = dict(id=uuid.uuid4(), kind=kind, ref_id=ref_id, idempotency_key=idempotency_key,
                  payload=payload or {}, max_attempts=max_attempts, status=JobStatus.queued.value,
                  run_after=run_after, attempts=0, created_at=utcnow(), updated_at=utcnow())
    stmt = pg_insert(ProcessingJob).values(**values).on_conflict_do_nothing(index_elements=["idempotency_key"])
    db.execute(stmt)
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key))
    assert job is not None
    if job.id == values["id"]:
        db.info.setdefault("clipx_pending_dispatch", []).append((job.id, delay_s))
    return job.id


def requeue(db: Session, job: ProcessingJob, delay_s: float = 0) -> None:
    job.status = JobStatus.queued
    job.run_after = utcnow() + timedelta(seconds=delay_s) if delay_s else None
    job.error = None
    db.info.setdefault("clipx_pending_dispatch", []).append((job.id, delay_s))


# ----------------------------------------------------------------------------------------- execution
def _claim(job_id: uuid.UUID) -> tuple[str, dict[str, Any]] | None:
    with session_scope() as db:
        now = utcnow()
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .where(ProcessingJob.status.in_([JobStatus.queued, JobStatus.failed]))
            .where(or_(ProcessingJob.run_after.is_(None), ProcessingJob.run_after <= now))
            .where(or_(ProcessingJob.locked_until.is_(None), ProcessingJob.locked_until < now))
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None
        job.status = JobStatus.running
        job.attempts += 1
        job.locked_by = WORKER_ID
        job.locked_until = now + timedelta(seconds=_LEASE.get(job.kind, _LEASE["default"]))
        job.started_at = job.started_at or now
        return job.kind, dict(job.payload or {})


def run_job(job_id: uuid.UUID) -> str:
    """Execute one job. Returns the resulting status (for logging/tests)."""
    if not _HANDLERS:
        import app.workers.handlers  # noqa: F401
    claimed = _claim(job_id)
    if claimed is None:
        return "skipped"
    kind, payload = claimed
    fn = _HANDLERS[kind]
    try:
        result = fn(payload, job_id)
    except RetryLater as rl:
        with session_scope() as db:
            job = db.get(ProcessingJob, job_id)
            assert job is not None
            polls = int((job.result or {}).get("polls", 0)) + 1
            job.attempts -= 1  # polling is not a failure
            job.locked_until = None
            job.locked_by = None
            if polls > rl.max_polls:
                job.status = JobStatus.dead
                job.error = f"gave up waiting: {rl.reason}"
                job.finished_at = utcnow()
                _on_dead(kind, payload, job.error)
                return "dead"
            job.result = {**(job.result or {}), "polls": polls, "waiting_for": rl.reason}
            requeue(db, job, rl.delay_s)
        return "waiting"
    except Exception as exc:
        permanent = isinstance(exc, PermanentFailure)
        with session_scope() as db:
            job = db.get(ProcessingJob, job_id)
            assert job is not None
            job.locked_until = None
            job.locked_by = None
            job.error = f"{type(exc).__name__}: {exc}"[:4000]
            log.warning("job %s (%s) failed attempt %s: %s", job_id, kind, job.attempts, job.error)
            if permanent or job.attempts >= job.max_attempts:
                job.status = JobStatus.dead
                job.finished_at = utcnow()
                if not permanent:
                    log.error("job %s dead:\n%s", job_id, traceback.format_exc())
                _on_dead(kind, payload, job.error)
                return "dead"
            backoff = min(600, 15 * (2 ** (job.attempts - 1)))
            job.status = JobStatus.failed
            job.run_after = utcnow() + timedelta(seconds=backoff)
            db.info.setdefault("clipx_pending_dispatch", []).append((job.id, backoff))
        return "failed"
    with session_scope() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None
        job.status = JobStatus.succeeded
        job.result = {**(job.result or {}), **(result or {})}
        job.locked_until = None
        job.locked_by = None
        job.error = None
        job.finished_at = utcnow()
    return "succeeded"


_DEAD_HOOKS: dict[str, Callable[[dict[str, Any], str], None]] = {}


def on_dead(kind: str) -> Callable[[Callable[[dict[str, Any], str], None]], Callable[[dict[str, Any], str], None]]:
    def deco(fn: Callable[[dict[str, Any], str], None]) -> Callable[[dict[str, Any], str], None]:
        _DEAD_HOOKS[kind] = fn
        return fn

    return deco


def _on_dead(kind: str, payload: dict[str, Any], error: str) -> None:
    hook = _DEAD_HOOKS.get(kind)
    if hook:
        try:
            hook(payload, error)
        except Exception:
            log.exception("dead hook for %s failed", kind)


def recover_stale_jobs() -> dict[str, int]:
    """Re-queue jobs whose worker died (lease expired) and re-dispatch due jobs the broker lost."""
    now = utcnow()
    recovered = redispatched = 0
    with session_scope() as db:
        stale = db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.status == JobStatus.running, ProcessingJob.locked_until < now)
            .with_for_update(skip_locked=True)
        ).all()
        for job in stale:
            job.locked_by = None
            job.locked_until = None
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.dead
                job.error = (job.error or "") + " | worker lost on final attempt"
            else:
                job.status = JobStatus.failed
                job.run_after = None
                db.info.setdefault("clipx_pending_dispatch", []).append((job.id, 0))
                recovered += 1
        overdue = db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.status.in_([JobStatus.queued, JobStatus.failed]))
            .where(or_(ProcessingJob.run_after.is_(None), ProcessingJob.run_after < now - timedelta(minutes=2)))
            .where(ProcessingJob.updated_at < now - timedelta(minutes=2))
            .limit(200)
        ).all()
        for job in overdue:
            job.updated_at = now
            db.info.setdefault("clipx_pending_dispatch", []).append((job.id, 0))
            redispatched += 1
    return {"recovered": recovered, "redispatched": redispatched}
