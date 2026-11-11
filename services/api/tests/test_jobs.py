from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.db import session_scope, utcnow
from app.models import JobStatus, ProcessingJob
from app.services import jobs

CALLS: list[str] = []


@jobs.handler("t_ok")
def _ok(payload: dict[str, Any], job_id: uuid.UUID) -> dict[str, Any]:
    CALLS.append("ok")
    return {"done": payload.get("n")}


@jobs.handler("t_flaky")
def _flaky(payload: dict[str, Any], job_id: uuid.UUID) -> None:
    CALLS.append("flaky")
    raise RuntimeError("boom")


@jobs.handler("t_perm")
def _perm(payload: dict[str, Any], job_id: uuid.UUID) -> None:
    raise jobs.PermanentFailure("clips disabled")


@jobs.handler("t_wait")
def _wait(payload: dict[str, Any], job_id: uuid.UUID) -> None:
    raise jobs.RetryLater(5, "not ready", max_polls=2)


DEAD: list[str] = []


@jobs.on_dead("t_flaky")
def _dead(payload: dict[str, Any], error: str) -> None:
    DEAD.append(error)


@pytest.fixture(autouse=True)
def _reset() -> None:
    CALLS.clear()
    DEAD.clear()


def _status(jid: uuid.UUID) -> ProcessingJob:
    with session_scope() as db:
        j = db.get(ProcessingJob, jid)
        assert j is not None
        db.expunge(j)
        return j


def test_enqueue_idempotent_and_dispatch_after_commit(drain) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        a = jobs.enqueue(db, "t_ok", "same-key", payload={"n": 1})
        b = jobs.enqueue(db, "t_ok", "same-key", payload={"n": 2})
        assert a == b
        assert drain.queue == []  # nothing dispatched before commit
    assert len(drain.queue) == 1
    assert drain.run() == ["t_ok:succeeded"]
    assert _status(a).result == {"done": 1}


def test_rollback_does_not_dispatch(drain) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(RuntimeError):
        with session_scope() as db:
            jobs.enqueue(db, "t_ok", "rolled-back")
            raise RuntimeError("abort")
    assert drain.queue == []


def test_retry_then_dead_with_hook(drain) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        jid = jobs.enqueue(db, "t_flaky", "flaky", max_attempts=3)
    out = drain.run()
    assert out == ["t_flaky:failed", "t_flaky:failed", "t_flaky:dead"]
    j = _status(jid)
    assert j.status == JobStatus.dead and j.attempts == 3 and "boom" in (j.error or "")
    assert DEAD and "boom" in DEAD[0]


def test_permanent_failure_not_retried(drain) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        jid = jobs.enqueue(db, "t_perm", "perm")
    assert drain.run() == ["t_perm:dead"]
    assert _status(jid).attempts == 1


def test_retry_later_polls_then_gives_up(drain) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        jid = jobs.enqueue(db, "t_wait", "wait")
    assert drain.run() == ["t_wait:waiting", "t_wait:waiting", "t_wait:dead"]
    j = _status(jid)
    assert j.attempts == 0 and "gave up" in (j.error or "")


def test_running_job_is_not_claimed_twice(drain) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        jid = jobs.enqueue(db, "t_ok", "lease")
    with session_scope() as db:
        j = db.get(ProcessingJob, jid)
        j.status, j.locked_until = JobStatus.running, utcnow() + timedelta(minutes=5)
    assert jobs.run_job(jid) == "skipped"
    assert CALLS == []


def test_recover_stale_lease(drain) -> None:  # type: ignore[no-untyped-def]
    with session_scope() as db:
        jid = jobs.enqueue(db, "t_ok", "crashed")
    drain.queue.clear()
    with session_scope() as db:  # simulate a worker that died mid-job
        j = db.get(ProcessingJob, jid)
        j.status, j.attempts, j.locked_until = JobStatus.running, 1, utcnow() - timedelta(seconds=1)
    stats = jobs.recover_stale_jobs()
    assert stats["recovered"] == 1
    assert drain.run() == ["t_ok:succeeded"]
    assert _status(jid).status == JobStatus.succeeded
